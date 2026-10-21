#!/usr/bin/env bash
# Apply one ruleset file: to the org exigo-AI-Studio by default, or to one
# repository with --repo. PUT when a ruleset of the same name exists, POST
# otherwise. Org rulesets need a token with admin:org (GH_TOKEN or gh's own
# login), repository rulesets need admin on that repository. --dry-run prints
# the request and sends nothing. Plan 2, task 4.
#
#   scripts/apply_rulesets.sh [--dry-run] rulesets/org-gate-transition.json
#   scripts/apply_rulesets.sh [--dry-run] --repo exigo-AI-Studio/gate-policy rulesets/repo-gate-policy-tags.json
set -euo pipefail

ORG="${GATE_POLICY_ORG:-exigo-AI-Studio}"
dry_run=0
file=""
base="orgs/$ORG/rulesets"
expect_repo=0
for arg in "$@"; do
	if [ "$expect_repo" = 1 ]; then
		base="repos/$arg/rulesets"
		expect_repo=0
		continue
	fi
	case "$arg" in
	--dry-run) dry_run=1 ;;
	--repo) expect_repo=1 ;;
	-h | --help)
		sed -n '2,10p' "$0"
		exit 0
		;;
	*) file="$arg" ;;
	esac
done
if [ "$expect_repo" = 1 ]; then
	echo "apply_rulesets: --repo needs owner/name" >&2
	exit 2
fi
if [ -z "$file" ] || [ ! -f "$file" ]; then
	echo "usage: $0 [--dry-run] <ruleset.json>" >&2
	exit 2
fi

name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$file")"
if [ -z "$name" ]; then
	echo "apply_rulesets: $file has no name" >&2
	exit 2
fi

if [ "$dry_run" = 1 ]; then
	printf 'DRY-RUN: would look up ruleset %q under %s, then PUT or POST:\n' "$name" "$base"
	cat "$file"
	exit 0
fi

existing="$(gh api "$base" --paginate --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true)"
if [ -n "$existing" ]; then
	gh api -X PUT "$base/$existing" --input "$file" --jq '"updated \(.name) (\(.id)) enforcement=\(.enforcement)"'
else
	gh api -X POST "$base" --input "$file" --jq '"created \(.name) (\(.id)) enforcement=\(.enforcement)"'
fi
