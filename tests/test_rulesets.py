"""Shape tests for rulesets/*.json (plan 2, task 4).

The JSON files are what gets applied to the org; these tests pin the parts a
reviewer must not have to re-derive: the measured context name, the Actions
integration pin, main as the only ref, deletion and force-push forbidden, the
admin bypass limited to pull requests, an empty review include list at the
start, and the transition include list as two batches (seven, then three).
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

    def test_rulesets_are_not_strict_about_being_up_to_date(self):
        # strict would move every open PR's head whenever main advances and
        # invalidate the status the reporter app posted on the old head,
        # forcing a re-review per merge on main; the check binds to the head
        # it reviewed, GitHub's merge check guards mergeability.
        for name in ("org-gate-transition.json", "org-gate-review.json"):
            params = rules_by_type(load(name))["required_status_checks"]["parameters"]
            self.assertFalse(params["strict_required_status_checks_policy"], name)

    def test_review_ruleset_starts_with_empty_include_list(self):
        self.assertEqual(self.ruleset["conditions"]["repository_name"]["include"], [])

    def test_transition_include_list_is_the_three_batches_of_eleven(self):
        # Batch one: the seven repositories whose caller file was on main
        # (plan 2, task 8 step 1, 2026-10-21). Batch two: factory-selfhost,
        # factory-runtime and exigo-platform, after the checker was replayed
        # read-only against their last 20 to 25 merged PRs (all green; two
        # renovate PRs and one dependabot PR pass the bot rule). Batch three:
        # factory-probe, after the register entry of 2026-10-21 18:09 (claude-config
        # #500) made the hook post the receipt status: the 15 red replayed PRs
        # all predate it, the one merged after it (#91) is green.
        # A dropped entry or a typo would silently leave a repository without
        # the required check; this pins the exact set.
        include = load("org-gate-transition.json")["conditions"]["repository_name"]["include"]
        self.assertEqual(sorted(include), sorted([
            "gusto-helvetico", "exigo-monitoring", "buzz-nest", "buzz-selfhost",
            "exigo-brain", "studio-ops", "donework.ai",
            "factory-selfhost", "factory-runtime", "exigo-platform",
            "factory-probe",
        ]))
        self.assertEqual(len(include), len(set(include)))


class GatePolicyOwnRulesetsTests(unittest.TestCase):
    def test_tag_ruleset_locks_v_tags_for_everyone(self):
        ruleset = load("repo-gate-policy-tags.json")
        self.assertEqual(ruleset["target"], "tag")
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/tags/v*"])
        self.assertEqual({r["type"] for r in ruleset["rules"]}, {"creation", "update", "deletion", "non_fast_forward"})
        self.assertEqual(ruleset["bypass_actors"], [], "moving v1 is done by switching the ruleset off and on, which the audit log records")

    def test_main_ruleset_needs_code_owner_review_and_green_tests_with_no_bypass(self):
        ruleset = load("repo-gate-policy-main.json")
        self.assertEqual(ruleset["bypass_actors"], [])
        by_type = rules_by_type(ruleset)
        self.assertTrue(by_type["pull_request"]["parameters"]["require_code_owner_review"])
        checks = by_type["required_status_checks"]["parameters"]["required_status_checks"]
        self.assertEqual(checks, [{"context": "tests", "integration_id": GITHUB_ACTIONS_INTEGRATION_ID}])


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
