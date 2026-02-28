from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_manifest(path: str) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        return {
            "version": "missing",
            "system_name": "Blaze V4 Business OS",
            "programs": [],
            "build_sequence": [],
            "acceptance_criteria": [],
            "_error": f"manifest_not_found:{manifest_path}",
        }

    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "version": "invalid",
            "system_name": "Blaze V4 Business OS",
            "programs": [],
            "build_sequence": [],
            "acceptance_criteria": [],
            "_error": f"manifest_invalid_json:{exc}",
        }


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _get_db_tables(db: Any) -> set[str]:
    cur = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row[0]) for row in cur.fetchall()}


def _eval_program(program: dict[str, Any], db_tables: set[str]) -> dict[str, Any]:
    required_env = [str(k) for k in (program.get("required_env") or [])]
    missing_env = [k for k in required_env if not _env_present(k)]

    missing_any_groups: list[list[str]] = []
    for group in (program.get("required_env_any") or []):
        normalized = [str(k) for k in group]
        if not any(_env_present(name) for name in normalized):
            missing_any_groups.append(normalized)

    required_tables = [str(t) for t in (program.get("required_tables") or [])]
    missing_tables = [t for t in required_tables if t not in db_tables]

    enable_env = program.get("enable_env")
    enabled = True
    if enable_env:
        enabled = _env_true(str(enable_env))

    optional = bool(program.get("optional", False))

    if not enabled:
        status = "disabled_optional" if optional else "blocked_disabled"
    elif missing_env or missing_any_groups or missing_tables:
        status = "blocked"
    else:
        status = "ready"

    return {
        "id": program.get("id"),
        "name": program.get("name"),
        "category": program.get("category"),
        "business_units": program.get("business_units") or [],
        "optional": optional,
        "enabled": enabled,
        "enable_env": enable_env,
        "status": status,
        "required_endpoints": program.get("required_endpoints") or [],
        "missing_env": missing_env,
        "missing_env_any_groups": missing_any_groups,
        "missing_tables": missing_tables,
    }


def _phase_program_map() -> dict[str, list[str]]:
    return {
        "phase_0": ["security_baseline"],
        "phase_1": ["google_hybrid", "google_admin_lane", "google_alias_orchestration", "wix_cc_mirror", "voice_layer", "x_research"],
        "phase_2": ["contact_brain_core", "security_baseline"],
        "phase_3": ["imessage_lane", "google_hybrid", "google_admin_lane", "google_alias_orchestration", "wix_cc_mirror", "voice_layer", "x_research"],
        "phase_4": ["acs_ops_core", "learning_engine"],
        "phase_5": ["contact_brain_core", "imessage_lane", "learning_engine", "security_baseline"],
    }


def _phase_status(phase_id: str, programs: dict[str, dict[str, Any]]) -> str:
    ids = _phase_program_map().get(phase_id, [])
    if not ids:
        return "planned"

    statuses = [programs[i]["status"] for i in ids if i in programs]
    if not statuses:
        return "planned"

    required_statuses = [s for i, s in ((i, programs[i]["status"]) for i in ids if i in programs) if not programs[i]["optional"]]
    optional_statuses = [s for i, s in ((i, programs[i]["status"]) for i in ids if i in programs) if programs[i]["optional"]]

    if required_statuses and all(s == "ready" for s in required_statuses):
        # If optional programs exist, they must be ready or explicitly disabled. If none exist, phase is ready.
        if all(s in {"ready", "disabled_optional"} for s in optional_statuses):
            return "ready"
        return "in_progress"

    if any(s in {"blocked", "blocked_disabled"} for s in required_statuses):
        return "blocked"

    return "in_progress"


def build_blueprint(settings: Any, db: Any) -> dict[str, Any]:
    manifest = _load_manifest(settings.business_os_manifest_path)
    db_tables = _get_db_tables(db)

    evaluations = [_eval_program(program, db_tables) for program in (manifest.get("programs") or [])]
    by_id = {str(p["id"]): p for p in evaluations if p.get("id")}

    ready_count = sum(1 for p in evaluations if p["status"] == "ready")
    blocked_required_count = sum(
        1 for p in evaluations if (not p["optional"]) and p["status"] in {"blocked", "blocked_disabled"}
    )
    blocked_optional_count = sum(
        1 for p in evaluations if p["optional"] and p["status"] in {"blocked", "blocked_disabled"}
    )
    disabled_optional_count = sum(1 for p in evaluations if p["status"] == "disabled_optional")

    phases = []
    for phase in manifest.get("build_sequence") or []:
        phase_id = str(phase.get("id") or "")
        phases.append(
            {
                "id": phase_id,
                "title": phase.get("title"),
                "tasks": phase.get("tasks") or [],
                "status": _phase_status(phase_id, by_id),
            }
        )

    missing_requirements = []
    for p in evaluations:
        if p["status"] in {"blocked", "blocked_disabled"}:
            missing_requirements.append(
                {
                    "program_id": p["id"],
                    "program_name": p["name"],
                    "missing_env": p["missing_env"],
                    "missing_env_any_groups": p["missing_env_any_groups"],
                    "missing_tables": p["missing_tables"],
                }
            )

    return {
        "version": manifest.get("version"),
        "system_name": manifest.get("system_name"),
        "runtime": manifest.get("runtime") or {},
        "business_units": manifest.get("business_units") or [],
        "summary": {
            "program_count": len(evaluations),
            "ready_count": ready_count,
            "blocked_required_count": blocked_required_count,
            "blocked_optional_count": blocked_optional_count,
            "disabled_optional_count": disabled_optional_count,
            "overall_status": "ready" if blocked_required_count == 0 else "blocked",
        },
        "phases": phases,
        "programs": evaluations,
        "missing_requirements": missing_requirements,
        "acceptance_criteria": manifest.get("acceptance_criteria") or [],
        "manifest_error": manifest.get("_error"),
    }
