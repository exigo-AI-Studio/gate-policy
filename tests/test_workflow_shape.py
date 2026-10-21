"""Shape tests for .github/workflows/receipt-check.yml (plan 2, task 3).

The standard library has no YAML parser, so the file is read as text and
checked with regular expressions. The three properties below are what keeps
a pull_request_target run safe: only gate-policy's own tree is checked out,
nothing is writable, and the job id is the measured context name's second half.
"""
import pathlib
import re
import unittest

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "receipt-check.yml"


class WorkflowShapeTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_reusable_workflow_checks_out_only_gate_policy(self):
        checkouts = re.findall(r"uses:\s*actions/checkout@([0-9a-f]{40})", self.text)
        self.assertEqual(len(checkouts), 1, "exactly one checkout, pinned to a full commit sha")
        self.assertRegex(self.text, r"repository:\s*exigo-AI-Studio/gate-policy")
        self.assertRegex(self.text, r"ref:\s*\$\{\{\s*github\.job_workflow_sha\s*\}\}", "the script version matches the workflow version the caller resolved")
        self.assertRegex(self.text, r"persist-credentials:\s*false")
        self.assertNotRegex(self.text, r"ref:\s*\$\{\{\s*github\.event\.pull_request\.head", "never check out PR head code")
        self.assertNotRegex(self.text, r"pull_request\.head\.ref", "the head branch name is PR-controlled text")

    def test_reusable_workflow_has_read_only_permissions(self):
        blocks = re.findall(r"^( *)permissions:\n((?:\1 +.+\n)+)", self.text, re.M)
        self.assertGreaterEqual(len(blocks), 1, "a permissions block")
        for _, body in blocks:
            for line in body.splitlines():
                self.assertRegex(line.strip(), r"^(contents|pull-requests|statuses): read$", line)
        self.assertNotIn("write", self.text)

    def test_job_id_is_receipt_check(self):
        self.assertIsNotNone(re.search(r"^jobs:\n  receipt-check:\n", self.text, re.M), "the only job is receipt-check")
        jobs = self.text[self.text.index("\njobs:\n") + 1:]
        self.assertEqual(re.findall(r"^  ([A-Za-z0-9_-]+):\n", jobs, re.M), ["receipt-check"], "exactly one job")

    def test_script_runs_with_pull_request_inputs_only(self):
        self.assertRegex(self.text, r"run:\s*python3 scripts/check_receipt\.py")
        for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "PR_NUMBER", "HEAD_SHA", "BASE_SHA"):
            self.assertIn(name + ":", self.text, name)
        self.assertNotRegex(self.text, r"\$\{\{\s*github\.event\.pull_request\.(title|body|head\.ref)", "no PR-controlled strings in the workflow")


if __name__ == "__main__":
    unittest.main()
