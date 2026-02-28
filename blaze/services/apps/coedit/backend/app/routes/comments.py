import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

from app.database import get_db
from app.models.comment import CommentCreate, CommentUpdate, CommentResponse
from app.dependencies import get_current_user, get_optional_user
from app.services.auth_service import generate_id
from app.services.notification_service import notify_new_comment
from app.services.ws_manager import publish, review_channel
from app.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_ENABLED, APP_URL

router = APIRouter(prefix="/api", tags=["comments"])


def build_comment_tree(comments: list) -> List[CommentResponse]:
    """Build threaded comment tree from flat list."""
    by_id = {}
    roots = []
    for c in comments:
        cr = CommentResponse(**dict(c), replies=[])
        by_id[cr.id] = cr

    for cr in by_id.values():
        if cr.parent_id and cr.parent_id in by_id:
            by_id[cr.parent_id].replies.append(cr)
        else:
            roots.append(cr)

    return roots


@router.get("/assets/{asset_id}/versions/{version_id}/comments", response_model=List[CommentResponse])
async def list_comments(
    asset_id: str,
    version_id: str,
    include_private: bool = Query(False),
    user: Optional[dict] = Depends(get_optional_user),
):
    db = await get_db()
    try:
        if include_private and user:
            rows = await db.execute(
                "SELECT * FROM comments WHERE asset_id = ? AND version_id = ? ORDER BY created_at ASC",
                (asset_id, version_id)
            )
        else:
            rows = await db.execute(
                "SELECT * FROM comments WHERE asset_id = ? AND version_id = ? AND is_private = 0 ORDER BY created_at ASC",
                (asset_id, version_id)
            )
        comments = await rows.fetchall()
        return build_comment_tree(comments)
    finally:
        await db.close()


