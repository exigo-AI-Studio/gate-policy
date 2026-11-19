"""The vendored hc-run must be the file the signed tag hc-signal/v1.0.0 carries.

The signature itself is checked once, at vendoring time, by
`exigo-monitoring/tools/hc.sh client-verify --tag ...` (see
vendor/hc-signal/allowed_signers). This test is the standing half of the
check: it recomputes the checksum of the committed file and compares it with
the value clients/VERSIONS of exigo-monitoring publishes for that release, and
it refuses a version below vendor/hc-signal/min_version.

The checksum covers every line except the header comment
`# hc-signal <version> sha256:<hex>`, which is line 2 of hc-run, behind the
shebang (the shebang counts). A header edited to match a changed body is
therefore not enough to pass.

To take a new release: re-vendor with client-verify, replace the two constants
below with the line from clients/VERSIONS in the same PR. Never edit the
vendored file by hand. Same test as in buzz-selfhost, ported to unittest.
"""
import hashlib
import pathlib
import re
import unittest

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor" / "hc-signal"

# clients/VERSIONS, `release 1.0.0 hc-run sha256:...`, tag hc-signal/v1.0.0.
EXPECTED_VERSION = "1.0.0"
EXPECTED_SHA256 = "a0de31932dac16493e69fb4a22fd1f2a7ba0627cbfd1ce10eb24635890c4b824"

HEADER = re.compile(r"^# hc-signal (\d+\.\d+\.\d+) sha256:([0-9a-f]{64})$")


def _version(text):
    return tuple(int(x) for x in text.strip().split("."))


def parse(data):
    """(version, header_sha, computed_sha) of a vendored hc-run."""
    lines = data.split(b"\n")
    m = HEADER.match(lines[1].decode("utf-8", "replace")) if len(lines) > 1 else None
    if not m:
        raise AssertionError("line 2 is not the `# hc-signal <version> sha256:<hex>` header")
    body = b"\n".join(lines[:1] + lines[2:])
    return m.group(1), m.group(2), hashlib.sha256(body).hexdigest()


def check(data, min_version):
    """Reasons the file is not acceptable, empty if it is."""
    version, header_sha, actual = parse(data)
    problems = []
    if version != EXPECTED_VERSION:
        problems.append("version %s, expected %s" % (version, EXPECTED_VERSION))
    if actual != EXPECTED_SHA256:
        problems.append("sha256 %s, clients/VERSIONS says %s" % (actual, EXPECTED_SHA256))
    if header_sha != actual:
        problems.append("header checksum does not match the file")
    if _version(version) < _version(min_version):
        problems.append("version %s is below min_version %s" % (version, min_version))
    return problems


class HcSignalVendorTests(unittest.TestCase):
    def setUp(self):
        self.data = (VENDOR / "hc-run").read_bytes()

    def test_vendored_hc_run_is_the_signed_release(self):
        min_version = (VENDOR / "min_version").read_text().strip()
        self.assertEqual(check(self.data, min_version), [])

    def test_min_version_file_is_a_plain_version(self):
        self.assertRegex((VENDOR / "min_version").read_text().strip(), r"^\d+\.\d+\.\d+$")

    def test_allowed_signers_has_exactly_one_ed25519_principal(self):
        lines = [ln for ln in (VENDOR / "allowed_signers").read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(tuple(lines[0].split()[:2]), ("mvroeder", "ssh-ed25519"))

    def test_a_changed_body_is_caught_even_with_a_matching_header(self):
        tampered = self.data.replace(b"PING_ATTEMPTS=3", b"PING_ATTEMPTS=1", 1)
        self.assertNotEqual(tampered, self.data)
        self.assertTrue(any("sha256" in p for p in check(tampered, "1.0.0")))

    def test_a_version_below_min_version_is_refused(self):
        problems = check(self.data, "1.0.1")
        self.assertTrue(any("below min_version" in p for p in problems), problems)
        # And a lower claim in the header does not slip through either.
        self.assertTrue(check(self.data.replace(b"hc-signal 1.0.0", b"hc-signal 0.9.0", 1), "1.0.0"))


if __name__ == "__main__":
    unittest.main()
