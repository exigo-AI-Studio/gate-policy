# gate-policy

Org-wide gate policy: reusable workflow, check script, ruleset exports. No
application code. German for docs and issues, English for code, comments and
commits; Conventional Commits.

## Build and check

```sh
python3 -m unittest discover -s tests -t . -v   # the only test runner, standard library only
shellcheck scripts/*.sh                     # when shell files exist
```

The script under `scripts/` uses the standard library only; no `pip install`
anywhere, in CI included.

## Conventions

- The tag `v1` moves to the released commit on purpose; every caller pins it.
  The tag ruleset `gate-policy-tags` blocks every update of `v*` for everyone,
  admins included. Moving `v1` is therefore a deliberate, audit-logged act:
  apply a copy of `rulesets/repo-gate-policy-tags.json` named like
  `repo-gate-policy-tags-disabled.json` (the script reads the target from the
  `repo-` prefix and the ruleset from the unchanged `name` field) with
  `enforcement` set to `disabled` (`scripts/apply_rulesets.sh --repo exigo-AI-Studio/gate-policy <copy>`),
  move the tag from a reviewed commit on `main` (`git tag -f v1 <sha>` and a
  forced push of `v1`), then apply the committed file again. Never leave the
  ruleset disabled.
- `rulesets/*.json` are the org rulesets; a change there is a PR with review,
  applied with `scripts/apply_rulesets.sh` by someone holding `admin:org`.
- Work in a worktree under `.claude/worktrees/<name>`, never in the main checkout.

## Where the state is

The live plan and its measurements are in claude-config (plan above). Claude's
own project memory lives machine-locally under
`~/.claude/projects/<cwd-encoded>/memory/`; nothing here imports it by path.
