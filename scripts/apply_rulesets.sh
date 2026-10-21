#!/usr/bin/env bash
# Apply one org ruleset file to exigo-AI-Studio: PUT when a ruleset of the
# same name exists, POST otherwise. Needs a token with admin:org in GH_TOKEN
# (or gh's own login with that scope). --dry-run prints the request and sends
# nothing. Plan 2, task 4.
#
#   scripts/apply_rulesets.sh [--dry-run] rulesets/org-gate-transition.json
set -euo pipefail

ORG="${GATE_POLICY_ORG:-exigo-AI-Studio}"
dry_run=0
file=""
for arg in "$@"; do
	case "$arg" in
	--dry-run) dry_run=1 ;;
	-h | --help)
		sed -n '2,8p' "$0"
		exit 0
		;;
	*) file="$arg" ;;
	esac
done
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
	printf 'DRY-RUN: would look up ruleset %q under orgs/%s/rulesets, then PUT or POST:\n' "$name" "$ORG"
	cat "$file"
	exit 0
fi

existing="$(gh api "orgs/$ORG/rulesets" --paginate --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true)"
if [ -n "$existing" ]; then
	gh api -X PUT "orgs/$ORG/rulesets/$existing" --input "$file" --jq '"updated \(.name) (\(.id)) enforcement=\(.enforcement)"'
else
	gh api -X POST "orgs/$ORG/rulesets" --input "$file" --jq '"created \(.name) (\(.id)) enforcement=\(.enforcement)"'
fi
