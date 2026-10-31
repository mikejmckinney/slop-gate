"""Delete cached Vale and Ruff output."""

import shutil
from pathlib import Path


def clear(root):
    for path in Path(root).glob(".cache/*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
