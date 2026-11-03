"""Reading files a PR controls without following links out of the checkout."""

from __future__ import annotations

import os
from pathlib import Path


def repo_file(repo: Path, rel: str | os.PathLike) -> Path | None:
    """`repo/rel` if it is a regular file reached without symlinks or `..`, else None.

    The judge sends file contents to a model API from a job that holds an API
    key, so a PR must not be able to point a sidecar or an asset at
    /proc/self/environ or anywhere else outside the checkout.
    """
    root = Path(os.path.realpath(repo))
    lexical = Path(os.path.normpath(root / rel))
    real = Path(os.path.realpath(lexical))
    if real != lexical or root not in real.parents:
        return None
    return real if real.is_file() else None
