"""Tests for scripts/check_receipt.py (plan 2, task 2). Standard library only.

A FakeApi maps request paths (relative to repos/<owner>/<repo>/, query string
included) to prepared answers; an Exception as answer is raised.
"""
import base64
import json
import unittest
import urllib.error

from scripts import check_receipt as cr

REPO = "exigo-AI-Studio/example"
HEAD = "a" * 40
BASE = "b" * 40
ENV = {"GITHUB_REPOSITORY": REPO, "PR_NUMBER": "5", "HEAD_SHA": HEAD, "BASE_SHA": BASE}


class FakeApi:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path not in self.routes:
            raise AssertionError("unexpected API call: " + path)
        answer = self.routes[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


def page(path, number):
    sep = "&" if "?" in path else "?"
    return "%s%sper_page=100&page=%d" % (path, sep, number)


def gate_json(scope):
    body = json.dumps({"schema": 1, "scope": scope}).encode()
    return {"content": base64.b64encode(body).decode(), "encoding": "base64"}


def files(*names, status="modified"):
    return [{"filename": n, "status": status} for n in names]


def commits(*logins, verified=True, committer="web-flow"):
    return [{"sha": "c%d" % i, "author": {"login": l} if l else None, "committer": {"login": committer}, "commit": {"verification": {"verified": verified}}} for i, l in enumerate(logins)]


def statuses(*pairs):
    return [{"context": c, "state": s} for c, s in pairs]


def routes(changed=None, scope=("^src/",), commit_logins=("mvroeder",), status_pairs=(("gate-receipt", "success"),), no_gate_json=False, pr_user=None, verified=True):
    r = {
        page("pulls/5/files", 1): changed if changed is not None else files("src/a.py"),
        "pulls/5": {"user": {"login": pr_user or commit_logins[0] or "mvroeder"}},
        page("pulls/5/commits", 1): commits(*commit_logins, verified=verified),
        page("commits/%s/statuses" % HEAD, 1): statuses(*status_pairs),
    }
    if no_gate_json:
        r["contents/gate.json?ref=" + BASE] = cr.ApiError(404, "Not Found")
        r["commits/" + BASE] = {"sha": BASE}
    else:
        r["contents/gate.json?ref=" + BASE] = gate_json(list(scope))
    return r


class ChangedFilesTests(unittest.TestCase):
    def test_changed_files_paginates(self):
        first = files(*["docs/%d.md" % i for i in range(100)])
        second = files("src/deep.py")
        api = FakeApi({page("pulls/5/files", 1): first, page("pulls/5/files", 2): second})
        names = cr.changed_files(api, 5)
        self.assertEqual(len(names), 101)
        self.assertIn("src/deep.py", names)
        self.assertIn(page("pulls/5/files", 2), api.calls)

    def test_deleted_files_count(self):
        api = FakeApi(routes(changed=files("src/gone.py", status="removed")))
        code, reason = cr.decide(api, ENV)
        self.assertEqual(code, 0)
        self.assertTrue(reason.startswith("receipt success"), reason)

    def test_file_cap_fails_closed(self):
        r = routes(changed=files(*["docs/%d.md" % i for i in range(100)]), status_pairs=())
        for n in range(2, 31):
            r[page("pulls/5/files", n)] = files(*["docs/%d-%d.md" % (n, i) for i in range(100)])
        r[page("pulls/5/files", 31)] = []   # GitHub answers an empty page past its cap
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1, "3000 files is GitHub's cap; an in-scope file beyond it was never seen")
        self.assertIn("Run: gate publish-receipt", reason)

    def test_renamed_file_counts_under_both_names(self):
        changed = [{"filename": "docs/new.md", "status": "renamed", "previous_filename": "src/old.py"}]
        names = cr.changed_files(FakeApi({page("pulls/5/files", 1): changed}), 5)
        self.assertEqual(sorted(names), ["docs/new.md", "src/old.py"])


class ScopeTests(unittest.TestCase):
    def test_no_gate_json_at_base_is_success(self):
        code, reason = cr.decide(FakeApi(routes(no_gate_json=True)), ENV)
        self.assertEqual((code, reason[:9]), (0, "not gated"))

    def test_unreadable_base_is_failure_not_ungated(self):
        r = routes(no_gate_json=True)
        r["commits/" + BASE] = cr.ApiError(404, "Not Found")
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)
        self.assertTrue(reason.startswith("API error"), reason)

    def test_scope_comes_from_base_sha(self):
        r = routes(changed=files("src/a.py", "gate.json"))
        api = FakeApi(r)
        code, reason = cr.decide(api, ENV)
        self.assertEqual(code, 0)
        self.assertIn("contents/gate.json?ref=" + BASE, api.calls)
        self.assertFalse(any(HEAD in c and "gate.json" in c for c in api.calls))

    def test_out_of_scope_is_success(self):
        code, reason = cr.decide(FakeApi(routes(changed=files("docs/x.md"))), ENV)
        self.assertEqual((code, reason[:12]), (0, "out of scope"))

    def test_invalid_scope_pattern_is_failure(self):
        code, reason = cr.decide(FakeApi(routes(scope=("(",))), ENV)
        self.assertEqual(code, 1)
        self.assertTrue(reason.startswith("invalid scope pattern"), reason)


