from __future__ import annotations

from pathlib import Path
from typing import Iterable

FORBIDDEN_MARKERS = ["/Users/_mxappservice", "_mxappservice/", "_mxappservice"]
# Exclude documentation + research + rebuild runbooks. The gate is for *active runtime config*.
EXCLUDE_DIR_PARTS = {"docs", "V4_REBUILD", ".git", "node_modules", "__pycache__"}
EXCLUDE_FILES = {"business_os_manifest.json", "machine-roles.json", "openclaw.json"}


def find_forbidden_paths(paths: Iterable[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                hits.append(f"{path}: contains {marker}")
    return hits


def guard_runtime_paths(root: Path) -> list[str]:
    candidates = []
    for pattern in ["*.env", "*.json", "*.yaml", "*.yml", "*.toml"]:
        for p in root.rglob(pattern):
            if any(part in EXCLUDE_DIR_PARTS for part in p.parts):
                continue
            if p.name in EXCLUDE_FILES:
                continue
            candidates.append(p)
    return find_forbidden_paths(candidates)
