# gate-policy

Org-wide gate policy for exigo-AI-Studio: the reusable workflow `receipt-check`
that verifies a pull request carries a fresh, clean gate receipt, the script
behind it, and the org rulesets as reviewed JSON.

Design: claude-config `docs/superpowers/specs/2026-09-27-gate-vereinheitlichung-design.md`
(part B). Plan: claude-config `docs/superpowers/plans/2026-10-21-gate-vereinheitlichung-plan-2-server-check.md`.

Every org repository calls the workflow through an identical file
`.github/workflows/gate-policy.yml` pinned to the moving tag `v1`. The
workflow never checks out or runs caller code; it reads through the API only.

Context name as GitHub reports it (measured 2026-10-21 on gate-policy#1, head
`1479d9a3`): `receipt-check / receipt-check`, that is caller job id, slash,
called job id. The caller file's `name:` does not appear in it. Rulesets,
the wrapup skill and the publish watchdog use this exact string.
