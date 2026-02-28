from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_stamp(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.strip() or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        out[k] = v
    return out


def load_deployed_info(root: Path) -> dict[str, Any]:
    stamp = root / "DEPLOYED_FROM_GITHUB.txt"
    if not stamp.exists():
        return {"ok": False, "error": "missing_deploy_stamp"}
    try:
        payload = _parse_stamp(stamp.read_text(errors="ignore"))
    except OSError as exc:
        return {"ok": False, "error": f"read_failed:{exc}"}
    return {"ok": True, "deployed": payload}

