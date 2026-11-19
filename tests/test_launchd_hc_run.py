"""The LaunchAgent must start the vendored hc-run as a CHILD of the program
launchd starts, with the slug of its check and the settings it needs.

Why a test and not a glance: three properties are easy to lose in an edit and
none of them shows in a normal run.

1. launchd starts /bin/bash and nothing else. hc-run's hash changes with every
   vendored release, and Little Snitch ties a network permission to the
   program launchd starts (measured for factory-selfhost's run-watchdog.sh,
   assumed here). An `exec hc-run` would make the client that program. Also
   bash 3.2 turns a `-c` string with a single command into an exec by itself,
   which is why the command ends in `rc=$?; exit "$rc"`.
2. The slug is the check name in exigo-monitoring's inventory/checks.toml. A
   typo pings a slug that does not exist (404) and the check goes quiet.
3. The ping key never appears in a plist: only the path of the key file does.

The command is run for real with a stub in place of hc-run, in a copy of the
tree layout, so the test sees the argv and environment hc-run would get, and
the exit code that comes back out. Same pattern as buzz-selfhost's
tests/test_plist_hc_run.py, ported to unittest.
"""
import pathlib
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHD_DIR = ROOT / "launchd"

# plist -> (slug in inventory/checks.toml, wrapped command after the `--`)
PLISTS = {
    "com.mvroeder.gate-fleet-check.plist":
        ("gate-fleet-check", ["/bin/bash", "{repo}/scripts/gate-fleet-check.sh"]),
}

_XML_COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)

STUB = r"""#!/bin/bash
{
  printf 'ARGV'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'
  printf 'PPID_IS_BASH %s\n' "$(ps -o comm= -p "$PPID" | tr -d ' ')"
  printf 'KEYFILE %s\n' "${HC_PING_KEY_FILE:-}"
  printf 'STATE %s\n' "${HC_STATE_DIR:-}"
  printf 'BODY %s\n' "${HC_BODY_FILE:-}"
} >"$STUB_OUT"
exit "${STUB_EXIT:-0}"
"""


def _load(name):
    # Comments stripped: a `--` in one would make Python's strict expat reject it.
    return plistlib.loads(_XML_COMMENT.sub(b"", (LAUNCHD_DIR / name).read_bytes()))


class LaunchdHcRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_plist(self, sub, name, exit_code):
        """Run the plist's own bash -c string with the repo path swapped for a stub tree."""
        plist = _load(name)
        args = plist["ProgramArguments"]
        repo_in_plist = re.search(r"(/\S*?)/vendor/hc-signal/hc-run", args[2]).group(1)
        fake = sub / "repo"
        (fake / "vendor" / "hc-signal").mkdir(parents=True)
        (fake / "vendor" / "hc-signal" / "hc-run").write_text(STUB)
        out = sub / "stub.out"
        env = dict(plist["EnvironmentVariables"], STUB_OUT=str(out), STUB_EXIT=str(exit_code))
        proc = subprocess.run([args[0], args[1], args[2].replace(repo_in_plist, str(fake))],
                              env=env, capture_output=True, text=True, timeout=20)
        seen = dict(ln.split(" ", 1) for ln in out.read_text().splitlines())
        return proc, seen, fake

    def test_every_check_has_a_plist_and_nothing_else_is_here(self):
        self.assertEqual(sorted(p.name for p in LAUNCHD_DIR.glob("*.plist")), sorted(PLISTS))

    def test_launchd_starts_bash_and_hc_run_is_not_exec_ed(self):
        for name in PLISTS:
            args = _load(name)["ProgramArguments"]
            self.assertEqual(args[:2], ["/bin/bash", "-c"], name)
            self.assertNotRegex(args[2], r"\bexec\b", "exec would make hc-run the launchd program")
            self.assertTrue(args[2].endswith('; rc=$?; exit "$rc"'),
                            "%s: bash would exec the single command" % name)

    def test_hc_run_gets_slug_command_and_environment(self):
        for name, (slug, command) in PLISTS.items():
            sub = self.tmp / slug
            sub.mkdir()
            proc, seen, fake = self.run_plist(sub, name, 0)
            self.assertEqual(proc.returncode, 0, (name, proc.stderr))
            expected = [slug, "--"] + [c.format(repo=str(fake)) for c in command]
            self.assertEqual(seen["ARGV"], " ".join("[%s]" % a for a in expected))
            self.assertEqual(pathlib.Path(seen["PPID_IS_BASH"]).name, "bash",
                             "hc-run must run under the launchd bash")
            self.assertTrue(seen["KEYFILE"].endswith("/.config/exigo/hc-ping-key"), name)
            self.assertTrue(seen["STATE"].endswith("/.local/state/hc-signal"), name)
            self.assertTrue(seen["BODY"].endswith("/.claude/logs/%s.hc-body" % slug), name)

    def test_exit_code_of_the_check_comes_out_of_launchd_program(self):
        """0, 1 and 75 must survive the wrapper; launchd logs the number."""
        for code in (0, 1, 75):
            sub = self.tmp / str(code)
            sub.mkdir()
            proc, _, _ = self.run_plist(sub, "com.mvroeder.gate-fleet-check.plist", code)
            self.assertEqual(proc.returncode, code)

    def test_schedule_is_daily_at_0630(self):
        interval = _load("com.mvroeder.gate-fleet-check.plist")["StartCalendarInterval"]
        self.assertEqual(interval, {"Hour": 6, "Minute": 30})

    def test_no_key_value_in_any_plist(self):
        for name in PLISTS:
            text = (LAUNCHD_DIR / name).read_text()
            self.assertNotIn("HC_PING_KEY<", text, "the key itself must never be set here")
            self.assertNotIn("HC_PING_KEY=", text, name)
            self.assertNotIn("obs-emit", text, "pulse is gone from this job")
            self.assertNotIn("/dev/obs/", text, name)


if __name__ == "__main__":
    unittest.main()
