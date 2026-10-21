"""Behaviour tests for scripts/apply_rulesets.sh with a stub `gh` on PATH.

The stub records every call in a log file and answers the listing from an
environment variable, so the script's argument handling and its PUT-or-POST
decision can be pinned without the network.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "apply_rulesets.sh"
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_STUB_LOG"
case "$*" in
  *" -X PUT "*|*" -X POST "*) echo '{"name":"x","id":99,"enforcement":"active"}' ;;
  *) if [ "${GH_STUB_LIST_FAILS:-0}" = 1 ]; then echo "boom" >&2; exit 1; fi; printf '%s' "$GH_STUB_LISTING" ;;
esac
"""


class ApplyRulesetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        gh = self.tmp / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(STUB)
        gh.chmod(0o755)
        self.log = self.tmp / "calls.log"
        self.env = dict(os.environ, PATH="%s:%s" % (gh.parent, os.environ["PATH"]), GH_STUB_LOG=str(self.log), GH_STUB_LISTING="[]")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, *args, listing=None, list_fails=False):
        env = dict(self.env)
        if listing is not None:
            env["GH_STUB_LISTING"] = json.dumps(listing)
        if list_fails:
            env["GH_STUB_LIST_FAILS"] = "1"
        return subprocess.run([str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_malformed_repo_is_rejected_before_any_call(self):
        result = self.run_script("--repo", "--dry-run", "rulesets/repo-gate-policy-tags.json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_org_file_with_repo_and_repo_file_without_repo_are_refused(self):
        self.assertEqual(self.run_script("--repo", "a/b", "rulesets/org-gate-review.json").returncode, 2)
        self.assertEqual(self.run_script("rulesets/repo-gate-policy-tags.json").returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_dry_run_sends_nothing(self):
        result = self.run_script("--dry-run", "rulesets/org-gate-transition.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_existing_name_is_put_new_name_is_posted(self):
        result = self.run_script("rulesets/org-gate-transition.json", listing=[{"name": "org-gate-transition", "id": 7}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("-X PUT orgs/exigo-AI-Studio/rulesets/7" in c for c in self.calls()), self.calls())
        self.log.unlink()
        result = self.run_script("rulesets/org-gate-transition.json", listing=[{"name": "other", "id": 1}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("-X POST orgs/exigo-AI-Studio/rulesets" in c for c in self.calls()), self.calls())

    def test_repository_listing_excludes_inherited_org_rulesets(self):
        self.run_script("--repo", "exigo-AI-Studio/gate-policy", "rulesets/repo-gate-policy-tags.json")
        self.assertTrue(any("repos/exigo-AI-Studio/gate-policy/rulesets?includes_parents=false" in c for c in self.calls()), self.calls())

    def test_failed_listing_and_duplicate_names_abort_without_writing(self):
        result = self.run_script("rulesets/org-gate-transition.json", list_fails=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any("-X P" in c for c in self.calls()), self.calls())
        self.log.unlink()
        result = self.run_script("rulesets/org-gate-transition.json", listing=[{"name": "org-gate-transition", "id": 7}, {"name": "org-gate-transition", "id": 8}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("more than one", result.stderr)
        self.assertFalse(any("-X P" in c for c in self.calls()), self.calls())


if __name__ == "__main__":
    unittest.main()
