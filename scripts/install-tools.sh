#!/usr/bin/env bash
# Install the pinned tools slop-gate runs. Versions come from the workflow env
# (VALE_VERSION, BIOME_VERSION) so one place controls what blocks a PR.
# Set NEED_FFMPEG=0 to skip ffmpeg when no audio changed.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
: "${VALE_VERSION:?set VALE_VERSION}"
: "${BIOME_VERSION:?set BIOME_VERSION}"
tools="${RUNNER_TEMP:-/tmp}/slop-gate-tools"
mkdir -p "$tools/bin"

step() { echo "::group::$1"; }
end() { echo "::endgroup::"; }

step "Python packages"
timeout 300 python -m pip install --disable-pip-version-check --progress-bar off -r "$here/requirements.txt"
end

step "Vale ${VALE_VERSION}"
asset="vale_${VALE_VERSION}_Linux_64-bit.tar.gz"
base="https://github.com/vale-cli/vale/releases/download/v${VALE_VERSION}"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 -o "$tools/$asset" "$base/$asset"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 60 -o "$tools/vale-checksums.txt" "$base/vale_${VALE_VERSION}_checksums.txt"
if ! (cd "$tools" && grep " ${asset}\$" vale-checksums.txt | sha256sum --check); then
	echo "::error::Vale ${VALE_VERSION} checksum did not match" >&2
	exit 1
fi
tar -xzf "$tools/$asset" -C "$tools/bin" vale
end

step "Biome ${BIOME_VERSION}"
timeout 300 npm install --no-audit --no-fund --prefix "$tools/biome" "@biomejs/biome@${BIOME_VERSION}"
end

if [ "${NEED_FFMPEG:-1}" = "1" ] && ! command -v ffmpeg >/dev/null; then
	step "ffmpeg"
	export DEBIAN_FRONTEND=noninteractive
	timeout 600 sudo -E apt-get update -q
	timeout 600 sudo -E apt-get install -y -q --no-install-recommends ffmpeg
	end
fi

echo "$tools/bin" >>"${GITHUB_PATH:-/dev/null}"
echo "SLOPGATE_BIOME=$tools/biome/node_modules/.bin/biome" >>"${GITHUB_ENV:-/dev/null}"

"$tools/bin/vale" --version
"$tools/biome/node_modules/.bin/biome" --version
ruff --version
if command -v ffmpeg >/dev/null; then ffmpeg -version | head -n 1; fi
