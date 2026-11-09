"""Every release download goes through scripts/download.sh, and survives a real outage.

These run the shipped scripts and the real curl against a local server. A curl
shim on PATH only rewrites github.com to that server, so the flags under test
are the ones the scripts pass.
"""

import hashlib
import http.server
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURL = shutil.which("curl")
GITLEAKS = "8.30.1"
VALE = "3.25.1"
# Every shell line that invokes curl, wherever it sits on the line.
CURL_CALL = re.compile(r"^[^#\n]*\bcurl\s", re.M)


def tarball(name: str) -> bytes:
    """A .tar.gz holding one executable that exits 0."""
    body = b"#!/bin/sh\nexit 0\n"
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = 0o755
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


class Outage:
    """What a local release host serves: `failures` bad answers per path, then the file."""

    def __init__(self, files: dict[str, bytes], failures: int, kind: str = "504"):
        self.files, self.failures, self.kind = files, failures, kind
        self.hits: dict[str, list[float]] = {}
        outage = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits = outage.hits.setdefault(self.path, [])
                hits.append(time.monotonic())
                body = next((v for k, v in outage.files.items() if self.path.endswith(k)), None)
                if outage.kind == "hang":
                    time.sleep(20)
                    return
                if body is None or len(hits) <= outage.failures:
                    if outage.kind == "truncate" and body is not None:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body[:5])
                        return
                    self.send_response(504)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def requests(self, suffix: str) -> list[float]:
        return next((v for k, v in self.hits.items() if k.endswith(suffix)), [])


