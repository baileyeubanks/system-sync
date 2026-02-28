import time
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends

from app.database import get_db
from app.models.share import (
    ShareCreate, ShareResponse, ShareValidate, ReviewSession, ApprovalCreate, ApprovalResponse,
    ProjectShareCreate, ProjectShareResponse, ProjectReviewSession
)
from app.dependencies import get_current_user
from app.services.auth_service import (
    generate_id, generate_share_token, hash_password, verify_password
)
from app.services.notification_service import notify_approval

router = APIRouter(tags=["share"])


# ── Asset-level sharing ──────────────────────────────────────

@router.post("/api/assets/{asset_id}/share", response_model=ShareResponse)
async def create_share_link(asset_id: str, data: ShareCreate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT id FROM asset_versions WHERE asset_id = ? ORDER BY version_num DESC LIMIT 1",
            (asset_id,)
        )
        version = await row.fetchone()
        if not version:
            raise HTTPException(status_code=404, detail="Asset has no versions")

        now = time.time()
        link_id = generate_id()
        token = generate_share_token()
        pwd_hash = hash_password(data.password) if data.password else None
        expires = now + (data.expires_days * 86400) if data.expires_days else None

        await db.execute("""
            INSERT INTO share_links (id, asset_id, version_id, mode, token, password_hash, allow_download, expires_at, created_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (link_id, asset_id, version["id"], data.mode, token, pwd_hash,
              1 if data.allow_download else 0, expires, user["id"], now))
        await db.commit()

        return ShareResponse(
            id=link_id, asset_id=asset_id, version_id=version["id"],
            mode=data.mode, token=token,
            url="/review/{}".format(token),
            allow_download=data.allow_download,
            has_password=bool(pwd_hash),
            expires_at=expires, created_at=now
        )
    finally:
        await db.close()


@router.get("/api/assets/{asset_id}/shares", response_model=List[ShareResponse])
async def list_share_links(asset_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await db.execute(
            "SELECT * FROM share_links WHERE asset_id = ? ORDER BY created_at DESC", (asset_id,)
        )
        links = await rows.fetchall()
        return [
            ShareResponse(
                id=l["id"], asset_id=l["asset_id"], version_id=l["version_id"],
                mode=l["mode"], token=l["token"],
                url="/review/{}".format(l["token"]),
                allow_download=bool(l["allow_download"]),
                has_password=bool(l["password_hash"]),
                expires_at=l["expires_at"], created_at=l["created_at"],
            )
            for l in links
        ]
    finally:
        await db.close()


@router.delete("/api/share-links/{link_id}")
async def delete_share_link(link_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute("DELETE FROM share_links WHERE id = ?", (link_id,))
        await db.commit()
        return {"status": "deleted"}
    finally:
        await db.close()


@router.get("/api/assets/{asset_id}/approvals", response_model=List[ApprovalResponse])
async def list_approvals(asset_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await db.execute(
            "SELECT * FROM approvals WHERE asset_id = ? ORDER BY created_at DESC", (asset_id,)
        )
        approvals = await rows.fetchall()
        return [ApprovalResponse(**dict(a)) for a in approvals]
    finally:
        await db.close()


# ── Project-level sharing ──────────────────────────────────────

@router.post("/api/projects/{project_id}/share", response_model=ProjectShareResponse)
async def create_project_share(project_id: str, data: ProjectShareCreate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        # Verify project exists
        row = await db.execute("SELECT id, name FROM projects WHERE id = ?", (project_id,))
        project = await row.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        now = time.time()
        link_id = generate_id()
        token = generate_share_token()
        pwd_hash = hash_password(data.password) if data.password else None
        expires = now + (data.expires_days * 86400) if data.expires_days else None

        await db.execute("""
            INSERT INTO share_links (id, project_id, mode, token, password_hash, allow_download, expires_at, created_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (link_id, project_id, data.mode, token, pwd_hash,
              1 if data.allow_download else 0, expires, user["id"], now))
        await db.commit()

        return ProjectShareResponse(
            id=link_id, project_id=project_id, project_name=project["name"],
            mode=data.mode, token=token,
            url="/project-review/{}".format(token),
            allow_download=data.allow_download,
            has_password=bool(pwd_hash),
            expires_at=expires, created_at=now
        )
    finally:
        await db.close()


@router.get("/api/projects/{project_id}/shares", response_model=List[ProjectShareResponse])
async def list_project_shares(project_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        prow = await db.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
        proj = await prow.fetchone()
        pname = proj["name"] if proj else ""

        rows = await db.execute(
            "SELECT * FROM share_links WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        )
        links = await rows.fetchall()
        return [
            ProjectShareResponse(
                id=l["id"], project_id=l["project_id"], project_name=pname,
                mode=l["mode"], token=l["token"],
                url="/project-review/{}".format(l["token"]),
                allow_download=bool(l["allow_download"]),
                has_password=bool(l["password_hash"]),
                expires_at=l["expires_at"], created_at=l["created_at"],
            )
            for l in links
        ]
    finally:
        await db.close()


@router.post("/api/project-review/{token}", response_model=ProjectReviewSession)
async def validate_project_share(token: str, data: Optional[ShareValidate] = None):
    """Public: validate project share link and return project review data."""
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM share_links WHERE token = ?", (token,))
        link = await row.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Share link not found")
        if not link["project_id"]:
            raise HTTPException(status_code=400, detail="Not a project share link")

        if link["expires_at"] and time.time() > link["expires_at"]:
            raise HTTPException(status_code=410, detail="Share link expired")

        if link["password_hash"]:
            pwd = data.password if data else None
            if not pwd or not verify_password(pwd, link["password_hash"]):
                raise HTTPException(status_code=401, detail="Password required")

        prow = await db.execute("SELECT * FROM projects WHERE id = ?", (link["project_id"],))
        project = await prow.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get all ready assets with their latest version info
        arows = await db.execute("""
            SELECT a.id, a.name, a.asset_type, a.status,
                   v.version_num, v.duration_ms, v.fps, v.width, v.height
            FROM assets a
            LEFT JOIN asset_versions v ON v.asset_id = a.id
                AND v.version_num = (SELECT MAX(version_num) FROM asset_versions WHERE asset_id = a.id)
            WHERE a.project_id = ? AND a.status = 'ready'
            ORDER BY a.created_at DESC
        """, (link["project_id"],))
        assets = await arows.fetchall()

        asset_list = []
        for a in assets:
            asset_list.append({
                "id": a["id"],
                "name": a["name"],
                "asset_type": a["asset_type"],
                "version_num": a["version_num"],
                "duration_ms": a["duration_ms"],
                "fps": a["fps"],
                "width": a["width"],
                "height": a["height"],
            })

        return ProjectReviewSession(
            project_id=project["id"],
            project_name=project["name"],
            mode=link["mode"],
            allow_download=bool(link["allow_download"]),
            assets=asset_list,
        )
    finally:
        await db.close()


# ── Nudge ──────────────────────────────────────

@router.post("/api/share-links/{link_id}/nudge")
async def nudge_reviewer(link_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM share_links WHERE id = ?", (link_id,))
        link = await row.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Share link not found")

        last_nudge = link["last_nudged_at"]
        now = time.time()
        # Rate limit: 1 nudge per hour
        if last_nudge and (now - last_nudge) < 3600:
            mins_left = int((3600 - (now - last_nudge)) / 60)
            raise HTTPException(status_code=429, detail="Already nudged recently. Try again in {} minutes.".format(mins_left))

        await db.execute("UPDATE share_links SET last_nudged_at = ? WHERE id = ?", (now, link_id))
        await db.commit()

        # TODO: send actual email notification when email service is configured
        return {"status": "nudged", "message": "Reminder sent"}
    finally:
        await db.close()


# ── Public review endpoints ──────────────────────────────────────

@router.post("/api/review/{token}", response_model=ReviewSession)
async def validate_share(token: str, data: Optional[ShareValidate] = None):
    """Public: validate share link and return review session data."""
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM share_links WHERE token = ?", (token,))
        link = await row.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Share link not found")

        if link["expires_at"] and time.time() > link["expires_at"]:
            raise HTTPException(status_code=410, detail="Share link expired")

        if link["password_hash"]:
            pwd = data.password if data else None
            if not pwd or not verify_password(pwd, link["password_hash"]):
                raise HTTPException(status_code=401, detail="Password required")

        arow = await db.execute("SELECT * FROM assets WHERE id = ?", (link["asset_id"],))
        asset = await arow.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        vrow = await db.execute("SELECT * FROM asset_versions WHERE id = ?", (link["version_id"],))
        version = await vrow.fetchone()

        return ReviewSession(
            asset_id=asset["id"],
            asset_name=asset["name"],
            version_id=version["id"],
            version_num=version["version_num"],
            mode=link["mode"],
            allow_download=bool(link["allow_download"]),
            fps=version["fps"],
            duration_ms=version["duration_ms"],
            width=version["width"],
            height=version["height"],
        )
    finally:
        await db.close()


@router.post("/api/review/{token}/approve")
async def submit_approval(token: str, data: ApprovalCreate):
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM share_links WHERE token = ?", (token,))
        link = await row.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Share link not found")
        if link["mode"] != "approval":
            raise HTTPException(status_code=400, detail="Link is not in approval mode")

        now = time.time()
        approval_id = generate_id()
        await db.execute("""
            INSERT INTO approvals (id, asset_id, version_id, share_link_id, reviewer_name, status, note, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (approval_id, link["asset_id"], link["version_id"], link["id"],
              data.reviewer_name, data.status, data.note, now))
        await db.commit()

        # Send notification to asset owner
        arow = await db.execute("SELECT name FROM assets WHERE id = ?", (link["asset_id"],))
        asset = await arow.fetchone()
        if asset:
            try:
                await notify_approval(link["asset_id"], asset["name"], data.reviewer_name, data.status, data.note)
            except Exception:
                pass  # Don't fail the approval if notification fails

        return {"status": "ok", "approval_id": approval_id}
    finally:
        await db.close()
