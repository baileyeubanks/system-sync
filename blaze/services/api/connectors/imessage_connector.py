from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.db import Database


@dataclass
class IMessageConfig:
    enabled: bool
    export_root: str
    send_enabled_cc: bool
    send_enabled_acs: bool
    sender_user_cc: str
    sender_user_acs: str
    rate_limit_per_minute: int


class IMessageConnector:
    def __init__(self, db: Database, config: IMessageConfig) -> None:
        self.db = db
        self.config = config
        self._send_timestamps: list[float] = []

    def _is_phone_or_email(self, raw: str) -> bool:
        value = raw.strip()
        if not value:
            return False
        if "@" in value:
            return True
        digits = "".join(ch for ch in value if ch.isdigit())
        return len(digits) >= 10

    def _normalize_identifier(self, raw: str) -> str:
        value = (raw or "").strip()
        if "@" in value:
            return value.lower()
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return value
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        return f"+{digits}"

    def _resolve_export_dir(self, business_unit: str, export_dir: str | None = None) -> Path:
        if export_dir:
            return Path(export_dir).expanduser()
        base = Path(self.config.export_root).expanduser()
        return base / business_unit / "export"

    def ingest_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "reason": "IMESSAGE_DISABLED"}

        business_unit = (payload.get("business_unit") or "CC").upper()
        if business_unit not in {"CC", "ACS"}:
            return {"ok": False, "reason": "business_unit must be CC or ACS"}

        export_dir = self._resolve_export_dir(business_unit, payload.get("export_dir"))
        if not export_dir.exists():
            return {"ok": False, "reason": f"export dir not found: {export_dir}"}

        limit = int(payload.get("limit_files") or 200)
        limit = max(1, min(limit, 5000))
        files = sorted(export_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        source = (payload.get("source") or "imessage_export").strip() or "imessage_export"

        idempotency_key = (payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            seed = f"{business_unit}:{str(export_dir)}:{len(files)}:{int(time.time() // 60)}"
            idempotency_key = "imessage-export-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

        job = self.db.begin_ingestion_job(
            provider="imessage",
            business_unit=business_unit,
            job_type="export_sync",
            idempotency_key=idempotency_key,
            details={"files_seen": len(files), "export_dir": str(export_dir)},
        )
        if not job.get("accepted"):
            return {"ok": True, "duplicate": True, "idempotency_key": idempotency_key, "status": job.get("status")}

        threads_upserted = 0
        interactions_upserted = 0
        contacts_upserted = 0
        for path in files:
            thread_id = path.stem
            participants = [self._normalize_identifier(p.strip()) for p in thread_id.split(",") if p.strip()]
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            snippet = lines[-1][:500] if lines else ""
            message_count = max(1, len(lines))
            mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))

            self.db.upsert_message_thread(
                business_unit=business_unit,
                source=source,
                external_thread_id=thread_id,
                latest_message_at=mtime,
                participants=participants,
                message_count=message_count,
                metadata={"file_path": str(path), "mode": "export"},
            )
            threads_upserted += 1

            # Stable idempotency: exporter rewrites files, so mtime-based keys duplicate on re-export.
            # Use a snippet hash so re-running without new messages doesn't create duplicate interactions.
            thread_hash = hashlib.sha1(thread_id.encode("utf-8")).hexdigest()[:12]
            snippet_hash = hashlib.sha1(snippet.encode("utf-8")).hexdigest()[:12] if snippet else "empty"
            interaction_key = f"imsg-exp-{business_unit}-{thread_hash}-{snippet_hash}"
            self.db.add_interaction(
                business_unit=business_unit,
                source="imessage",
                direction="unknown",
                content=snippet,
                idempotency_key=interaction_key,
                contact_id=None,
            )
            interactions_upserted += 1

            for participant in participants:
                if not self._is_phone_or_email(participant):
                    continue
                contact_id = self.db.upsert_contact_from_external(
                    business_unit=business_unit,
                    full_name=participant,
                    primary_email=participant if "@" in participant else None,
                    company=None,
                    source_of_truth="imessage",
                    provider="imessage_handle",
                    external_id=participant,
                    metadata={"thread_id": thread_id},
                )
                if contact_id:
                    contacts_upserted += 1

        self.db.upsert_sync_cursor("imessage_export", business_unit, str(int(time.time())), "ok")
        self.db.finalize_ingestion_job(
            provider="imessage",
            business_unit=business_unit,
            job_type="export_sync",
            idempotency_key=idempotency_key,
            status="ok",
            details={
                "files_seen": len(files),
                "threads_upserted": threads_upserted,
                "interactions_upserted": interactions_upserted,
                "contacts_upserted": contacts_upserted,
            },
        )
        return {
            "ok": True,
            "business_unit": business_unit,
            "source": source,
            "threads_upserted": threads_upserted,
            "interactions_upserted": interactions_upserted,
            "contacts_upserted": contacts_upserted,
            "idempotency_key": idempotency_key,
            "export_dir": str(export_dir),
        }

    def ingest_chatdb(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "reason": "IMESSAGE_DISABLED"}

        business_unit = (payload.get("business_unit") or "CC").upper()
        if business_unit not in {"CC", "ACS"}:
            return {"ok": False, "reason": "business_unit must be CC or ACS"}

        chat_db_path = Path(payload.get("chat_db_path") or Path.home() / "Library" / "Messages" / "chat.db").expanduser()
        if not chat_db_path.exists():
            return {"ok": False, "reason": f"chat.db not found: {chat_db_path}"}

        limit = int(payload.get("limit_threads") or 500)
        limit = max(1, min(limit, 5000))
        idempotency_key = (payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            seed = f"{business_unit}:{str(chat_db_path)}:{int(time.time() // 60)}"
            idempotency_key = "imessage-chatdb-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

        job = self.db.begin_ingestion_job(
            provider="imessage",
            business_unit=business_unit,
            job_type="chatdb_sync",
            idempotency_key=idempotency_key,
            details={"chat_db_path": str(chat_db_path), "limit_threads": limit},
        )
        if not job.get("accepted"):
            return {"ok": True, "duplicate": True, "idempotency_key": idempotency_key, "status": job.get("status")}

        # NOTE: Accessing ~/Library/Messages/chat.db can fail under macOS TCC (Full Disk Access)
        # depending on how this process was started. Prefer feeding this endpoint a snapshot path
        # under IMESSAGE_EXPORT_ROOT. We still harden this path to return JSON errors (no "empty reply").
        uri = f"file:{chat_db_path}?mode=ro"
        conn: sqlite3.Connection | None = None
        try:
            try:
                conn = sqlite3.connect(uri, uri=True)
            except Exception:
                # Fallback for environments where URI mode fails but direct open works.
                conn = sqlite3.connect(str(chat_db_path))
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA query_only = ON")
            except Exception:
                pass

            rows = conn.execute(
                """
                SELECT
                  c.chat_identifier AS thread_id,
                  COUNT(DISTINCT m.ROWID) AS message_count,
                  MAX(datetime(m.date/1000000000 + strftime('%s', '2001-01-01'), 'unixepoch')) AS latest_message_at
                FROM chat c
                LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
                LEFT JOIN message m ON cmj.message_id = m.ROWID
                WHERE c.chat_identifier IS NOT NULL
                GROUP BY c.chat_identifier
                ORDER BY message_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except Exception as exc:
            self.db.finalize_ingestion_job(
                provider="imessage",
                business_unit=business_unit,
                job_type="chatdb_sync",
                idempotency_key=idempotency_key,
                status="error",
                details={"chat_db_path": str(chat_db_path), "limit_threads": limit, "error": str(exc)},
            )
            return {"ok": False, "business_unit": business_unit, "error": str(exc), "chat_db_path": str(chat_db_path)}
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

        upserted = 0
        for row in rows:
            thread_id = str(row["thread_id"])
            participants = [self._normalize_identifier(p.strip()) for p in thread_id.split(",") if p.strip()]
            self.db.upsert_message_thread(
                business_unit=business_unit,
                source="imessage_chatdb",
                external_thread_id=thread_id,
                latest_message_at=row["latest_message_at"],
                participants=participants,
                message_count=int(row["message_count"] or 0),
                metadata={"chat_db_path": str(chat_db_path), "mode": "chatdb"},
            )
            upserted += 1

        self.db.upsert_sync_cursor("imessage_chatdb", business_unit, str(int(time.time())), "ok")
        self.db.finalize_ingestion_job(
            provider="imessage",
            business_unit=business_unit,
            job_type="chatdb_sync",
            idempotency_key=idempotency_key,
            status="ok",
            details={"threads_upserted": upserted, "chat_db_path": str(chat_db_path)},
        )
        return {
            "ok": True,
            "business_unit": business_unit,
            "threads_upserted": upserted,
            "chat_db_path": str(chat_db_path),
            "idempotency_key": idempotency_key,
        }

    def propose_send(self, payload: dict[str, Any]) -> dict[str, Any]:
        business_unit = (payload.get("business_unit") or "CC").upper()
        recipient = (payload.get("recipient") or "").strip()
        message = (payload.get("message") or "").strip()
        if business_unit not in {"CC", "ACS"}:
            return {"ok": False, "reason": "business_unit must be CC or ACS"}
        if not recipient or not message:
            return {"ok": False, "reason": "recipient and message are required"}

        approval_id = self.db.create_action_approval(
            business_unit=business_unit,
            action_type="imessage_send",
            payload={"recipient": recipient, "message": message, "business_unit": business_unit},
            state="proposed",
        )
        return {
            "ok": True,
            "approval_id": approval_id,
            "state": "proposed",
            "business_unit": business_unit,
            "recipient": recipient,
        }

    def direct_send(self, payload: dict) -> dict:
        """Direct send — queues to /tmp/imsg_queue/ for the GUI-context relay daemon.

        Supports two modes:
        - New message:  {"recipient": "+1...", "message": "...", "business_unit": "ACS"}
        - Reply in chat: {"chat_id": "17", "message": "...", "business_unit": "ACS"}
          Using chat_id ensures the reply comes from the correct ACS iMessage account.
        """
        import json as _json, uuid as _uuid, os as _os
        business_unit = (payload.get("business_unit") or "ACS").upper()
        recipient = (payload.get("recipient") or "").strip()
        chat_id   = str(payload.get("chat_id") or "").strip()
        message   = (payload.get("message") or "").strip()

        if business_unit not in {"CC", "ACS"}:
            return {"ok": False, "reason": "business_unit must be CC or ACS"}
        if not message:
            return {"ok": False, "reason": "message required"}
        if not recipient and not chat_id:
            return {"ok": False, "reason": "recipient or chat_id required"}
        if not self._send_allowed_for_bu(business_unit):
            return {"ok": False, "reason": f"imessage sending disabled for {business_unit}"}
        if self._rate_limited():
            return {"ok": False, "reason": "rate_limit_exceeded"}

        queue_dir = "/tmp/imsg_queue"
        _os.makedirs(queue_dir, exist_ok=True)
        fname = _os.path.join(queue_dir, f"{_uuid.uuid4().hex}.json")

        if chat_id:
            queue_entry = {"chat_id": chat_id, "text": message}
            ref = f"chat:{chat_id}"
        else:
            normalized = self._normalize_identifier(recipient)
            queue_entry = {"to": normalized, "text": message}
            ref = normalized

        with open(fname, "w") as _f:
            _json.dump(queue_entry, _f)
        return {"ok": True, "business_unit": business_unit, "recipient": ref}


    def _rate_limited(self) -> bool:
        now = time.time()
        cutoff = now - 60.0
        self._send_timestamps = [ts for ts in self._send_timestamps if ts >= cutoff]
        if len(self._send_timestamps) >= max(1, self.config.rate_limit_per_minute):
            return True
        self._send_timestamps.append(now)
        return False

    def _send_allowed_for_bu(self, business_unit: str) -> bool:
        if business_unit == "CC":
            return self.config.send_enabled_cc
        if business_unit == "ACS":
            return self.config.send_enabled_acs
        return False

    def _required_sender_user(self, business_unit: str) -> str:
        return self.config.sender_user_cc if business_unit == "CC" else self.config.sender_user_acs

    def send_with_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        approval_id = int(payload.get("approval_id") or 0)
        if approval_id <= 0:
            return {"ok": False, "reason": "approval_id is required"}
        approval = self.db.get_action_approval(approval_id)
        if not approval:
            return {"ok": False, "reason": "approval not found"}
        if approval.get("state") != "approved":
            return {"ok": False, "reason": "approval must be in approved state", "state": approval.get("state")}

        action_payload = approval.get("payload") or {}
        business_unit = (action_payload.get("business_unit") or approval.get("business_unit") or "CC").upper()
        recipient = (action_payload.get("recipient") or "").strip()
        message = (action_payload.get("message") or "").strip()
        if not recipient or not message:
            return {"ok": False, "reason": "approval payload missing recipient/message"}

        if not self._send_allowed_for_bu(business_unit):
            return {"ok": False, "reason": f"imessage sending disabled for {business_unit}"}
        if self._rate_limited():
            return {"ok": False, "reason": "rate_limit_exceeded"}

        required_user = self._required_sender_user(business_unit)
        active_user = os.getenv("USER", "")
        if required_user and active_user and active_user != required_user:
            return {
                "ok": False,
                "reason": "wrong_macos_user_context",
                "required_user": required_user,
                "active_user": active_user,
            }

        script = (
            'tell application "Messages"\n'
            '  set targetService to 1st service whose service type = iMessage\n'
            '  set targetBuddy to buddy "{recipient}" of targetService\n'
            '  send "{message}" to targetBuddy\n'
            "end tell\n"
        ).format(
            recipient=recipient.replace('"', '\\"'),
            message=message.replace('"', '\\"'),
        )
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        if proc.returncode != 0:
            self.db.add_admin_audit_log(
                provider="imessage",
                delegated_subject=required_user or "unknown",
                action="send",
                status="error",
                details={"approval_id": approval_id, "stderr": proc.stderr.read().decode("utf-8", errors="replace").strip()},
            )
            return {"ok": False, "reason": "osascript_failed", "detail": proc.stderr.read().decode("utf-8", errors="replace").strip()}

        self.db.set_action_approval_state(approval_id, "executed")
        self.db.add_admin_audit_log(
            provider="imessage",
            delegated_subject=required_user or "unknown",
            action="send",
            status="ok",
            details={"approval_id": approval_id, "business_unit": business_unit, "recipient": recipient},
        )
        return {
            "ok": True,
            "approval_id": approval_id,
            "state": "executed",
            "business_unit": business_unit,
            "recipient": recipient,
        }
