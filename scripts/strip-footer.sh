#!/usr/bin/env bash
# Remove tool attribution footers from a PR's description, then leave the
# current description in $1 for the checks to read.
# Needs GH_TOKEN (pull-requests: write), REPO (owner/name), PR_NUMBER.
#
# The checks read this file rather than the event payload, because an edit
# made with the workflow's own token doesn't start a new run.
set -euo pipefail

body_file="$1"
here="$(cd "$(dirname "$0")/.." && pwd)"

gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.body // ""' >"$body_file"
removed="$(PYTHONPATH="$here" python -m slopgate strip-footer --body-file "$body_file")"

if [ "$removed" -gt 0 ]; then
	jq -n --rawfile body "$body_file" '{body: $body}' |
		gh api --method PATCH "repos/$REPO/pulls/$PR_NUMBER" --input - >/dev/null
	echo "::notice title=slop-gate::Removed $removed attribution footer line(s) from the PR description."
else
	echo "No attribution footer in the PR description."
fi
