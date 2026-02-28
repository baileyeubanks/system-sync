import os
import time
import shutil
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.config import PROJECTS_DIR, TMP_DIR, CHUNK_SIZE
from app.database import get_db
from app.models.asset import AssetResponse, VersionResponse, UploadInitResponse, AssetUpdate, AssetMove, BatchAction, BatchMove
from app.dependencies import get_current_user
from app.services.auth_service import generate_id

router = APIRouter(prefix="/api", tags=["assets"])


def detect_asset_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    video_exts = {"mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "mxf", "mpg", "mpeg", "ts", "mts"}
    image_exts = {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp", "svg"}
    audio_exts = {"mp3", "wav", "aac", "flac", "ogg", "m4a", "wma"}
    pdf_exts = {"pdf"}
    if ext in video_exts:
        return "video"
    if ext in image_exts:
        return "image"
    if ext in audio_exts:
        return "audio"
    if ext in pdf_exts:
        return "pdf"
    return "video"  # default


@router.post("/projects/{project_id}/assets/upload", response_model=UploadInitResponse)
async def upload_asset(
    project_id: str,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """Upload a file, create asset + version, trigger transcode."""
    db = await get_db()
    try:
        now = time.time()
        asset_id = generate_id()
        version_id = generate_id()
        upload_id = generate_id()
        asset_type = detect_asset_type(file.filename or "video.mp4")

        # Create asset directory
        asset_dir = PROJECTS_DIR / project_id / asset_id
        original_dir = asset_dir / "original"
        original_dir.mkdir(parents=True, exist_ok=True)

        # Save original file
        ext = (file.filename or "file").rsplit(".", 1)[-1] if "." in (file.filename or "") else "mp4"
        original_path = original_dir / "v1.{}".format(ext)
        with open(str(original_path), "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                f.write(chunk)

        file_size = original_path.stat().st_size

        # Create asset record
        await db.execute(
            "INSERT INTO assets (id, project_id, folder_id, name, asset_type, status, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (asset_id, project_id, folder_id, file.filename, asset_type, "transcoding", user["id"], now, now)
        )

        # Create version record
        await db.execute(
            "INSERT INTO asset_versions (id, asset_id, version_num, original_path, file_size, created_at) VALUES (?,?,?,?,?,?)",
            (version_id, asset_id, 1, str(original_path), file_size, now)
        )

        # Create transcode job
        job_id = generate_id()
        await db.execute(
            "INSERT INTO transcode_jobs (id, version_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, version_id, "queued", now)
        )
        await db.execute(
            "UPDATE asset_versions SET transcode_job_id = ? WHERE id = ?",
            (job_id, version_id)
        )

        await db.commit()

        # Enqueue transcode job (imported here to avoid circular)
        from app.services.transcode_service import enqueue_transcode
        await enqueue_transcode(job_id, version_id, str(original_path), str(asset_dir), asset_type)

        return UploadInitResponse(upload_id=upload_id, asset_id=asset_id, version_id=version_id, transcode_job_id=job_id)
    finally:
        await db.close()


@router.post("/assets/{asset_id}/versions", response_model=VersionResponse)
async def upload_version(
    asset_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload a new version of an existing asset."""
    db = await get_db()
    try:
        # Get current max version
        row = await db.execute(
            "SELECT MAX(version_num) as max_v, project_id FROM asset_versions av JOIN assets a ON a.id = av.asset_id WHERE av.asset_id = ?",
            (asset_id,)
        )
        result = await row.fetchone()
        if not result or result["max_v"] is None:
            raise HTTPException(status_code=404, detail="Asset not found")

        next_version = result["max_v"] + 1
        project_id = result["project_id"]

        now = time.time()
        version_id = generate_id()

        asset_dir = PROJECTS_DIR / project_id / asset_id
        original_dir = asset_dir / "original"
        original_dir.mkdir(parents=True, exist_ok=True)

        ext = (file.filename or "file").rsplit(".", 1)[-1] if "." in (file.filename or "") else "mp4"
        original_path = original_dir / "v{}.{}".format(next_version, ext)
        with open(str(original_path), "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        file_size = original_path.stat().st_size

        await db.execute(
            "INSERT INTO asset_versions (id, asset_id, version_num, original_path, file_size, created_at) VALUES (?,?,?,?,?,?)",
            (version_id, asset_id, next_version, str(original_path), file_size, now)
        )

        job_id = generate_id()
        await db.execute(
            "INSERT INTO transcode_jobs (id, version_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, version_id, "queued", now)
        )
        await db.execute(
            "UPDATE asset_versions SET transcode_job_id = ? WHERE id = ?",
            (job_id, version_id)
        )
        await db.execute(
            "UPDATE assets SET status = 'transcoding', updated_at = ? WHERE id = ?",
            (now, asset_id)
        )
        await db.commit()

        # Get asset type
        arow = await db.execute("SELECT asset_type FROM assets WHERE id = ?", (asset_id,))
        asset = await arow.fetchone()

        from app.services.transcode_service import enqueue_transcode
        await enqueue_transcode(job_id, version_id, str(original_path), str(asset_dir), asset["asset_type"])

        return VersionResponse(
            id=version_id, asset_id=asset_id, version_num=next_version,
            file_size=file_size, transcode_job_id=job_id, created_at=now
        )
    finally:
        await db.close()


# ─── Chunked Upload ──────────────────────────────────────────

@router.post("/projects/{project_id}/assets/upload-chunked")
async def init_chunked_upload(
    project_id: str,
    filename: str = Form(...),
    file_size: int = Form(...),
    folder_id: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """Initialize a chunked upload. Returns upload_id and chunk info."""
    db = await get_db()
    try:
        now = time.time()
        upload_id = generate_id()
        asset_id = generate_id()
        version_id = generate_id()
        asset_type = detect_asset_type(filename)

        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

        # Create tmp directory for chunks
        chunk_dir = TMP_DIR / upload_id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        # Store upload metadata
        import json
        meta = {
            "upload_id": upload_id, "asset_id": asset_id, "version_id": version_id,
            "project_id": project_id, "folder_id": folder_id,
            "filename": filename, "file_size": file_size, "asset_type": asset_type,
            "total_chunks": total_chunks, "chunk_size": CHUNK_SIZE,
            "received_chunks": [], "user_id": user["id"], "created_at": now,
        }
        with open(str(chunk_dir / "meta.json"), "w") as f:
            json.dump(meta, f)

        return {
            "upload_id": upload_id, "asset_id": asset_id, "version_id": version_id,
            "chunk_size": CHUNK_SIZE, "total_chunks": total_chunks,
        }
    finally:
        await db.close()


@router.put("/upload/{upload_id}/chunk/{chunk_index}")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Upload a single chunk."""
    import json

    chunk_dir = TMP_DIR / upload_id
    meta_path = chunk_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload not found")

    with open(str(meta_path)) as f:
        meta = json.load(f)

    if chunk_index < 0 or chunk_index >= meta["total_chunks"]:
        raise HTTPException(status_code=400, detail="Invalid chunk index")

    # Read chunk data from request body
    body = await request.body()
    chunk_path = chunk_dir / "chunk_{:05d}".format(chunk_index)
    with open(str(chunk_path), "wb") as f:
        f.write(body)

    # Track received chunks
    if chunk_index not in meta["received_chunks"]:
        meta["received_chunks"].append(chunk_index)
        meta["received_chunks"].sort()
    with open(str(meta_path), "w") as f:
        json.dump(meta, f)

    return {
        "chunk_index": chunk_index,
        "received": len(meta["received_chunks"]),
        "total": meta["total_chunks"],
    }


@router.post("/upload/{upload_id}/complete")
async def complete_chunked_upload(upload_id: str, user: dict = Depends(get_current_user)):
    """Assemble chunks into final file and trigger transcode."""
    import json

    chunk_dir = TMP_DIR / upload_id
    meta_path = chunk_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload not found")

    with open(str(meta_path)) as f:
        meta = json.load(f)

    # Verify all chunks received
    if len(meta["received_chunks"]) != meta["total_chunks"]:
        missing = set(range(meta["total_chunks"])) - set(meta["received_chunks"])
        raise HTTPException(status_code=400, detail="Missing chunks: {}".format(list(missing)[:10]))

    db = await get_db()
    try:
        now = time.time()
        asset_id = meta["asset_id"]
        version_id = meta["version_id"]
        project_id = meta["project_id"]
        filename = meta["filename"]
        asset_type = meta["asset_type"]

        # Assemble file
        asset_dir = PROJECTS_DIR / project_id / asset_id
        original_dir = asset_dir / "original"
        original_dir.mkdir(parents=True, exist_ok=True)

        ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp4"
        original_path = original_dir / "v1.{}".format(ext)
        with open(str(original_path), "wb") as out:
            for i in range(meta["total_chunks"]):
                cp = chunk_dir / "chunk_{:05d}".format(i)
                with open(str(cp), "rb") as cf:
                    out.write(cf.read())

        file_size = original_path.stat().st_size

        # Create DB records
        await db.execute(
            "INSERT INTO assets (id, project_id, folder_id, name, asset_type, status, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (asset_id, project_id, meta.get("folder_id"), filename, asset_type, "transcoding", meta["user_id"], now, now)
        )
        await db.execute(
            "INSERT INTO asset_versions (id, asset_id, version_num, original_path, file_size, created_at) VALUES (?,?,?,?,?,?)",
            (version_id, asset_id, 1, str(original_path), file_size, now)
        )

        job_id = generate_id()
        await db.execute(
            "INSERT INTO transcode_jobs (id, version_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, version_id, "queued", now)
        )
        await db.execute("UPDATE asset_versions SET transcode_job_id = ? WHERE id = ?", (job_id, version_id))
        await db.commit()

        # Clean up chunks
        shutil.rmtree(str(chunk_dir), ignore_errors=True)

        # Enqueue transcode
        from app.services.transcode_service import enqueue_transcode
        await enqueue_transcode(job_id, version_id, str(original_path), str(asset_dir), asset_type)

        return {
            "asset_id": asset_id, "version_id": version_id,
            "transcode_job_id": job_id, "file_size": file_size,
        }
    finally:
        await db.close()


@router.get("/upload/{upload_id}/status")
async def upload_status(upload_id: str, user: dict = Depends(get_current_user)):
    """Check which chunks have been received (for resume)."""
    import json
    meta_path = TMP_DIR / upload_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload not found")
    with open(str(meta_path)) as f:
        meta = json.load(f)
    return {
        "upload_id": upload_id,
        "total_chunks": meta["total_chunks"],
        "received_chunks": meta["received_chunks"],
        "chunk_size": meta["chunk_size"],
    }


@router.get("/projects/{project_id}/assets", response_model=List[AssetResponse])
async def list_assets(project_id: str, folder_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        if folder_id:
            rows = await db.execute(
                "SELECT * FROM assets WHERE project_id = ? AND folder_id = ? ORDER BY updated_at DESC",
                (project_id, folder_id)
            )
        else:
            rows = await db.execute(
                "SELECT * FROM assets WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,)
            )
        assets = await rows.fetchall()

        result = []
        for a in assets:
            vrows = await db.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_num DESC",
                (a["id"],)
            )
            versions = [VersionResponse(**dict(v)) for v in await vrows.fetchall()]
            asset = AssetResponse(**dict(a), versions=versions)
            result.append(asset)
        return result
    finally:
        await db.close()


@router.get("/assets/{asset_id}", response_model=AssetResponse)
async def get_asset(asset_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
        asset = await row.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        vrows = await db.execute(
            "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_num DESC",
            (asset_id,)
        )
        versions = [VersionResponse(**dict(v)) for v in await vrows.fetchall()]
        return AssetResponse(**dict(asset), versions=versions)
    finally:
        await db.close()


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        # Get project_id for directory cleanup
        row = await db.execute("SELECT project_id FROM assets WHERE id = ?", (asset_id,))
        asset = await row.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        # Delete from DB (cascades to versions, comments, etc.)
        await db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        await db.commit()

        # Delete files
        asset_dir = PROJECTS_DIR / asset["project_id"] / asset_id
        if asset_dir.exists():
            shutil.rmtree(str(asset_dir))

        return {"status": "deleted"}
    finally:
        await db.close()


@router.patch("/assets/{asset_id}")
async def update_asset(asset_id: str, data: AssetUpdate, user: dict = Depends(get_current_user)):
    """Rename an asset."""
    db = await get_db()
    try:
        if not data.name:
            raise HTTPException(status_code=400, detail="Name is required")
        now = time.time()
        await db.execute(
            "UPDATE assets SET name = ?, updated_at = ? WHERE id = ?",
            (data.name, now, asset_id)
        )
        await db.commit()
        return await get_asset(asset_id, user)
    finally:
        await db.close()


@router.patch("/assets/{asset_id}/move")
async def move_asset(asset_id: str, data: AssetMove, user: dict = Depends(get_current_user)):
    """Move an asset to a different folder."""
    db = await get_db()
    try:
        now = time.time()
        await db.execute(
            "UPDATE assets SET folder_id = ?, updated_at = ? WHERE id = ?",
            (data.folder_id, now, asset_id)
        )
        await db.commit()
        return {"status": "moved", "folder_id": data.folder_id}
    finally:
        await db.close()


@router.post("/assets/batch-delete")
async def batch_delete_assets(data: BatchAction, user: dict = Depends(get_current_user)):
    """Delete multiple assets."""
    db = await get_db()
    try:
        deleted = 0
        for aid in data.asset_ids:
            row = await db.execute("SELECT project_id FROM assets WHERE id = ?", (aid,))
            asset = await row.fetchone()
            if asset:
                await db.execute("DELETE FROM assets WHERE id = ?", (aid,))
                asset_dir = PROJECTS_DIR / asset["project_id"] / aid
                if asset_dir.exists():
                    shutil.rmtree(str(asset_dir))
                deleted += 1
        await db.commit()
        return {"deleted": deleted}
    finally:
        await db.close()


@router.post("/assets/batch-move")
async def batch_move_assets(data: BatchMove, user: dict = Depends(get_current_user)):
    """Move multiple assets to a folder."""
    db = await get_db()
    try:
        now = time.time()
        placeholders = ",".join("?" for _ in data.asset_ids)
        values = [data.folder_id, now] + data.asset_ids
        await db.execute(
            "UPDATE assets SET folder_id = ?, updated_at = ? WHERE id IN ({})".format(placeholders),
            values
        )
        await db.commit()
        return {"moved": len(data.asset_ids), "folder_id": data.folder_id}
    finally:
        await db.close()


MIME_MAP = {
    "mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
    "mkv": "video/x-matroska", "webm": "video/webm", "m4v": "video/x-m4v",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    "svg": "image/svg+xml", "tiff": "image/tiff",
    "mp3": "audio/mpeg", "wav": "audio/wav", "aac": "audio/aac",
    "flac": "audio/flac", "ogg": "audio/ogg", "m4a": "audio/mp4",
    "pdf": "application/pdf",
}


def _detect_mime(filepath: str) -> str:
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
    return MIME_MAP.get(ext, "application/octet-stream")


@router.get("/assets/{asset_id}/stream/{version_num}")
async def stream_asset(asset_id: str, version_num: int):
    """Stream asset file with correct MIME type and range request support."""
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT proxy_path, original_path FROM asset_versions WHERE asset_id = ? AND version_num = ?",
            (asset_id, version_num)
        )
        version = await row.fetchone()
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")

        # Prefer proxy (transcoded), fall back to original
        file_path = version["proxy_path"] or version["original_path"]
        if not file_path or not Path(file_path).exists():
            raise HTTPException(status_code=404, detail="File not found")

        mime = _detect_mime(file_path)
        filename = Path(file_path).name
        return FileResponse(file_path, media_type=mime, filename=filename)
    finally:
        await db.close()


@router.get("/assets/{asset_id}/thumbnail/{version_num}")
async def get_thumbnail(asset_id: str, version_num: int):
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT thumbnail_path FROM asset_versions WHERE asset_id = ? AND version_num = ?",
            (asset_id, version_num)
        )
        version = await row.fetchone()
        if not version or not version["thumbnail_path"]:
            raise HTTPException(status_code=404, detail="Thumbnail not found")

        return FileResponse(version["thumbnail_path"], media_type="image/jpeg")
    finally:
        await db.close()


@router.get("/assets/{asset_id}/sprite/{version_num}")
async def get_sprite(asset_id: str, version_num: int):
    db = await get_db()
    try:
        row = await db.execute(
            "SELECT sprite_path FROM asset_versions WHERE asset_id = ? AND version_num = ?",
            (asset_id, version_num)
        )
        version = await row.fetchone()
        if not version or not version["sprite_path"]:
            raise HTTPException(status_code=404, detail="Sprite not found")

        return FileResponse(version["sprite_path"], media_type="image/jpeg")
    finally:
        await db.close()