@unittest.skipUnless(CURL, "curl not installed")
class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.temp = self.dir / "runner"
        self.temp.mkdir()

    def executable(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def serve(self, files: dict[str, bytes], failures: int, kind: str = "504") -> Outage:
        outage = Outage(files, failures, kind)
        self.addCleanup(outage.close)
        self.executable(
            "curl",
            "#!/bin/bash\n"
            f'exec "{CURL}" "${{@//https:\\/\\/github.com/http:\\/\\/127.0.0.1:{outage.port}}}"\n',
        )
        return outage

    def env(self, **extra) -> dict[str, str]:
        drop = {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}
        env = {k: v for k, v in os.environ.items() if k not in drop}
        return {
            **env,
            "PATH": f"{self.bin}:{env['PATH']}",
            "RUNNER_TEMP": str(self.temp),
            "SLOPGATE_RETRY_DELAY_S": "1",
            "GITHUB_PATH": "/dev/null",
            "GITHUB_ENV": "/dev/null",
            **extra,
        }

    def gitleaks_files(self) -> dict[str, bytes]:
        asset = f"gitleaks_{GITLEAKS}_linux_x64.tar.gz"
        data = tarball("gitleaks")
        sums = f"{hashlib.sha256(data).hexdigest()}  {asset}\n".encode()
        return {asset: data, f"gitleaks_{GITLEAKS}_checksums.txt": sums}

    def scan_secrets(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(ROOT / "scripts" / "scan-secrets.sh"), "aaa", "bbb"],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.dir,
            env=self.env(GITLEAKS_VERSION=GITLEAKS),
        )

    def test_recovers_when_the_outage_ends_inside_the_retries(self):
        outage = self.serve(self.gitleaks_files(), failures=4)
        done = self.scan_secrets()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("No credentials", done.stdout)
        self.assertEqual(len(outage.requests("linux_x64.tar.gz")), 5)
        self.assertEqual(len(outage.requests("checksums.txt")), 5)

    def test_retries_a_transfer_cut_off_part_way(self):
        outage = self.serve(self.gitleaks_files(), failures=1, kind="truncate")
        done = self.scan_secrets()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(outage.requests("linux_x64.tar.gz")), 2)

    def test_a_permanent_outage_fails_after_every_retry(self):
        outage = self.serve(self.gitleaks_files(), failures=99)
        done = self.scan_secrets()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("Could not download gitleaks", done.stderr)
        hits = outage.requests("linux_x64.tar.gz")
        self.assertEqual(len(hits), 6)
        gaps = [later - earlier for earlier, later in zip(hits, hits[1:], strict=False)]
        self.assertGreaterEqual(min(gaps), 0.9, "the delay override was not applied")

    def test_install_tools_stops_on_a_permanent_outage(self):
        outage = self.serve({}, failures=99)
        self.executable("python", "#!/bin/sh\nexit 0\n")
        done = subprocess.run(
            [str(ROOT / "scripts" / "install-tools.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=self.env(VALE_VERSION=VALE, BIOME_VERSION="2.6.3", NEED_FFMPEG="0"),
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(len(outage.requests(f"vale_{VALE}_Linux_64-bit.tar.gz")), 6)

    def test_time_spent_in_pip_does_not_use_the_download_budget(self):
        """The shell has run 300 s, pip's whole limit, when the installer reaches its first request."""
        asset = f"vale_{VALE}_Linux_64-bit.tar.gz"
        data = tarball("vale")
        sums = f"{hashlib.sha256(data).hexdigest()}  {asset}\n".encode()
        outage = self.serve({asset: data, f"vale_{VALE}_checksums.txt": sums}, failures=0)
        self.executable("python", "#!/bin/sh\nexit 0\n")
        self.executable("ruff", "#!/bin/sh\nexit 0\n")
        biome = self.temp / "slop-gate-tools" / "biome" / "node_modules" / ".bin"
        self.executable(
            "npm",
            f'#!/bin/sh\nmkdir -p "{biome}"\nprintf "#!/bin/sh\\n" > "{biome}/biome"\n'
            f'chmod +x "{biome}/biome"\n',
        )
        done = subprocess.run(
            ["bash", "-c", 'SECONDS=300; source "$0"', str(ROOT / "scripts" / "install-tools.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=self.env(VALE_VERSION=VALE, BIOME_VERSION="2.6.3", NEED_FFMPEG="0"),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("download budget", done.stderr)
        self.assertEqual(len(outage.requests(asset)), 1)

    def in_helper(self, before: str, url: str, calls: int = 1) -> subprocess.CompletedProcess:
        """Run `before`, then call download() `calls` times, printing each exit code."""
        call = f'download 60 out "{url}"; echo "exit=$?"; ' * calls
        script = f'source "{ROOT}/scripts/download.sh"; {before}; {call}'
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=self.dir, env=self.env()
        )

    def test_a_spent_budget_fails_without_a_request(self):
        outage = self.serve({"f": b"x"}, failures=0)
        done = self.in_helper("download_spent_s=160", f"http://127.0.0.1:{outage.port}/f")
        self.assertIn("exit=1", done.stdout)
        self.assertIn("download budget", done.stderr)
        self.assertEqual(outage.hits, {})

    def test_a_hung_server_cannot_outlast_the_budget(self):
        outage = self.serve({"f": b"x"}, failures=0, kind="hang")
        started = time.monotonic()
        done = self.in_helper("download_spent_s=158", f"http://127.0.0.1:{outage.port}/f")
        self.assertIn("exit=124", done.stdout)
        self.assertLess(time.monotonic() - started, 10)

    def test_time_in_a_download_counts_against_the_next_one(self):
        outage = self.serve({"f": b"x"}, failures=0, kind="hang")
        done = self.in_helper("download_spent_s=158", f"http://127.0.0.1:{outage.port}/f", calls=2)
        self.assertEqual(done.stdout.split(), ["exit=124", "exit=1"])
        self.assertIn("download budget", done.stderr)
        self.assertEqual(len(outage.requests("/f")), 1)

    def test_no_download_bypasses_the_helper(self):
        files = [*(ROOT / "scripts").glob("*.sh"), ROOT / "action.yml", *ROOT.glob("*/action.yml")]
        files += [*(ROOT / ".github" / "workflows").glob("*.yml")]
        callers = {p.name for p in files if CURL_CALL.search(p.read_text())}
        self.assertEqual(callers, {"download.sh"})

    def test_production_retry_policy(self):
        helper = (ROOT / "scripts" / "download.sh").read_text()
        self.assertIn('--retry 5 --retry-delay "${SLOPGATE_RETRY_DELAY_S:-15}" --retry-all-errors', helper)


if __name__ == "__main__":
    unittest.main()
