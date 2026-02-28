import time
from typing import List
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse

from app.config import PROJECTS_DIR
from app.database import get_db
from app.models.project import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    FolderCreate, FolderUpdate, FolderResponse
)
from app.dependencies import get_current_user
from app.services.auth_service import generate_id

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=List[ProjectResponse])
async def list_projects(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await db.execute("""
            SELECT p.*, COALESCE(ac.cnt, 0) as asset_count
            FROM projects p
            LEFT JOIN (SELECT project_id, COUNT(*) as cnt FROM assets GROUP BY project_id) ac
                ON ac.project_id = p.id
            WHERE p.owner_id = ? OR p.id IN (SELECT project_id FROM project_members WHERE user_id = ?)
            ORDER BY p.updated_at DESC
        """, (user["id"], user["id"]))
        projects = await rows.fetchall()
        return [ProjectResponse(**dict(p)) for p in projects]
    finally:
        await db.close()


@router.post("", response_model=ProjectResponse)
async def create_project(data: ProjectCreate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        now = time.time()
        project_id = generate_id()
        await db.execute(
            "INSERT INTO projects (id, name, description, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, data.name, data.description, user["id"], now, now)
        )
        await db.commit()
        return ProjectResponse(
            id=project_id, name=data.name, description=data.description,
            owner_id=user["id"], created_at=now, updated_at=now
        )
    finally:
        await db.close()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await db.execute("""
            SELECT p.*, COALESCE(ac.cnt, 0) as asset_count
            FROM projects p
            LEFT JOIN (SELECT project_id, COUNT(*) as cnt FROM assets GROUP BY project_id) ac
                ON ac.project_id = p.id
            WHERE p.id = ?
        """, (project_id,))
        project = await row.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse(**dict(project))
    finally:
        await db.close()


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, data: ProjectUpdate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.description is not None:
            updates["description"] = data.description
        if data.branding is not None:
            updates["branding"] = data.branding
        if data.accent_color is not None:
            updates["accent_color"] = data.accent_color
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["updated_at"] = time.time()
        set_clause = ", ".join("{} = ?".format(k) for k in updates.keys())
        values = list(updates.values()) + [project_id]
        await db.execute(
            "UPDATE projects SET {} WHERE id = ?".format(set_clause), values
        )
        await db.commit()
        return await get_project(project_id, user)
    finally:
        await db.close()


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
        return {"status": "deleted"}
    finally:
        await db.close()


@router.post("/{project_id}/logo")
async def upload_logo(project_id: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Upload a project logo."""
    db = await get_db()
    try:
        logo_dir = PROJECTS_DIR / project_id
        logo_dir.mkdir(parents=True, exist_ok=True)
        ext = (file.filename or "logo.png").rsplit(".", 1)[-1] if "." in (file.filename or "") else "png"
        logo_path = logo_dir / "logo.{}".format(ext)
        with open(str(logo_path), "wb") as f:
            content = await file.read()
            f.write(content)
        await db.execute(
            "UPDATE projects SET logo_path = ?, updated_at = ? WHERE id = ?",
            (str(logo_path), time.time(), project_id)
        )
        await db.commit()
        return {"logo_path": str(logo_path)}
    finally:
        await db.close()


@router.get("/{project_id}/logo")
async def get_logo(project_id: str):
    """Serve the project logo."""
    db = await get_db()
    try:
        row = await db.execute("SELECT logo_path FROM projects WHERE id = ?", (project_id,))
        project = await row.fetchone()
        if not project or not project["logo_path"]:
            raise HTTPException(status_code=404, detail="Logo not found")
        logo = Path(project["logo_path"])
        if not logo.exists():
            raise HTTPException(status_code=404, detail="Logo file not found")
        ext = logo.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "webp": "image/webp"}.get(ext.lstrip("."), "image/png")
        return FileResponse(str(logo), media_type=mime)
    finally:
        await db.close()


@router.get("/{project_id}/branding")
async def get_branding(project_id: str):
    """Return public branding info for a project (no auth required — used on share pages)."""
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT id, name, logo_path, accent_color FROM projects WHERE id = ?",
            (project_id,)
        )
        project = await row.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        has_logo = bool(project["logo_path"] and Path(project["logo_path"]).exists())
        return {
            "project_id": project["id"],
            "project_name": project["name"],
            "has_logo": has_logo,
            "accent_color": project["accent_color"] or "#3b82f6",
        }
    finally:
        await db.close()


# Folders

@router.get("/{project_id}/folders", response_model=List[FolderResponse])
async def list_folders(project_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await db.execute(
            "SELECT * FROM folders WHERE project_id = ? ORDER BY sort_order, name", (project_id,)
        )
        folders = await rows.fetchall()
        return [FolderResponse(**dict(f)) for f in folders]
    finally:
        await db.close()


@router.post("/{project_id}/folders", response_model=FolderResponse)
async def create_folder(project_id: str, data: FolderCreate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        now = time.time()
        folder_id = generate_id()
        await db.execute(
            "INSERT INTO folders (id, project_id, parent_id, name, created_at) VALUES (?,?,?,?,?)",
            (folder_id, project_id, data.parent_id, data.name, now)
        )
        await db.commit()
        return FolderResponse(
            id=folder_id, project_id=project_id, parent_id=data.parent_id,
            name=data.name, created_at=now
        )
    finally:
        await db.close()


@router.patch("/{project_id}/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(project_id: str, folder_id: str, data: FolderUpdate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.parent_id is not None:
            updates["parent_id"] = data.parent_id if data.parent_id != "" else None
        if data.sort_order is not None:
            updates["sort_order"] = data.sort_order
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clause = ", ".join("{} = ?".format(k) for k in updates.keys())
        values = list(updates.values()) + [folder_id, project_id]
        await db.execute(
            "UPDATE folders SET {} WHERE id = ? AND project_id = ?".format(set_clause), values
        )
        await db.commit()

        row = await db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,))
        folder = await row.fetchone()
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        return FolderResponse(**dict(folder))
    finally:
        await db.close()


@router.delete("/{project_id}/folders/{folder_id}")
async def delete_folder(project_id: str, folder_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        # Get the folder to find its parent
        row = await db.execute("SELECT parent_id FROM folders WHERE id = ? AND project_id = ?", (folder_id, project_id))
        folder = await row.fetchone()
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        parent_id = folder["parent_id"]

        # Re-parent child folders to this folder's parent
        await db.execute(
            "UPDATE folders SET parent_id = ? WHERE parent_id = ? AND project_id = ?",
            (parent_id, folder_id, project_id)
        )

        # Move assets to project root (folder_id = NULL)
        await db.execute(
            "UPDATE assets SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
        )

        # Delete the folder
        await db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        await db.commit()
        return {"status": "deleted"}
    finally:
        await db.close()
