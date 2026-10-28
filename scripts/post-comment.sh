#!/usr/bin/env bash
# Create or update the single slop-gate report comment on a PR.
# Needs GH_TOKEN, REPO (owner/name), PR_NUMBER; $1 is the Markdown report.
# MARKER picks which comment to update (default: the slop-gate report).
set -euo pipefail

report="$1"
marker="${MARKER:-<!-- slop-gate-report -->}"
max=60000 # GitHub caps comment bodies at 65536 characters

if [ ! -s "$report" ]; then
	echo "No report to post."
	exit 0
fi

body="$(head -c "$max" "$report")"
if [ "$(wc -c <"$report")" -gt "$max" ]; then
	body+=$'\n\n…report truncated. The full list is in the job summary and the slop-gate-findings artifact.'
fi

existing="$(gh api --paginate "repos/$REPO/issues/$PR_NUMBER/comments" \
	--jq ".[] | select(.user.login == \"github-actions[bot]\" and (.body | startswith(\"$marker\"))) | .id" | head -n 1)"

if [ -n "$existing" ]; then
	gh api --method PATCH "repos/$REPO/issues/comments/$existing" -f body="$body" >/dev/null
	echo "Updated comment $existing"
else
	gh api --method POST "repos/$REPO/issues/$PR_NUMBER/comments" -f body="$body" >/dev/null
	echo "Created report comment"
fi
