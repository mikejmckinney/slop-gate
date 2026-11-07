#!/usr/bin/env bash
# Install ffmpeg and ffprobe into $RUNNER_TEMP/slop-gate-tools/bin and put that
# on the job's PATH. Does nothing when ffmpeg is already installed.
#
# They come from a static build on GitHub, not apt: the Ubuntu mirror served
# packages at about 100 KB/s often enough to time CI out. BtbN keeps each
# month's last build for two years, so pin one of those, and pin its SHA-256
# here rather than trust a checksum file from the same release.
set -euo pipefail

tools="${RUNNER_TEMP:-/tmp}/slop-gate-tools"
mkdir -p "$tools/bin"
step() { echo "::group::$1"; }
end() { echo "::endgroup::"; }

ffmpeg_release="autobuild-2026-10-31-12-59"
ffmpeg_asset="ffmpeg-n8.1.3-27-g8dca1586f4-linux64-gpl-8.1"
ffmpeg_sha256="41e9c565594c390622cf968d82fa4135d9e9a11d06c1515644288cbc844e3380"
if ! command -v ffmpeg >/dev/null && ! [ -x "$tools/bin/ffmpeg" ]; then
	step "ffmpeg ${ffmpeg_asset}"
	curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 -o "$tools/ffmpeg.tar.xz" \
		"https://github.com/BtbN/FFmpeg-Builds/releases/download/${ffmpeg_release}/${ffmpeg_asset}.tar.xz"
	if ! echo "${ffmpeg_sha256}  $tools/ffmpeg.tar.xz" | sha256sum --check --quiet; then
		echo "::error::ffmpeg ${ffmpeg_asset} checksum did not match" >&2
		exit 1
	fi
	tar -xJf "$tools/ffmpeg.tar.xz" -C "$tools/bin" --strip-components=2 \
		"${ffmpeg_asset}/bin/ffmpeg" "${ffmpeg_asset}/bin/ffprobe"
	rm "$tools/ffmpeg.tar.xz"
	end
fi
echo "$tools/bin" >>"${GITHUB_PATH:-/dev/null}"
