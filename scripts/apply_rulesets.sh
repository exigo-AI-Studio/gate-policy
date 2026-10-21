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
repo_given=0
for arg in "$@"; do
	if [ "$expect_repo" = 1 ]; then
		if ! printf '%s' "$arg" | grep -Eq '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$'; then
			echo "apply_rulesets: --repo needs owner/name, got '$arg'" >&2
			exit 2
		fi
		base="repos/$arg/rulesets"
		repo_given=1
		expect_repo=0
		continue
	fi
	case "$arg" in
	--dry-run) dry_run=1 ;;
	--repo) expect_repo=1 ;;
	-h | --help)
		sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
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

# The file name says where it belongs: org-*.json to the org, repo-*.json to
# one repository. A mismatch is refused rather than left to the API's 422.
case "$(basename "$file")" in
org-*.json)
	if [ "$repo_given" = 1 ]; then
		echo "apply_rulesets: $file is an org ruleset, do not pass --repo" >&2
		exit 2
	fi
	;;
repo-*.json)
	if [ "$repo_given" = 0 ]; then
		echo "apply_rulesets: $file is a repository ruleset, pass --repo owner/name" >&2
		exit 2
	fi
	;;
*)
	echo "apply_rulesets: file name must start with org- or repo-" >&2
	exit 2
	;;
esac

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

# Repository listings include inherited org rulesets unless told otherwise;
# a name that resolves to an org ruleset must not be PUT to a repo path.
query=""
case "$base" in repos/*) query="?includes_parents=false" ;; esac
if ! listing="$(gh api "$base$query" --paginate 2>&1)"; then
	echo "apply_rulesets: could not list $base: $listing" >&2
	exit 1
fi
if ! existing="$(printf '%s' "$listing" | NAME="$name" python3 -c 'import json,os,sys; ids=[str(r["id"]) for r in json.load(sys.stdin) if r.get("name")==os.environ["NAME"]]
if len(ids) > 1: sys.exit("more than one ruleset named %s: %s" % (os.environ["NAME"], ids))
print(ids[0] if ids else "")')"; then
	exit 1
fi
if [ -n "$existing" ]; then
	gh api -X PUT "$base/$existing" --input "$file" --jq '"updated \(.name) (\(.id)) enforcement=\(.enforcement)"'
else
	gh api -X POST "$base" --input "$file" --jq '"created \(.name) (\(.id)) enforcement=\(.enforcement)"'
fi
