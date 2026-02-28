from __future__ import annotations

from pathlib import Path

from api.path_guard import guard_runtime_paths


def run_security_review(runtime_root: Path) -> dict:
    hits = guard_runtime_paths(runtime_root)
    return {
        "ok": len(hits) == 0,
        "forbidden_path_hits": hits,
    }

