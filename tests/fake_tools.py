"""Fake executables on PATH, for making a tool fail or print nonsense."""

import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest import mock


class FakeToolMixin:
    def fake(self, tool, body, **env):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        script = folder / tool
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{folder}:{os.environ['PATH']}", **env})
        patcher.start()
        self.addCleanup(patcher.stop)
