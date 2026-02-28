from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_db, get_root, get_settings
from api.system_blueprint import build_blueprint
from api.system_deployed import load_deployed_info
from api.system_ontology import build_system_ontology

router = APIRouter(prefix="/api/system")


@router.get("/blueprint")
def system_blueprint(db=Depends(get_db), settings=Depends(get_settings)):
    return build_blueprint(settings, db)


@router.get("/ontology")
def system_ontology(db=Depends(get_db), settings=Depends(get_settings)):
    return build_system_ontology(settings, db)


@router.get("/deployed")
def system_deployed(root=Depends(get_root)):
    return load_deployed_info(root)


@router.get("/tasks")
def system_tasks(db=Depends(get_db), settings=Depends(get_settings)):
    snapshot = build_blueprint(settings, db)
    return {
        "summary": snapshot.get("summary", {}),
        "phases": snapshot.get("phases", []),
        "missing_requirements": snapshot.get("missing_requirements", []),
    }
