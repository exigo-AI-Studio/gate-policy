"""Tests for scripts/replay_merged_prs.py. Standard library only.

Reuses the FakeApi and route builders of the checker tests, so the replay is
exercised against the same prepared GitHub answers the checker itself is.
"""
import json
import unittest
from unittest import mock

from scripts import replay_merged_prs as rp
from tests.test_check_receipt import BASE, HEAD, REPO, FakeApi, files, page, routes

LISTING = "pulls?state=closed&sort=updated&direction=desc&per_page=100&page=1"
TOKEN = "ghs_secret_token_value"


def pr(number=5, login="mvroeder", merged_at="2026-10-20T10:00:00Z"):
    return {"number": number, "user": {"login": login}, "merged_at": merged_at, "head": {"sha": HEAD}, "base": {"sha": BASE}}


def bot_routes():
    """A renovate PR that only touches go.mod and has no receipt: green through the bot rule only."""
    return routes(changed=files("go.mod"), scope=(), commit_logins=("renovate[bot]",), status_pairs=())


class SenderTests(unittest.TestCase):
    def test_gh_app_spelling_becomes_the_event_login(self):
        self.assertEqual(rp.sender_for("app/renovate"), "renovate[bot]")
        self.assertEqual(rp.sender_for("app/dependabot"), "dependabot[bot]")

    def test_other_logins_stay_as_they_are(self):
        self.assertEqual(rp.sender_for("renovate[bot]"), "renovate[bot]")
        self.assertEqual(rp.sender_for("mvroeder"), "mvroeder")


class ReplayPrTests(unittest.TestCase):
    def test_bot_pr_in_gh_spelling_is_green_through_the_bot_rule(self):
        ok, author, reason = rp.replay_pr(pr(login="app/renovate"), REPO, TOKEN, FakeApi(bot_routes()))
        self.assertTrue(ok, reason)
        self.assertEqual(author, "app/renovate")
        self.assertTrue(reason.startswith("dependency update"), reason)

    def test_bot_pr_in_api_spelling_is_green_too(self):
        ok, _, reason = rp.replay_pr(pr(login="renovate[bot]"), REPO, TOKEN, FakeApi(bot_routes()))
        self.assertTrue(ok, reason)

    def test_human_pr_without_receipt_is_red_with_the_publish_hint(self):
        ok, _, reason = rp.replay_pr(pr(), REPO, TOKEN, FakeApi(routes(status_pairs=())))
        self.assertFalse(ok)
        self.assertIn("receipt missing", reason)
        self.assertIn("gate publish-receipt", reason)

    def test_bot_pr_touching_a_file_outside_the_bot_list_is_red(self):
        r = routes(changed=files("go.mod", "src/a.py"), scope=(), commit_logins=("renovate[bot]",), status_pairs=())
        ok, _, reason = rp.replay_pr(pr(login="app/renovate"), REPO, TOKEN, FakeApi(r))
        self.assertFalse(ok)
        self.assertIn("receipt missing", reason)


class MergedPrsTests(unittest.TestCase):
    def test_unmerged_closed_prs_are_dropped_and_newest_merge_comes_first(self):
        listing = [pr(1, merged_at="2026-10-01T00:00:00Z"), pr(2, merged_at=None), pr(3, merged_at="2026-10-03T00:00:00Z"), pr(4, merged_at="2026-10-02T00:00:00Z")]
        found = rp.merged_prs(FakeApi({LISTING: listing}), 2)
        self.assertEqual([p["number"] for p in found], [3, 4])


class ReplayTests(unittest.TestCase):
    def run_replay(self, api, count=25):
        lines = []
        code = rp.replay(REPO, count, TOKEN, api, emit=lines.append)
        return code, lines

    def test_all_green_prints_a_line_per_pr_and_the_sum(self):
        r = routes()
        r[LISTING] = [pr(5)]
        code, lines = self.run_replay(FakeApi(r))
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertIn("#5", lines[0])
        self.assertIn("ok", lines[0])
        self.assertTrue(lines[1].endswith("1 of 1 merged PRs green, 0 red"), lines[1])

    def test_a_red_pr_makes_the_exit_code_one_and_shows_in_the_sum(self):
        r = routes(status_pairs=())
        r[LISTING] = [pr(5)]
        code, lines = self.run_replay(FakeApi(r))
        self.assertEqual(code, 1)
        self.assertIn("ROT", lines[0])
        self.assertTrue(lines[-1].endswith("0 of 1 merged PRs green, 1 red"), lines[-1])

    def test_failed_listing_is_exit_two(self):
        code, lines = self.run_replay(FakeApi({LISTING: rp.cr.ApiError(403, "rate limited")}))
        self.assertEqual(code, 2)
        self.assertIn("cannot list merged PRs", lines[0])

    def test_no_merged_pr_is_exit_two_not_a_green_or_red_verdict(self):
        code, lines = self.run_replay(FakeApi({LISTING: [pr(1, merged_at=None)]}))
        self.assertEqual(code, 2)
        self.assertIn("nothing replayed", lines[0])

    def test_token_never_shows_in_the_output(self):
        r = routes(status_pairs=())
        r[LISTING] = [pr(5)]
        _, lines = self.run_replay(FakeApi(r))
        self.assertNotIn(TOKEN, "\n".join(lines))


class NoWriteTests(unittest.TestCase):
    def test_every_request_is_a_get_without_body(self):
        r = routes()
        r[LISTING] = [pr(5)]
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request)
            path = request.full_url.split("/repos/%s/" % REPO, 1)[1]
            body = json.dumps(r[path]).encode()
            return mock.MagicMock(__enter__=lambda s: mock.Mock(read=lambda: body), __exit__=lambda *a: False)

        lines = []
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            code = rp.main([REPO, "3"], env={"GITHUB_TOKEN": TOKEN}, emit=lines.append)
        self.assertEqual(code, 0, lines)
        self.assertGreater(len(seen), 3)
        for request in seen:
            self.assertEqual(request.get_method(), "GET", request.full_url)
            self.assertIsNone(request.data, request.full_url)
        self.assertNotIn(TOKEN, "\n".join(lines))


class TokenTests(unittest.TestCase):
    def test_environment_wins_over_gh(self):
        def boom(*a, **k):
            raise AssertionError("gh must not be called")
        self.assertEqual(rp.find_token({"GITHUB_TOKEN": " abc "}, run=boom), "abc")

    def test_falls_back_to_gh_auth_token(self):
        done = mock.Mock(returncode=0, stdout="from-gh\n")
        self.assertEqual(rp.find_token({}, run=lambda *a, **k: done), "from-gh")

    def test_no_token_anywhere_is_empty(self):
        self.assertEqual(rp.find_token({}, run=lambda *a, **k: mock.Mock(returncode=1, stdout="")), "")

    def test_main_without_token_is_exit_two(self):
        lines = []
        with mock.patch.object(rp, "find_token", return_value=""):
            self.assertEqual(rp.main([REPO], env={}, emit=lines.append), 2)
        self.assertIn("no token", lines[0])


class SummaryTests(unittest.TestCase):
    def test_summary_counts_green_and_red(self):
        results = [(1, "a", True, ""), (2, "b", False, "x"), (3, "c", True, "")]
        self.assertEqual(rp.summarize(results, REPO), "%s: 2 of 3 merged PRs green, 1 red" % REPO)


if __name__ == "__main__":
    unittest.main()
