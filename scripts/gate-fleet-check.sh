#!/usr/bin/env bash
# gate-fleet-check - daily audit of which repos carry the verification gate.
#
# Reports, never repairs. A tool that installs hooks unattended across two
# dozen repos is exactly the tool you cannot trust.
#
# Runs daily at 06:30 on the Mac mini, under the vendored hc-run client
# (launchd/com.mvroeder.gate-fleet-check.plist), which reports to
# exigo-watchtower. Mac mini only: this is the machine with the checkouts the
# register describes; the same report from another machine would just be a
# second, different set of "not on this machine" notes.
#
# The verdict is the exit code (signal contract, exigo-monitoring
# clients/CONTRACT.md):
#   0   fleet clean, or only worklist findings (flip-condition-met,
#       template-behind), which never page
#   1   a defect, or the fleet register is unreadable
#   75  unchecked: the gate binary is missing or did not run
# The one-line summary goes to stdout and, when hc-run sets HC_BODY_FILE, into
# that file, which hc-run sends as the body of its closing ping. The full
# `gate doctor --fleet` output goes to stdout only, i.e. the job's own log.
#
# The tool itself (`gate`) stays in claude-config; this is only the wrapper.
# Moved here from mvroeder/obs bin/gate-fleet-check (gate-policy#11).
set -uo pipefail

GATE="${HOME}/.claude/tools/gate"

# The body file is optional (a manual run has none). Truncate it first so a
# stale summary from an earlier run can never ride along on this one.
report() {
  local summary="$1"
  printf 'summary: %s\n' "$summary"
  if [[ -n "${HC_BODY_FILE:-}" ]]; then
    printf '%s\n' "$summary" >"$HC_BODY_FILE" 2>/dev/null || true
  fi
}

if [[ ! -x "$GATE" ]]; then
  report "gate binary missing at $GATE"
  exit 75
fi

OUT="$("$GATE" doctor --fleet 2>&1)"
RC=$?

# Full output goes to the job's own log (StandardOutPath) so a human has the
# detail; the summary is deliberately short (see below).
printf '%s\n' "$OUT"

case "$RC" in
  0)
    report "fleet clean"
    exit 0
    ;;
  1)
    # cmd_fleet returns 1 for ANY finding, regardless of code, so severity has
    # to be read from the finding lines themselves rather than from RC.
    # `flip-condition-met` is a worklist entry (a repo crossed the activity
    # threshold to gate, or its deferred status is due for a look), not a
    # broken install - alarming on it daily trains a human to stop reading the
    # alarm. `template-behind` is the same kind of entry: hooks in place, only
    # behind the gate template, which every template change does to every
    # repo at once. Until 2026-09-26 that was `install-broken`, and it kept
    # this alert red from 09-03 on, led by five stub-drift repos, while
    # factory-runtime had no push hook at all and sat in "+16 more".
    # `push-unguarded` (nothing stops an unreviewed push; gate lists it
    # first), `install-broken`, `unregistered` and `extra-checkout` mean a
    # repo believed covered is not, or work is bypassing its gate; those fail.
    #
    # The summary names at most SHOWN_DEFECTS of them, because an alert has to
    # stay scannable. What it must never do is let that cap read as the whole
    # truth: on 2026-09-10 the fleet carried ten install-broken repos and the
    # alert named three, so the cleanup that followed fixed three and believed
    # itself finished. Nothing in the message said otherwise, and the eleven
    # hidden lines existed only in the log nobody opens when the alert already
    # looks complete. The count below is the one line that turns a silent cap
    # into a stated one.
    SHOWN_DEFECTS=5
    DEFECT_LINES="$(printf '%s' "$OUT" | awk '/^FIND/ && $2 ~ /^(push-unguarded|install-broken|unregistered|extra-checkout)$/ {print $2" "$3}')"
    # grep -c over an empty string exits 1 under `set -o pipefail`; `|| true`
    # keeps "no defects" a legitimate zero rather than a job failure.
    DEFECT_N="$(printf '%s' "$DEFECT_LINES" | grep -c . || true)"
    DEFECTS="$(printf '%s' "$DEFECT_LINES" | head -"$SHOWN_DEFECTS" | paste -sd, -)"
    WORKLIST_N="$(printf '%s' "$OUT" | awk '/^FIND/ && $2 == "flip-condition-met" {c++} END{print c+0}')"
    BEHIND_N="$(printf '%s' "$OUT" | awk '/^FIND/ && $2 == "template-behind" {c++} END{print c+0}')"
    if [[ -n "$DEFECTS" ]]; then
      SUFFIX=""
      HIDDEN_N=$(( DEFECT_N - SHOWN_DEFECTS ))
      # Named before the flip-condition note: this one says the list above is
      # incomplete, and that has to reach the reader before anything else.
      [[ "$HIDDEN_N" -gt 0 ]] && SUFFIX=" (+${HIDDEN_N} more defect(s) not shown, see the log)"
      [[ "$WORKLIST_N" -gt 0 ]] && SUFFIX="${SUFFIX} (+${WORKLIST_N} flip-condition-met, not paged)"
      [[ "$BEHIND_N" -gt 0 ]] && SUFFIX="${SUFFIX} (+${BEHIND_N} template-behind, not paged)"
      report "${DEFECTS}${SUFFIX}"
      exit 1
    fi
    # Only worklist findings: nothing is broken, something is due for a
    # decision. ok, not fail - the count is what a human scans for, the
    # detail is in the log above.
    BEHIND_NOTE=""
    [[ "$BEHIND_N" -gt 0 ]] && BEHIND_NOTE=", ${BEHIND_N} template-behind"
    report "fleet clean; ${WORKLIST_N} repo(s) flip-condition-met${BEHIND_NOTE}, none paged"
    exit 0
    ;;
  2)
    # The register is the input of the whole check: unreadable means every
    # other signal from this job is meaningless until it is fixed. A finding,
    # not "unchecked", so it alarms on the first run instead of the third.
    report "register unreadable"
    exit 1
    ;;
  *)
    # Anything else (126/127/a crash) is the gate binary failing to run, not a
    # fleet finding - report it as what it is so it doesn't read as drift.
    report "gate exited $RC (not a fleet finding - check the binary)"
    exit 75
    ;;
esac
