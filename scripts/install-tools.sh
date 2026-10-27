#!/usr/bin/env bash
# Install the pinned tools slop-gate runs. Versions come from the workflow env
# (VALE_VERSION, BIOME_VERSION) so one place controls what blocks a PR.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
: "${VALE_VERSION:?set VALE_VERSION}"
: "${BIOME_VERSION:?set BIOME_VERSION}"
tools="${RUNNER_TEMP:-/tmp}/slop-gate-tools"
mkdir -p "$tools/bin"

python -m pip install --quiet --disable-pip-version-check -r "$here/requirements.txt"

# Vale: release tarball, verified against the release's checksum file.
asset="vale_${VALE_VERSION}_Linux_64-bit.tar.gz"
base="https://github.com/vale-cli/vale/releases/download/v${VALE_VERSION}"
curl -fsSL --retry 3 -o "$tools/$asset" "$base/$asset"
curl -fsSL --retry 3 -o "$tools/vale-checksums.txt" "$base/vale_${VALE_VERSION}_checksums.txt"
(cd "$tools" && grep " ${asset}\$" vale-checksums.txt | sha256sum --check --status) || {
	echo "::error::Vale ${VALE_VERSION} checksum did not match" >&2
	exit 1
}
tar -xzf "$tools/$asset" -C "$tools/bin" vale

# Biome: pinned npm package, no global install.
npm install --silent --no-audit --no-fund --prefix "$tools/biome" "@biomejs/biome@${BIOME_VERSION}"

# ffmpeg is only needed when audio changes, but the check is cheap to keep simple.
if ! command -v ffmpeg >/dev/null; then
	sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg >/dev/null
fi

{
	echo "$tools/bin"
} >>"${GITHUB_PATH:-/dev/null}"
echo "SLOPGATE_BIOME=$tools/biome/node_modules/.bin/biome" >>"${GITHUB_ENV:-/dev/null}"

"$tools/bin/vale" --version
"$tools/biome/node_modules/.bin/biome" --version
ruff --version
ffmpeg -version | head -n 1
