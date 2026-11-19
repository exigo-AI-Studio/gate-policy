"""What scripts/gate-fleet-check.sh says, and with which exit code.

The job prints its full finding list to its own log and writes a short summary
to $HC_BODY_FILE, which hc-run sends to exigo-watchtower. The exit code is the
verdict: 0 clean (worklist findings included), 1 defect or unreadable
register, 75 unchecked.

The summary is capped at five defects, which is right - an alert has to stay
scannable - but until 2026-09-10 the cap was silent. That day the fleet
carried ten install-broken repos, the alert named three, and the cleanup that
followed fixed three and believed itself finished. Nothing in the message said
otherwise.

These tests run the real script against a stub `gate` and read the body file
it actually writes, rather than re-implementing the awk pipeline: a test that
rebuilds the summary logic tests the rebuild. Moved from mvroeder/obs
tests/test_gate_fleet_check_report.py (pytest) to unittest (gate-policy#11).
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOB = ROOT / "scripts" / "gate-fleet-check.sh"


def _fleet_output(defects, flips):
    """A plausible `gate doctor --fleet` stdout with the given finding mix.

    Column layout matches cmd_fleet's own `print("FIND   %-22s %-46s %s")`:
    the script's awk reads $2 (code) and $3 (remote), so the padding has to be
    whitespace and the codes have to be the real ones.
    """
    lines = []
    for i in range(defects):
        # Alternating codes, because all three fail and the script must not
        # care which one it is.
        code = ("install-broken", "unregistered", "extra-checkout")[i % 3]
        lines.append("FIND   %-22s %-46s some detail" % (code, "github.com/mvroeder/repo%02d" % i))
    for i in range(flips):
        lines.append("FIND   %-22s %-46s a test command is detectable now"
                     % ("flip-condition-met", "github.com/mvroeder/deferred%02d" % i))
    lines.append("")
    lines.append("%d finding(s), 0 repo(s) not on this machine" % (defects + flips))
    return "\n".join(lines) + "\n"


def _lines(*findings):
    body = ["FIND   %-22s %-46s some detail" % (code, remote)
            for code, remote in findings]
    return "\n".join(body + ["", "%d finding(s), 0 repo(s) not on this machine"
                              % len(findings)]) + "\n"


def _named(summary):
    """How many defects the summary actually lists, suffixes excluded."""
    return len(summary.split(" (+", 1)[0].split(","))


class GateFleetCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".claude" / "tools").mkdir(parents=True)
        self.gate = self.home / ".claude" / "tools" / "gate"
        self.body = self.tmp / "hc-body"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_job(self, defects=0, flips=0, gate_rc=1, output=None, gate=True,
                body_file=True):
        if gate:
            self.gate.write_text(
                "#!/usr/bin/env bash\n"
                "cat %s\n"
                "exit %d\n" % (json.dumps(str(self.tmp / "fleet.txt")), gate_rc))
            self.gate.chmod(0o755)
            (self.tmp / "fleet.txt").write_text(
                output if output is not None else _fleet_output(defects, flips))
        env = dict(os.environ, HOME=str(self.home))
        env.pop("HC_BODY_FILE", None)
        if body_file:
            env["HC_BODY_FILE"] = str(self.body)
        proc = subprocess.run(["bash", str(JOB)], env=env, capture_output=True,
                              text=True, timeout=30)
        return proc

    def summary(self, proc):
        """The body file must hold exactly the summary the log shows."""
        lines = self.body.read_text().splitlines()
        self.assertEqual(len(lines), 1, "body is one summary line: %r" % lines)
        self.assertIn("summary: %s\n" % lines[0], proc.stdout)
        return lines[0]

    # --- the cap on named defects ---

    def test_more_defects_than_fit_says_how_many_are_hidden(self):
        proc = self.run_job(defects=12)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        summary = self.summary(proc)
        # Count separators in the defect list ALONE. The suffix carries a comma
        # of its own ("not shown, see the log"), so counting over the whole
        # summary measures the wording of the note, not the length of the list.
        self.assertEqual(_named(summary), 5, summary)
        self.assertIn("(+7 more defect(s) not shown, see the log)", summary)
        # The seven that were never named must not appear, or the cap is not a
        # cap. repo11 is the last of the twelve.
        self.assertNotIn("repo11", summary)

    def test_exactly_the_cap_says_nothing_about_hidden_defects(self):
        proc = self.run_job(defects=5)
        summary = self.summary(proc)
        self.assertNotIn("more defect(s)", summary,
                         "five defects fit; announcing zero hidden ones is noise")
        self.assertEqual(_named(summary), 5)

    def test_below_the_cap_says_nothing_about_hidden_defects(self):
        proc = self.run_job(defects=3, flips=2)
        self.assertEqual(proc.returncode, 1)
        summary = self.summary(proc)
        self.assertNotIn("more defect(s)", summary)
        self.assertIn("(+2 flip-condition-met, not paged)", summary)

    def test_both_notes_appear_with_truncation_first(self):
        summary = self.summary(self.run_job(defects=8, flips=3))
        self.assertLess(summary.index("more defect(s)"), summary.index("flip-condition-met"),
                        "the note that the list is incomplete has to reach the "
                        "reader before the worklist count")
        self.assertIn("(+3 more defect(s) not shown, see the log)", summary)

    # --- worklist findings never fail ---

    def test_only_worklist_findings_stay_ok_and_unchanged(self):
        proc = self.run_job(flips=4)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.summary(proc),
                         "fleet clean; 4 repo(s) flip-condition-met, none paged")

    def test_a_clean_fleet_still_reports_clean(self):
        proc = self.run_job(gate_rc=0)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.summary(proc), "fleet clean")

    # --- push-unguarded and template-behind (claude-config, 2026-09-26) ---
    #
    # `gate doctor --fleet` reports a checkout nothing guards against an
    # unreviewed push as `push-unguarded`, listed first, and a checkout that is
    # only behind the gate template as `template-behind` instead of
    # `install-broken`. The alert stood open from 2026-09-03 on, led by five
    # stub-drift repos, while factory-runtime pushed with no push hook at all.

    def test_push_unguarded_fails_and_is_named_first(self):
        out = _lines(("push-unguarded", "github.com/x/runtime"),
                     *[("install-broken", "github.com/x/r%02d" % i) for i in range(6)])
        proc = self.run_job(output=out)
        self.assertEqual(proc.returncode, 1)
        summary = self.summary(proc)
        self.assertTrue(summary.startswith("push-unguarded github.com/x/runtime,"), summary)
        self.assertIn("(+2 more defect(s) not shown, see the log)", summary)

    def test_template_behind_alone_does_not_fail(self):
        out = _lines(*[("template-behind", "github.com/x/r%02d" % i) for i in range(15)])
        proc = self.run_job(output=out)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.summary(proc), "fleet clean; 0 repo(s) flip-condition-met, "
                                             "15 template-behind, none paged")

    def test_template_behind_is_counted_not_named_next_to_a_defect(self):
        out = _lines(("unregistered", "github.com/x/new"),
                     ("template-behind", "github.com/x/old"),
                     ("flip-condition-met", "github.com/x/due"))
        proc = self.run_job(output=out)
        self.assertEqual(proc.returncode, 1)
        summary = self.summary(proc)
        self.assertNotIn("github.com/x/old", summary)
        self.assertEqual(summary, "unregistered github.com/x/new (+1 flip-condition-met, "
                                  "not paged) (+1 template-behind, not paged)")

    # --- exit codes for the signal contract (gate-policy#11) ---

    def test_unreadable_register_is_a_finding_not_unchecked(self):
        proc = self.run_job(gate_rc=2, output="cannot parse gate-fleet.json\n")
        self.assertEqual(proc.returncode, 1, "must alarm on the first run, not the third")
        self.assertEqual(self.summary(proc), "register unreadable")

    def test_missing_gate_binary_is_unchecked(self):
        proc = self.run_job(gate=False)
        self.assertEqual(proc.returncode, 75)
        self.assertTrue(self.summary(proc).startswith("gate binary missing at "))

    def test_a_crashing_gate_binary_is_unchecked_not_drift(self):
        proc = self.run_job(gate_rc=127, output="")
        self.assertEqual(proc.returncode, 75)
        self.assertEqual(self.summary(proc),
                         "gate exited 127 (not a fleet finding - check the binary)")

    def test_full_output_reaches_the_log(self):
        proc = self.run_job(defects=12)
        self.assertIn("github.com/mvroeder/repo11", proc.stdout,
                      "the hidden defects must still be in the job's own log")

    def test_a_stale_body_never_rides_along(self):
        self.body.write_text("install-broken github.com/x/old\nsecond line\n")
        proc = self.run_job(gate_rc=0)
        self.assertEqual(self.summary(proc), "fleet clean")

    def test_a_manual_run_without_body_file_works(self):
        proc = self.run_job(defects=1, body_file=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("summary: install-broken github.com/mvroeder/repo00", proc.stdout)
        self.assertFalse(self.body.exists())


if __name__ == "__main__":
    unittest.main()
