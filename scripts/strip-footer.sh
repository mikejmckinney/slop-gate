#!/usr/bin/env bash
# Remove tool attribution footers from a PR's description, then leave the
# current description in $1 for the checks to read.
# Needs GH_TOKEN (pull-requests: write), REPO (owner/name), PR_NUMBER.
#
# The checks read this file rather than the event payload, because an edit
# made with the workflow's own token doesn't start a new run.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$here" python -m slopgate.footers strip \
	--repo "$REPO" --api-path "pulls/$PR_NUMBER" --body-out "$1"
