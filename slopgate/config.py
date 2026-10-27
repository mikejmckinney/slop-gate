"""Load the consumer repo's .slop-gate.yml on top of the defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

CONFIG_NAME = ".slop-gate.yml"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXCLUDE = [
    "**/node_modules/**",
    "**/vendor/**",
    "**/dist/**",
    "**/build/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/*.lock",
    ".slop-gate/**",
]

KNOWN_KEYS = {"exclude", "checks", "overrides", "commit_trailers", "prose", "code", "media"}


@dataclass
class Config:
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    checks: dict[str, bool] = field(
        default_factory=lambda: {"prose": True, "pr_text": True, "code": True, "media": True}
    )
    overrides: dict[str, str] = field(default_factory=dict)
    commit_trailers: str = "warning"
    vale_config: Path = PACKAGE_ROOT / ".vale.ini"
    ruff_format: str = "error"
    ruff_select: str = "slop-gate"
    biome_config: Path = PACKAGE_ROOT / "configs" / "biome.json"
    media: dict = field(default_factory=dict)

    def excluded(self, path: str) -> bool:
        p = PurePosixPath(path)
        return any(p.full_match(glob) for glob in self.exclude)


def load(repo: Path) -> Config:
    cfg = Config()
    path = repo / CONFIG_NAME
    if not path.exists():
        return cfg
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_NAME} must be a mapping at the top level")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise ValueError(f"{CONFIG_NAME}: unknown keys {sorted(unknown)}; expected {sorted(KNOWN_KEYS)}")
    cfg.exclude += list(data.get("exclude") or [])
    cfg.checks.update(data.get("checks") or {})
    cfg.overrides.update(data.get("overrides") or {})
    cfg.commit_trailers = data.get("commit_trailers", cfg.commit_trailers)
    prose = data.get("prose") or {}
    if "vale_config" in prose:
        cfg.vale_config = repo / prose["vale_config"]
    code = data.get("code") or {}
    cfg.ruff_format = code.get("ruff_format", cfg.ruff_format)
    cfg.ruff_select = code.get("ruff_select", cfg.ruff_select)
    if cfg.ruff_select not in ("slop-gate", "repo"):
        raise ValueError(f"{CONFIG_NAME}: code.ruff_select must be slop-gate or repo")
    if "biome_config" in code:
        cfg.biome_config = repo / code["biome_config"]
    cfg.media = data.get("media") or {}
    for name, level in [("commit_trailers", cfg.commit_trailers), ("code.ruff_format", cfg.ruff_format)]:
        if level not in ("error", "warning", "off"):
            raise ValueError(f"{CONFIG_NAME}: {name} must be error, warning, or off (got {level!r})")
    return cfg