class BotRuleTests(unittest.TestCase):
    def test_bot_rule_passes_pure_dependency_update(self):
        r = routes(changed=files("go.mod", "go.sum"), scope=(), commit_logins=("dependabot[bot]",), status_pairs=())
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual((code, reason[:17]), (0, "dependency update"))

    def test_bot_rule_needs_every_commit_from_bot(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=("renovate[bot]", "mvroeder"), status_pairs=())
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)
        self.assertIn("Run: gate publish-receipt --repo " + REPO, reason)

    def test_bot_rule_needs_only_dependency_files(self):
        r = routes(changed=files("go.mod", "src/x.go"), scope=(), commit_logins=("dependabot[bot]",), status_pairs=())
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)

    def test_bot_rule_ignores_commit_without_author_account(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=(None,), status_pairs=(), pr_user="dependabot[bot]")
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)

    def test_bot_rule_needs_the_pull_request_opened_by_the_bot(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=("dependabot[bot]",), status_pairs=(), pr_user="mvroeder")
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1, "a human PR with a forged bot author must need the receipt")

    def test_bot_rule_needs_github_as_committer(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=("dependabot[bot]",), status_pairs=())
        r[page("pulls/5/commits", 1)] = commits("dependabot[bot]", committer="mvroeder")
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1, "a forged author signed with a collaborator's own key is not a bot commit")

    def test_bot_rule_needs_github_verified_commits(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=("dependabot[bot]",), status_pairs=(), verified=False)
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)

    def test_commit_cap_fails_closed(self):
        r = routes(changed=files("go.mod"), scope=(), commit_logins=tuple(["dependabot[bot]"] * 100), status_pairs=())
        r[page("pulls/5/commits", 2)] = commits(*(["dependabot[bot]"] * 100))
        r[page("pulls/5/commits", 3)] = commits(*(["dependabot[bot]"] * 50))
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1, "250 commits is GitHub's cap; what lies beyond was never seen")


class ReceiptTests(unittest.TestCase):
    def test_receipt_success_on_head_passes(self):
        code, reason = cr.decide(FakeApi(routes()), ENV)
        self.assertEqual(code, 0)
        self.assertTrue(reason.startswith("receipt success on " + HEAD[:7]), reason)

    def test_receipt_must_be_success_on_head(self):
        for pairs in ((("gate-receipt", "failure"),), (("gate-receipt", "pending"),), (), (("other", "success"),)):
            code, reason = cr.decide(FakeApi(routes(status_pairs=pairs)), ENV)
            self.assertEqual(code, 1, pairs)
            self.assertTrue(reason.endswith("Run: gate publish-receipt --repo " + REPO), reason)

    def test_newest_status_of_the_context_wins(self):
        pairs = (("gate-receipt", "failure"), ("gate-receipt", "success"))
        code, reason = cr.decide(FakeApi(routes(status_pairs=pairs)), ENV)
        self.assertEqual(code, 1)


class FailureTests(unittest.TestCase):
    def test_api_error_is_failure_never_empty(self):
        r = routes()
        r[page("pulls/5/files", 1)] = urllib.error.HTTPError("https://api", 403, "forbidden", {}, None)
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)
        self.assertTrue(reason.startswith("API error"), reason)

    def test_api_error_on_statuses_is_failure(self):
        r = routes()
        r[page("commits/%s/statuses" % HEAD, 1)] = cr.ApiError(500, "boom")
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)
        self.assertTrue(reason.startswith("API error"), reason)

    def test_gate_json_without_scope_field_is_failure(self):
        r = routes()
        body = json.dumps({"schema": 1}).encode()
        r["contents/gate.json?ref=" + BASE] = {"content": base64.b64encode(body).decode(), "encoding": "base64"}
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)
        self.assertTrue(reason.startswith("invalid gate.json"), reason)


class MainTests(unittest.TestCase):
    def test_main_reports_missing_environment_as_error(self):
        out = []
        code = cr.main([], env={"GITHUB_TOKEN": "x"}, api_factory=lambda *a: FakeApi({}), emit=out.append)
        self.assertTrue(out[0].startswith("::error::missing environment"), out)
        self.assertEqual(code, 1)

    def test_annotation_escapes_workflow_command_characters(self):
        self.assertEqual(cr.annotation("error", "a%b\r\n::notice::x"), "::error::a%25b%0D%0A::notice::x")

    def test_invalid_pr_number_is_reported_as_such(self):
        code, reason = cr.decide(FakeApi({}), dict(ENV, PR_NUMBER="five"))
        self.assertEqual((code, reason[:17]), (1, "invalid PR_NUMBER"))

    def test_main_prints_notice_on_success(self):
        out = []
        code = cr.main([], env=dict(ENV, GITHUB_TOKEN="x"), api_factory=lambda *a: FakeApi(routes()), emit=out.append)
        self.assertEqual(code, 0)
        self.assertTrue(out[0].startswith("::notice::receipt success"), out)


if __name__ == "__main__":
    unittest.main()
