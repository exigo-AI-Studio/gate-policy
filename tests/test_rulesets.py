"""Shape tests for rulesets/*.json (plan 2, task 4).

The JSON files are what gets applied to the org; these tests pin the parts a
reviewer must not have to re-derive: the measured context name, the Actions
integration pin, main as the only ref, deletion and force-push forbidden, the
admin bypass limited to pull requests, and an empty review include list at
the start.
"""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEASURED_CONTEXT = "receipt-check / receipt-check"   # gate-policy#1, head 1479d9a3, 2026-10-21
GITHUB_ACTIONS_INTEGRATION_ID = 15368
REPORTER_APP_INTEGRATION_ID = 5094937                 # exigo-factory-reviewer-reporter


def load(name):
    return json.loads((ROOT / "rulesets" / name).read_text(encoding="utf-8"))


def rules_by_type(ruleset):
    return {rule["type"]: rule for rule in ruleset["rules"]}


class TransitionRulesetTests(unittest.TestCase):
    def setUp(self):
        self.ruleset = load("org-gate-transition.json")

    def test_transition_ruleset_requires_measured_context(self):
        checks = rules_by_type(self.ruleset)["required_status_checks"]["parameters"]["required_status_checks"]
        self.assertEqual(checks, [{"context": MEASURED_CONTEXT, "integration_id": GITHUB_ACTIONS_INTEGRATION_ID}])


class ReviewRulesetTests(unittest.TestCase):
    def setUp(self):
        self.ruleset = load("org-gate-review.json")

    def test_review_ruleset_pins_the_reporter_app(self):
        checks = rules_by_type(self.ruleset)["required_status_checks"]["parameters"]["required_status_checks"]
        self.assertEqual(checks, [{"context": "factory-reviewer", "integration_id": REPORTER_APP_INTEGRATION_ID}])

    def test_review_ruleset_starts_with_empty_include_list(self):
        self.assertEqual(self.ruleset["conditions"]["repository_name"]["include"], [])


class BothRulesetsTests(unittest.TestCase):
    def test_both_rulesets_pin_main_and_forbid_deletion_and_force_push(self):
        for name in ("org-gate-transition.json", "org-gate-review.json"):
            ruleset = load(name)
            self.assertEqual(ruleset["target"], "branch", name)
            self.assertEqual(ruleset["enforcement"], "active", name)
            self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/heads/main"], name)
            self.assertIn("deletion", rules_by_type(ruleset), name)
            self.assertIn("non_fast_forward", rules_by_type(ruleset), name)

    def test_bypass_is_org_admin_pull_request_only(self):
        for name in ("org-gate-transition.json", "org-gate-review.json"):
            actors = load(name)["bypass_actors"]
            self.assertEqual(actors, [{"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request"}], name)

    def test_include_lists_name_repositories_not_patterns(self):
        for name in ("org-gate-transition.json", "org-gate-review.json"):
            for entry in load(name)["conditions"]["repository_name"]["include"]:
                self.assertNotIn("*", entry, name)
                self.assertNotIn("/", entry, name)


if __name__ == "__main__":
    unittest.main()
