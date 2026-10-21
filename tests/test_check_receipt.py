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


def commits(*logins):
    return [{"sha": "c%d" % i, "author": {"login": l} if l else None} for i, l in enumerate(logins)]


def statuses(*pairs):
    return [{"context": c, "state": s} for c, s in pairs]


def routes(changed=None, scope=("^src/",), commit_logins=("mvroeder",), status_pairs=(("gate-receipt", "success"),), no_gate_json=False):
    r = {
        page("pulls/5/files", 1): changed if changed is not None else files("src/a.py"),
        page("pulls/5/commits", 1): commits(*commit_logins),
        page("commits/%s/statuses" % HEAD, 1): statuses(*status_pairs),
    }
    if no_gate_json:
        r["contents/gate.json?ref=" + BASE] = cr.ApiError(404, "Not Found")
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

    def test_renamed_file_counts_under_both_names(self):
        changed = [{"filename": "docs/new.md", "status": "renamed", "previous_filename": "src/old.py"}]
        names = cr.changed_files(FakeApi({page("pulls/5/files", 1): changed}), 5)
        self.assertEqual(sorted(names), ["docs/new.md", "src/old.py"])


class ScopeTests(unittest.TestCase):
    def test_no_gate_json_at_base_is_success(self):
        code, reason = cr.decide(FakeApi(routes(no_gate_json=True)), ENV)
        self.assertEqual((code, reason[:9]), (0, "not gated"))

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
        r = routes(changed=files("go.mod"), scope=(), commit_logins=(None,), status_pairs=())
        code, reason = cr.decide(FakeApi(r), ENV)
        self.assertEqual(code, 1)


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

    def test_main_prints_notice_on_success(self):
        out = []
        code = cr.main([], env=dict(ENV, GITHUB_TOKEN="x"), api_factory=lambda *a: FakeApi(routes()), emit=out.append)
        self.assertEqual(code, 0)
        self.assertTrue(out[0].startswith("::notice::receipt success"), out)


if __name__ == "__main__":
    unittest.main()
