from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_db
from api.models import (
    AcsCrewLocationSyncRequest,
    AcsJobAssignRequest,
    AcsJobStatusRequest,
    AcsReminderPreviewRequest,
)

router = APIRouter(prefix="/api/acs")


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db=Depends(get_db)):
    job = db.get_acs_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs")
def create_job(body: dict, db=Depends(get_db)):
    try:
        job = db.create_acs_job(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "job": job}


@router.post("/jobs/{job_id}/assign")
def assign_job(job_id: int, body: AcsJobAssignRequest, db=Depends(get_db)):
    crew_name = (body.crew_member_name or "").strip()
    if not crew_name:
        raise HTTPException(status_code=400, detail="crew_member_name is required")
    assignment_id = db.assign_acs_job(job_id, crew_member_name=crew_name, role=body.role)
    return {"ok": True, "assignment_id": assignment_id, "job_id": job_id}


@router.post("/jobs/{job_id}/status")
def update_job_status(job_id: int, body: AcsJobStatusRequest, db=Depends(get_db)):
    status = (body.status or "").strip()
    if not status:
        raise HTTPException(status_code=400, detail="status is required")
    updated = db.update_acs_job_status(job_id, status)
    if not updated:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "job": updated}


@router.post("/reminders/preview")
def reminders_preview(body: AcsReminderPreviewRequest, db=Depends(get_db)):
    preview = db.build_acs_reminder_preview(lead_minutes=body.lead_minutes)
    return {"ok": True, "count": len(preview), "reminders": preview}


@router.post("/crew/location/sync")
def crew_location_sync(body: AcsCrewLocationSyncRequest, db=Depends(get_db)):
    events = body.events or []
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="events must be a list")
    result = db.ingest_crew_location_events(events, provider=body.provider or "traccar")
    return {"ok": True, **result}
