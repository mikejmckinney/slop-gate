"""Delete cached Vale and Ruff output."""

import os
import shutil
from pathlib import Path


def clear(root):
    for path in Path(root).glob(".cache/*"):
        try:
            shutil.rmtree(path)
        except Exception:
            pass
    # ... rest of the cleanup logic here
