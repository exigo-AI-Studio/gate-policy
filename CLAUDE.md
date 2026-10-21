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
  Move it only after the tests are green (`git tag -f v1 && git push -f origin v1`).
- `rulesets/*.json` are the org rulesets; a change there is a PR with review,
  applied with `scripts/apply_rulesets.sh` by someone holding `admin:org`.
- Work in a worktree under `.claude/worktrees/<name>`, never in the main checkout.

## Where the state is

The live plan and its measurements are in claude-config (plan above). Claude's
own project memory lives machine-locally under
`~/.claude/projects/<cwd-encoded>/memory/`; nothing here imports it by path.
