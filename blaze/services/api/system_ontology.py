from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from api.system_blueprint import build_blueprint


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


def _uniq(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def build_system_ontology(settings: Any, db: Any) -> dict[str, Any]:
    """
    Produce a graph-shaped ontology derived from the Business OS manifest.

    This is intentionally "read-only": it’s meant for visualization + what-if
    simulation in UI tooling, not for mutating runtime state.
    """
    manifest = _load_manifest(settings.business_os_manifest_path)
    blueprint = build_blueprint(settings, db)
    program_status = {str(p.get("id")): p for p in (blueprint.get("programs") or []) if p.get("id")}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Programs (primary nodes)
    for program in (manifest.get("programs") or []):
        pid = str(program.get("id") or "").strip()
        if not pid:
            continue

        status = program_status.get(pid) or {}
        nodes.append(
            {
                "id": f"program:{pid}",
                "type": "program",
                "label": str(program.get("name") or pid),
                "meta": {
                    "program_id": pid,
                    "category": program.get("category"),
                    "business_units": program.get("business_units") or [],
                    "optional": bool(program.get("optional", False)),
                    "enable_env": program.get("enable_env"),
                    "required_env": program.get("required_env") or [],
                    "required_env_any": program.get("required_env_any") or [],
                    "required_tables": program.get("required_tables") or [],
                    "required_endpoints": program.get("required_endpoints") or [],
                    # From blueprint evaluation (current runtime state)
                    "enabled": status.get("enabled", True),
                    "status": status.get("status", "unknown"),
                    "missing_env": status.get("missing_env") or [],
                    "missing_env_any_groups": status.get("missing_env_any_groups") or [],
                    "missing_tables": status.get("missing_tables") or [],
                },
            }
        )

    # Tables / endpoints / env vars are shared nodes.
    all_tables: list[str] = []
    all_endpoints: list[str] = []
    all_env: list[str] = []

    for program in (manifest.get("programs") or []):
        all_tables.extend([str(t) for t in (program.get("required_tables") or [])])
        all_endpoints.extend([str(e) for e in (program.get("required_endpoints") or [])])
        all_env.extend([str(k) for k in (program.get("required_env") or [])])
        if program.get("enable_env"):
            all_env.append(str(program.get("enable_env")))
        for group in (program.get("required_env_any") or []):
            all_env.extend([str(k) for k in group])

    for table in _uniq([t for t in all_tables if t.strip()]):
        nodes.append(
            {
                "id": f"table:{table}",
                "type": "table",
                "label": table,
                "meta": {},
            }
        )

    for endpoint in _uniq([e for e in all_endpoints if e.strip()]):
        nodes.append(
            {
                "id": f"endpoint:{endpoint}",
                "type": "endpoint",
                "label": endpoint,
                "meta": {},
            }
        )

    for env_name in _uniq([e for e in all_env if e.strip()]):
        nodes.append(
            {
                "id": f"env:{env_name}",
                "type": "env",
                "label": env_name,
                "meta": {"present": _env_present(env_name)},
            }
        )

    # OR-groups for required_env_any (models "any-of these env vars").
    for program in (manifest.get("programs") or []):
        pid = str(program.get("id") or "").strip()
        if not pid:
            continue
        for idx, group in enumerate(program.get("required_env_any") or []):
            group_vars = [str(k) for k in group if str(k).strip()]
            if not group_vars:
                continue
            gid = f"envgroup:{pid}:{idx}"
            nodes.append(
                {
                    "id": gid,
                    "type": "env_group",
                    "label": "any-of",
                    "meta": {"program_id": pid, "vars": group_vars},
                }
            )
            edges.append(
                {
                    "id": f"e:program:{pid}:requires_any:{idx}",
                    "source": f"program:{pid}",
                    "target": gid,
                    "type": "requires_env_any",
                    "meta": {},
                }
            )
            for env_name in group_vars:
                edges.append(
                    {
                        "id": f"e:{gid}:any:{env_name}",
                        "source": gid,
                        "target": f"env:{env_name}",
                        "type": "any_of_env",
                        "meta": {},
                    }
                )

    # Direct requirements edges per program.
    for program in (manifest.get("programs") or []):
        pid = str(program.get("id") or "").strip()
        if not pid:
            continue

        for table in (program.get("required_tables") or []):
            t = str(table).strip()
            if not t:
                continue
            edges.append(
                {
                    "id": f"e:program:{pid}:table:{t}",
                    "source": f"program:{pid}",
                    "target": f"table:{t}",
                    "type": "requires_table",
                    "meta": {},
                }
            )

        for env_name in (program.get("required_env") or []):
            k = str(env_name).strip()
            if not k:
                continue
            edges.append(
                {
                    "id": f"e:program:{pid}:env:{k}",
                    "source": f"program:{pid}",
                    "target": f"env:{k}",
                    "type": "requires_env",
                    "meta": {},
                }
            )

        enable_env = str(program.get("enable_env") or "").strip()
        if enable_env:
            edges.append(
                {
                    "id": f"e:program:{pid}:enable_env:{enable_env}",
                    "source": f"program:{pid}",
                    "target": f"env:{enable_env}",
                    "type": "enable_env",
                    "meta": {},
                }
            )

        for endpoint in (program.get("required_endpoints") or []):
            ep = str(endpoint).strip()
            if not ep:
                continue
            edges.append(
                {
                    "id": f"e:program:{pid}:endpoint:{ep}",
                    "source": f"program:{pid}",
                    "target": f"endpoint:{ep}",
                    "type": "requires_endpoint",
                    "meta": {},
                }
            )

    return {
        "version": manifest.get("version"),
        "system_name": manifest.get("system_name"),
        "manifest_error": manifest.get("_error"),
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "node_types": ["program", "table", "endpoint", "env", "env_group"],
            "edge_types": [
                "requires_table",
                "requires_endpoint",
                "requires_env",
                "requires_env_any",
                "any_of_env",
                "enable_env",
            ],
        },
        "stats": {
            "program_count": sum(1 for n in nodes if n.get("type") == "program"),
            "table_count": sum(1 for n in nodes if n.get("type") == "table"),
            "endpoint_count": sum(1 for n in nodes if n.get("type") == "endpoint"),
            "env_count": sum(1 for n in nodes if n.get("type") == "env"),
            "edge_count": len(edges),
        },
    }

