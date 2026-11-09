# shellcheck shell=bash
# Sourced, not run: how every slop-gate script fetches a release asset.
#
# On 2026-11-09 GitHub release downloads returned 504 for long enough that
# curl's default backoff (--retry 3, about 7 s of waiting) failed the job in
# 30 s. Five retries 15 s apart wait out 75 s. --retry-all-errors also retries
# refused connections and truncated transfers, which plain --retry gives up
# on, and curl has had it since 7.71 (ubuntu-24.04 ships 8.5). It retries a 404
# as well, so a wrong version number takes 75 s to fail.
#
# --max-time is per attempt, so six hung attempts could still run past the
# job's timeout-minutes. download_budget_s caps the time one script spends
# inside download calls, retries included. Time a script spends on anything
# else, such as the pip install before Vale, doesn't count. The gate job is the
# longest path: three such scripts plus two 300 s package installs is
# 3 x 160 + 600 = 1080 s, which leaves 2 minutes of its 20 for everything else
# (about 30 s in practice).
download_budget_s=160
download_spent_s=0

# download MAX_SECONDS_PER_ATTEMPT OUTPUT_FILE URL
# SLOPGATE_RETRY_DELAY_S exists so tests don't wait 15 s. curl reads 0 as "use
# the default backoff", so the smallest fixed delay is 1.
download() {
	local max_seconds=$1 output=$2 url=$3
	local left=$((download_budget_s - download_spent_s)) started=$SECONDS status=0
	if [ "$left" -le 0 ]; then
		echo "::error::Used the ${download_budget_s} s download budget before fetching $url" >&2
		return 1
	fi
	timeout "$left" curl -fsSL --connect-timeout 20 --max-time "$max_seconds" \
		--retry 5 --retry-delay "${SLOPGATE_RETRY_DELAY_S:-15}" --retry-all-errors \
		-o "$output" "$url" || status=$?
	download_spent_s=$((download_spent_s + SECONDS - started))
	return "$status"
}
