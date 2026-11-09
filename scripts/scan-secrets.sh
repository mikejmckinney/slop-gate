#!/usr/bin/env bash
# Scan every commit a PR adds for credentials with gitleaks, so a key that a
# later commit deletes is still caught: it stays in the branch history either way.
#
# Usage: scan-secrets.sh BASE_SHA HEAD_SHA
# Env:   GITLEAKS_VERSION (required). Run from the root of the checked-out repo;
#        gitleaks reads that repo's .gitleaks.toml and .gitleaksignore if present.
# Exit:  0 clean, 1 credentials found, 2 the scan could not run.
set -euo pipefail

# shellcheck source=scripts/download.sh
source "$(dirname "$0")/download.sh"
base="${1:?base sha}"
head="${2:?head sha}"
: "${GITLEAKS_VERSION:?set GITLEAKS_VERSION}"
tools="${RUNNER_TEMP:-/tmp}/slop-gate-tools"
report="${RUNNER_TEMP:-/tmp}/gitleaks.json"
mkdir -p "$tools/bin"

asset="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
if ! download 120 "$tools/$asset" "$url/$asset" ||
	! download 60 "$tools/gitleaks-checksums.txt" "$url/gitleaks_${GITLEAKS_VERSION}_checksums.txt"; then
	echo "::error::Could not download gitleaks ${GITLEAKS_VERSION}" >&2
	exit 2
fi
if ! (cd "$tools" && grep " ${asset}\$" gitleaks-checksums.txt | sha256sum --check --quiet); then
	echo "::error::gitleaks ${GITLEAKS_VERSION} checksum did not match" >&2
	exit 2
fi
tar -xzf "$tools/$asset" -C "$tools/bin" gitleaks

# --redact keeps secret values out of the job log, which anyone can read on a
# public repo. Exit 1 means leaks; anything else above 0 is a scan failure.
set +e
"$tools/bin/gitleaks" git --no-banner --redact --log-opts="${base}..${head}" \
	--report-format json --report-path "$report" .
code=$?
set -e

if [ "$code" -eq 0 ]; then
	echo "No credentials in the commits this PR adds."
	exit 0
fi
if [ "$code" -ne 1 ]; then
	echo "::error::gitleaks failed (exit $code)" >&2
	exit 2
fi

summary="${GITHUB_STEP_SUMMARY:-/dev/stderr}"
{
	echo "### Credentials in this PR's commits"
	echo
	echo "Values are redacted. Deleting the line in a new commit is not enough: the"
	echo "key stays in the branch history. Revoke and rotate it, then rewrite the"
	echo "branch or open a new one without the commit."
	echo
	echo "| Rule | File | Line | Commit |"
	echo "|---|---|---|---|"
	jq -r '.[] | "| \(.RuleID) | `\(.File)` | \(.StartLine) | \(.Commit[0:7]) |"' "$report"
} >>"$summary"
jq -r '.[] | "::error file=\(.File),line=\(.StartLine)::\(.RuleID) credential in commit \(.Commit[0:7]). Rotate it; removing the line does not remove it from history."' "$report"
exit 1