@router.post("/assets/{asset_id}/versions/{version_id}/comments", response_model=CommentResponse)
async def create_comment(
    asset_id: str,
    version_id: str,
    data: CommentCreate,
    user: Optional[dict] = Depends(get_optional_user),
):
    db = await get_db()
    try:
        now = time.time()
        comment_id = generate_id()

        author_id = user["id"] if user else None
        author_name = user["name"] if user else (data.author_name or "Anonymous Reviewer")

        await db.execute("""
            INSERT INTO comments (id, asset_id, version_id, parent_id, author_id, author_name,
                frame_start, frame_end, timecode_start, timecode_end,
                pin_x, pin_y, annotation_type, annotation_data,
                body, is_private, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            comment_id, asset_id, version_id, data.parent_id,
            author_id, author_name,
            data.frame_start, data.frame_end,
            data.timecode_start, data.timecode_end,
            data.pin_x, data.pin_y,
            data.annotation_type, data.annotation_data,
            data.body, 1 if data.is_private else 0, now, now
        ))
        await db.commit()

        # Broadcast via WebSocket
        try:
            await publish(review_channel(asset_id, version_id), {
                "type": "new_comment",
                "comment_id": comment_id,
                "author_name": author_name,
                "body": data.body,
                "timecode_start": data.timecode_start,
            })
        except Exception:
            pass

        # Send notification to asset owner (don't notify if author is the owner)
        arow = await db.execute("SELECT name, created_by FROM assets WHERE id = ?", (asset_id,))
        asset = await arow.fetchone()
        if asset and asset["created_by"] != author_id:
            try:
                await notify_new_comment(asset_id, asset["name"], author_name, data.body, data.timecode_start)
            except Exception:
                pass

        # If comment is from an external reviewer (no auth user), send email to project owner
        if user is None and SMTP_ENABLED:
            try:
                # Look up project owner email
                proj_row = await db.execute(
                    """SELECT p.id, p.name, u.email, u.name as owner_name
                       FROM projects p
                       JOIN assets a ON a.project_id = p.id
                       JOIN users u ON u.id = p.created_by
                       WHERE a.id = ?""",
                    (asset_id,)
                )
                proj = await proj_row.fetchone()
                if proj:
                    _send_external_comment_email(
                        owner_email=proj["email"],
                        owner_name=proj["owner_name"],
                        reviewer_name=author_name,
                        asset_name=asset["name"] if asset else "Unknown Asset",
                        comment_body=data.body,
                        timecode=data.timecode_start,
                        asset_id=asset_id,
                    )
            except Exception:
                pass  # Never fail the request due to email

        return CommentResponse(
            id=comment_id, asset_id=asset_id, version_id=version_id,
            parent_id=data.parent_id, author_id=author_id, author_name=author_name,
            frame_start=data.frame_start, frame_end=data.frame_end,
            timecode_start=data.timecode_start, timecode_end=data.timecode_end,
            pin_x=data.pin_x, pin_y=data.pin_y,
            annotation_type=data.annotation_type, annotation_data=data.annotation_data,
            body=data.body, is_private=data.is_private, is_resolved=False,
            created_at=now, updated_at=now
        )
    finally:
        await db.close()


@router.patch("/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(comment_id: str, data: CommentUpdate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        now = time.time()
        updates = {"updated_at": now}
        if data.body is not None:
            updates["body"] = data.body
        if data.is_resolved is not None:
            updates["is_resolved"] = 1 if data.is_resolved else 0

        set_clause = ", ".join("{} = ?".format(k) for k in updates.keys())
        values = list(updates.values()) + [comment_id]
        await db.execute("UPDATE comments SET {} WHERE id = ?".format(set_clause), values)
        await db.commit()

        row = await db.execute("SELECT * FROM comments WHERE id = ?", (comment_id,))
        comment = await row.fetchone()
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")

        # Broadcast via WebSocket
        try:
            event_type = "comment_resolved" if data.is_resolved is not None else "comment_updated"
            await publish(review_channel(comment["asset_id"], comment["version_id"]), {
                "type": event_type,
                "comment_id": comment_id,
                "is_resolved": bool(comment["is_resolved"]),
            })
        except Exception:
            pass

        return CommentResponse(**dict(comment))
    finally:
        await db.close()


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        # Get comment info before deleting for WS broadcast
        row = await db.execute("SELECT asset_id, version_id FROM comments WHERE id = ?", (comment_id,))
        comment = await row.fetchone()

        await db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        await db.commit()

        # Broadcast via WebSocket
        if comment:
            try:
                await publish(review_channel(comment["asset_id"], comment["version_id"]), {
                    "type": "comment_deleted",
                    "comment_id": comment_id,
                })
            except Exception:
                pass

        return {"status": "deleted"}
    finally:
        await db.close()


@router.get("/assets/{asset_id}/versions/{version_id}/tasks", response_model=List[CommentResponse])
async def list_tasks(asset_id: str, version_id: str, user: dict = Depends(get_current_user)):
    """Get unresolved comments as a task checklist."""
    db = await get_db()
    try:
        rows = await db.execute(
            "SELECT * FROM comments WHERE asset_id = ? AND version_id = ? AND parent_id IS NULL ORDER BY created_at ASC",
            (asset_id, version_id)
        )
        comments = await rows.fetchall()
        return [CommentResponse(**dict(c)) for c in comments]
    finally:
        await db.close()


def _send_external_comment_email(
    owner_email: str,
    owner_name: str,
    reviewer_name: str,
    asset_name: str,
    comment_body: str,
    timecode: str | None,
    asset_id: str,
):
    """Send email notification to project owner when an external reviewer leaves a comment."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"New review comment on '{asset_name}' from {reviewer_name}"
    msg["From"] = SMTP_FROM
    msg["To"] = "blaze@contentco-op.com"  # Always CC the team inbox
    msg["CC"] = owner_email

    tc_part = f" at {timecode}" if timecode else ""
    text = (
        f"Hi {owner_name},\n\n"
        f"{reviewer_name} left a comment on \"{asset_name}\"{tc_part}:\n\n"
        f"  \"{comment_body}\"\n\n"
        f"View the review: {APP_URL}/review/{asset_id}\n\n"
        "— Co-Edit"
    )
    html = (
        f"<p>Hi {owner_name},</p>"
        f"<p><strong>{reviewer_name}</strong> left a comment on <strong>{asset_name}</strong>{tc_part}:</p>"
        f"<blockquote style='border-left:3px solid #3b82f6;padding-left:12px;color:#555;'>{comment_body}</blockquote>"
        f"<p><a href='{APP_URL}/review/{asset_id}'>View in Co-Edit →</a></p>"
        "<p style='color:#888;font-size:12px;'>— Co-Edit for Content Co-op</p>"
    )
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
