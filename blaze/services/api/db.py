from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self._run_migrations()

    def _init_schema(self) -> None:
        schema_path = Path(__file__).resolve().parent / "schema.sql"
        self.conn.executescript(schema_path.read_text())
        self.conn.commit()

    def _has_column(self, table: str, column: str) -> bool:
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        for row in cur.fetchall():
            if row["name"] == column:
                return True
        return False

    def _run_migrations(self) -> None:
        if not self._has_column("contact_identities", "business_unit"):
            self.conn.execute(
                "ALTER TABLE contact_identities ADD COLUMN business_unit TEXT NOT NULL DEFAULT 'CC'"
            )

        if not self._has_column("relationship_profiles", "business_unit"):
            self.conn.execute(
                "ALTER TABLE relationship_profiles ADD COLUMN business_unit TEXT NOT NULL DEFAULT 'CC'"
            )
            self.conn.execute(
                """
                UPDATE relationship_profiles
                SET business_unit = (
                  SELECT c.business_unit FROM contacts c WHERE c.id = relationship_profiles.contact_id
                )
                WHERE contact_id IN (SELECT id FROM contacts)
                """
            )

        if not self._has_column("voice_events", "details_json"):
            self.conn.execute("ALTER TABLE voice_events ADD COLUMN details_json TEXT")

        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_contact_from_external(
        self,
        business_unit: str,
        full_name: str,
        primary_email: str | None,
        company: str | None,
        source_of_truth: str,
        provider: str,
        external_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        cur = self.conn.cursor()
        if primary_email:
            cur.execute(
                """
                SELECT id FROM contacts
                WHERE business_unit = ? AND lower(primary_email) = lower(?)
                LIMIT 1
                """,
                (business_unit, primary_email),
            )
            row = cur.fetchone()
        else:
            row = None

        if row:
            contact_id = int(row["id"])
            cur.execute(
                """
                UPDATE contacts
                SET full_name = COALESCE(?, full_name),
                    company = COALESCE(?, company),
                    source_of_truth = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (full_name, company, source_of_truth, contact_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO contacts (business_unit, source_of_truth, full_name, primary_email, company)
                VALUES (?, ?, ?, ?, ?)
                """,
                (business_unit, source_of_truth, full_name, primary_email, company),
            )
            contact_id = int(cur.lastrowid)

        cur.execute(
            """
            INSERT INTO contact_identities (contact_id, business_unit, source, external_id, is_primary)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(source, external_id)
            DO UPDATE SET
              contact_id = excluded.contact_id,
              business_unit = excluded.business_unit,
              is_primary = 1
            """,
            (contact_id, business_unit, provider, external_id),
        )

        cur.execute(
            """
            INSERT INTO external_links (contact_id, business_unit, provider, external_ref, metadata_json, last_synced_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(provider, external_ref)
            DO UPDATE SET
              contact_id = excluded.contact_id,
              metadata_json = excluded.metadata_json,
              business_unit = excluded.business_unit,
              last_synced_at = datetime('now'),
              updated_at = datetime('now')
            """,
            (
                contact_id,
                business_unit,
                provider,
                external_id,
                json.dumps(metadata or {}, ensure_ascii=True),
            ),
        )
        self.conn.commit()
        return contact_id

    def add_billing_link(
        self,
        business_unit: str,
        provider: str,
        external_ref: str,
        metadata: dict[str, Any],
        contact_id: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO external_links (contact_id, business_unit, provider, external_ref, metadata_json, last_synced_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(provider, external_ref)
            DO UPDATE SET
              contact_id = COALESCE(excluded.contact_id, external_links.contact_id),
              business_unit = excluded.business_unit,
              metadata_json = excluded.metadata_json,
              last_synced_at = datetime('now'),
              updated_at = datetime('now')
            """,
            (
                contact_id,
                business_unit,
                provider,
                external_ref,
                json.dumps(metadata, ensure_ascii=True),
            ),
        )
        self.conn.commit()

    def add_voice_event(
        self,
        business_unit: str,
        intent: str,
        transcript: str | None,
        confidence: float | None,
        latency_ms: int | None,
        status: str,
        idempotency_key: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO voice_events
            (business_unit, intent, transcript, confidence, latency_ms, status, details_json, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key)
            DO UPDATE SET
              status = excluded.status,
              latency_ms = excluded.latency_ms,
              details_json = COALESCE(excluded.details_json, voice_events.details_json)
            """,
            (
                business_unit,
                intent,
                transcript,
                confidence,
                latency_ms,
                status,
                json.dumps(details, ensure_ascii=True) if details else None,
                idempotency_key,
            ),
        )
        self.conn.commit()

    def add_follow_up(
        self,
        business_unit: str,
        notes: str,
        due_at: str | None = None,
        contact_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO follow_ups (contact_id, business_unit, status, due_at, notes, idempotency_key)
            VALUES (?, ?, 'open', ?, ?, ?)
            ON CONFLICT(idempotency_key)
            DO UPDATE SET
              notes = excluded.notes,
              due_at = COALESCE(excluded.due_at, follow_ups.due_at),
              updated_at = datetime('now')
            """,
            (contact_id, business_unit, due_at, notes, idempotency_key),
        )
        follow_up_id = int(cur.lastrowid or 0)
        if follow_up_id == 0 and idempotency_key:
            cur.execute("SELECT id FROM follow_ups WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            follow_up_id = int(row["id"]) if row else 0
        self.conn.commit()
        return follow_up_id

    def get_unified_contact(self, contact_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
        contact = cur.fetchone()
        if not contact:
            return None

        cur.execute(
            "SELECT source, external_id, is_primary, business_unit FROM contact_identities WHERE contact_id = ?",
            (contact_id,),
        )
        identities = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT provider, external_ref, metadata_json, last_synced_at FROM external_links WHERE contact_id = ?",
            (contact_id,),
        )
        links = []
        for row in cur.fetchall():
            item = dict(row)
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item.pop("metadata_json"))
                except json.JSONDecodeError:
                    item["metadata"] = {"raw": item.pop("metadata_json")}
            links.append(item)

        cur.execute(
            """
            SELECT business_unit, relationship_strength, engagement_score, close_probability, summary, updated_at
            FROM relationship_profiles
            WHERE contact_id = ?
            """,
            (contact_id,),
        )
        profile = cur.fetchone()

        cur.execute(
            """
            SELECT source, direction, content, created_at
            FROM interactions
            WHERE contact_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (contact_id,),
        )
        interactions = [dict(r) for r in cur.fetchall()]

        return {
            "contact": dict(contact),
            "identities": identities,
            "external_links": links,
            "relationship_profile": dict(profile) if profile else None,
            "recent_interactions": interactions,
        }

    def search_contacts(
        self, query: str, business_unit: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        q = f"%{query.strip().lower()}%"
        cur = self.conn.cursor()
        if business_unit in {"CC", "ACS"}:
            cur.execute(
                """
                SELECT id, business_unit, full_name, primary_email, company, updated_at
                FROM contacts
                WHERE business_unit = ?
                  AND (
                    lower(full_name) LIKE ?
                    OR lower(COALESCE(primary_email, '')) LIKE ?
                    OR lower(COALESCE(company, '')) LIKE ?
                  )
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (business_unit, q, q, q, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, business_unit, full_name, primary_email, company, updated_at
                FROM contacts
                WHERE
                  lower(full_name) LIKE ?
                  OR lower(COALESCE(primary_email, '')) LIKE ?
                  OR lower(COALESCE(company, '')) LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (q, q, q, limit),
            )
        return [dict(r) for r in cur.fetchall()]

    def get_billing_snapshot(self, business_unit: str) -> dict[str, Any]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT provider, external_ref, metadata_json, last_synced_at
            FROM external_links
            WHERE business_unit = ?
              AND provider IN ('wix_quote', 'wix_invoice')
            ORDER BY last_synced_at DESC
            LIMIT 20
            """,
            (business_unit,),
        )
        items = []
        for row in cur.fetchall():
            item = dict(row)
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item.pop("metadata_json"))
                except json.JSONDecodeError:
                    item["metadata"] = {"raw": item.pop("metadata_json")}
            items.append(item)
        return {
            "business_unit": business_unit,
            "items": items,
            "count": len(items),
            "last_sync_at": items[0]["last_synced_at"] if items else None,
        }

    def daily_brief(self, business_unit: str | None = None) -> dict[str, Any]:
        cur = self.conn.cursor()
        params: tuple[Any, ...]
        contact_clause = ""
        follow_up_clause = ""
        if business_unit in {"CC", "ACS"}:
            contact_clause = "WHERE business_unit = ?"
            follow_up_clause = "WHERE business_unit = ? AND status = 'open'"
            params = (business_unit,)
        else:
            follow_up_clause = "WHERE status = 'open'"
            params = ()

        cur.execute(f"SELECT count(*) as c FROM contacts {contact_clause}", params)
        contacts_total = int(cur.fetchone()["c"])

        cur.execute(f"SELECT count(*) as c FROM follow_ups {follow_up_clause}", params)
        open_follow_ups = int(cur.fetchone()["c"])

        return {
            "business_unit": business_unit,
            "contacts_total": contacts_total,
            "open_follow_ups": open_follow_ups,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    def begin_ingestion_job(
        self,
        provider: str,
        business_unit: str,
        job_type: str,
        idempotency_key: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            self.conn.execute(
                """
                INSERT INTO ingestion_jobs
                (provider, business_unit, job_type, idempotency_key, status, details_json)
                VALUES (?, ?, ?, ?, 'started', ?)
                """,
                (
                    provider,
                    business_unit,
                    job_type,
                    idempotency_key,
                    json.dumps(details, ensure_ascii=True) if details else None,
                ),
            )
            self.conn.commit()
            return {"accepted": True, "status": "started", "idempotency_key": idempotency_key}
        except sqlite3.IntegrityError:
            cur = self.conn.execute(
                """
                SELECT status, details_json, updated_at
                FROM ingestion_jobs
                WHERE provider = ? AND business_unit = ? AND job_type = ? AND idempotency_key = ?
                """,
                (provider, business_unit, job_type, idempotency_key),
            )
            row = cur.fetchone()
            payload = {
                "accepted": False,
                "status": row["status"] if row else "duplicate",
                "idempotency_key": idempotency_key,
                "duplicate": True,
            }
            if row and row["details_json"]:
                try:
                    payload["details"] = json.loads(row["details_json"])
                except json.JSONDecodeError:
                    payload["details"] = {"raw": row["details_json"]}
            return payload

    def finalize_ingestion_job(
        self,
        provider: str,
        business_unit: str,
        job_type: str,
        idempotency_key: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, details_json = ?, updated_at = datetime('now')
            WHERE provider = ? AND business_unit = ? AND job_type = ? AND idempotency_key = ?
            """,
            (
                status,
                json.dumps(details, ensure_ascii=True) if details else None,
                provider,
                business_unit,
                job_type,
                idempotency_key,
            ),
        )
        self.conn.commit()

    def record_x_spend(self, amount_usd: float, cap_usd: float) -> dict[str, Any]:
        billing_period = datetime.utcnow().strftime("%Y-%m")
        self.conn.execute(
            """
            INSERT INTO integration_usage (provider, billing_period, spend_usd, cap_usd, enabled)
            VALUES ('x', ?, ?, ?, 1)
            ON CONFLICT(provider, billing_period)
            DO UPDATE SET
              spend_usd = integration_usage.spend_usd + excluded.spend_usd,
              cap_usd = excluded.cap_usd,
              updated_at = datetime('now')
            """,
            (billing_period, amount_usd, cap_usd),
        )
        self.conn.commit()
        return self.get_x_usage(cap_usd)

    def set_x_enabled(self, enabled: bool, cap_usd: float = 0.0) -> None:
        billing_period = datetime.utcnow().strftime("%Y-%m")
        self.conn.execute(
            """
            INSERT INTO integration_usage (provider, billing_period, spend_usd, cap_usd, enabled)
            VALUES ('x', ?, 0, ?, ?)
            ON CONFLICT(provider, billing_period)
            DO UPDATE SET enabled = excluded.enabled, updated_at = datetime('now')
            """,
            (billing_period, cap_usd, 1 if enabled else 0),
        )
        self.conn.commit()

    def get_x_usage(self, default_cap: float) -> dict[str, Any]:
        billing_period = datetime.utcnow().strftime("%Y-%m")
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT spend_usd, cap_usd, enabled, updated_at
            FROM integration_usage
            WHERE provider = 'x' AND billing_period = ?
            """,
            (billing_period,),
        )
        row = cur.fetchone()
        if not row:
            return {
                "provider": "x",
                "billing_period": billing_period,
                "spend_usd": 0.0,
                "cap_usd": float(default_cap),
                "enabled": True,
                "ratio": 0.0,
                "status": "ok",
                "warning_triggered": False,
                "cap_reached": False,
            }

        spend = float(row["spend_usd"])
        cap = float(row["cap_usd"] or default_cap)
        ratio = (spend / cap) if cap > 0 else 0.0
        status = "ok"
        if ratio >= 1.0:
            status = "cap_reached"
        elif ratio >= 0.8:
            status = "warning"

        return {
            "provider": "x",
            "billing_period": billing_period,
            "spend_usd": spend,
            "cap_usd": cap,
            "enabled": bool(row["enabled"]),
            "ratio": ratio,
            "status": status,
            "warning_triggered": ratio >= 0.8,
            "cap_reached": ratio >= 1.0,
            "updated_at": row["updated_at"],
        }

    def upsert_sync_cursor(
        self, provider: str, business_unit: str, cursor: str | None, status: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_cursors (provider, business_unit, cursor, last_sync_at, status)
            VALUES (?, ?, ?, datetime('now'), ?)
            ON CONFLICT(provider, business_unit)
            DO UPDATE SET
              cursor = excluded.cursor,
              last_sync_at = datetime('now'),
              status = excluded.status,
              updated_at = datetime('now')
            """,
            (provider, business_unit, cursor, status),
        )
        self.conn.commit()

    def add_admin_audit_log(
        self,
        provider: str,
        delegated_subject: str,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO admin_audit_logs (provider, delegated_subject, action, status, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                provider,
                delegated_subject,
                action,
                status,
                json.dumps(details, ensure_ascii=True) if details else None,
            ),
        )
        self.conn.commit()

    def create_action_approval(
        self,
        business_unit: str,
        action_type: str,
        payload: dict[str, Any],
        state: str = "proposed",
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO action_approvals (business_unit, action_type, payload_json, state)
            VALUES (?, ?, ?, ?)
            """,
            (business_unit, action_type, json.dumps(payload, ensure_ascii=True), state),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_action_approval(self, approval_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, business_unit, action_type, payload_json, state, created_at, updated_at
            FROM action_approvals
            WHERE id = ?
            """,
            (approval_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        out = dict(row)
        payload = out.get("payload_json")
        if payload:
            try:
                out["payload"] = json.loads(payload)
            except json.JSONDecodeError:
                out["payload"] = {"raw": payload}
        return out

    def set_action_approval_state(
        self,
        approval_id: int,
        state: str,
    ) -> dict[str, Any] | None:
        self.conn.execute(
            """
            UPDATE action_approvals
            SET state = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (state, approval_id),
        )
        self.conn.commit()
        return self.get_action_approval(approval_id)

    def upsert_learning_source(
        self,
        business_unit: str,
        source_type: str,
        source_ref: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        active: bool = True,
    ) -> int:
        self.conn.execute(
            """
            INSERT INTO learning_sources
            (business_unit, source_type, source_ref, title, metadata_json, active)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(business_unit, source_type, source_ref)
            DO UPDATE SET
              title = COALESCE(excluded.title, learning_sources.title),
              metadata_json = COALESCE(excluded.metadata_json, learning_sources.metadata_json),
              active = excluded.active,
              updated_at = datetime('now')
            """,
            (
                business_unit,
                source_type,
                source_ref,
                title,
                json.dumps(metadata, ensure_ascii=True) if metadata else None,
                1 if active else 0,
            ),
        )
        self.conn.commit()
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id FROM learning_sources
            WHERE business_unit = ? AND source_type = ? AND source_ref = ?
            """,
            (business_unit, source_type, source_ref),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else 0

    def add_learning_item(
        self,
        business_unit: str,
        source_type: str,
        title: str,
        source_ref: str | None = None,
        url: str | None = None,
        published_at: str | None = None,
        transcript_text: str | None = None,
        summary_text: str | None = None,
        relevance_score: float = 0.0,
        tags: list[str] | None = None,
        idempotency_key: str | None = None,
        source_id: int | None = None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO learning_items
            (business_unit, source_id, source_type, source_ref, title, url, published_at, transcript_text, summary_text,
             relevance_score, tags_json, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key)
            DO UPDATE SET
              source_id = COALESCE(excluded.source_id, learning_items.source_id),
              source_ref = COALESCE(excluded.source_ref, learning_items.source_ref),
              title = excluded.title,
              url = COALESCE(excluded.url, learning_items.url),
              published_at = COALESCE(excluded.published_at, learning_items.published_at),
              transcript_text = COALESCE(excluded.transcript_text, learning_items.transcript_text),
              summary_text = COALESCE(excluded.summary_text, learning_items.summary_text),
              relevance_score = excluded.relevance_score,
              tags_json = COALESCE(excluded.tags_json, learning_items.tags_json),
              updated_at = datetime('now')
            """,
            (
                business_unit,
                source_id,
                source_type,
                source_ref,
                title,
                url,
                published_at,
                transcript_text,
                summary_text,
                float(relevance_score),
                json.dumps(tags or [], ensure_ascii=True),
                idempotency_key,
            ),
        )
        learning_item_id = int(cur.lastrowid or 0)
        if learning_item_id == 0 and idempotency_key:
            cur.execute("SELECT id FROM learning_items WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            learning_item_id = int(row["id"]) if row else 0
        self.conn.commit()
        return learning_item_id

    def add_learning_insight(
        self,
        business_unit: str,
        insight_type: str,
        title: str,
        insight_text: str,
        confidence: float = 0.5,
        priority: int = 3,
        learning_item_id: int | None = None,
        contact_id: int | None = None,
        tags: list[str] | None = None,
        status: str = "active",
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO learning_insights
            (business_unit, learning_item_id, contact_id, insight_type, title, insight_text, confidence, priority, status, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business_unit,
                learning_item_id,
                contact_id,
                insight_type,
                title,
                insight_text,
                float(confidence),
                int(priority),
                status,
                json.dumps(tags or [], ensure_ascii=True),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_learning_digest(
        self,
        business_unit: str,
        limit: int = 20,
        tag: str | None = None,
    ) -> dict[str, Any]:
        cur = self.conn.cursor()
        sql = """
            SELECT i.id, i.learning_item_id, i.contact_id, i.insight_type, i.title, i.insight_text,
                   i.confidence, i.priority, i.status, i.tags_json, i.created_at,
                   li.title AS learning_title, li.url AS learning_url, li.source_type, li.source_ref
            FROM learning_insights i
            LEFT JOIN learning_items li ON li.id = i.learning_item_id
            WHERE i.business_unit = ? AND i.status = 'active'
        """
        params: list[Any] = [business_unit]
        if tag:
            sql += " AND lower(COALESCE(i.tags_json, '')) LIKE ?"
            params.append(f"%{tag.strip().lower()}%")
        sql += " ORDER BY i.priority ASC, i.created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        cur.execute(sql, tuple(params))

        insights: list[dict[str, Any]] = []
        for row in cur.fetchall():
            item = dict(row)
            raw_tags = item.get("tags_json")
            if raw_tags:
                try:
                    item["tags"] = json.loads(raw_tags)
                except json.JSONDecodeError:
                    item["tags"] = []
            insights.append(item)

        return {
            "business_unit": business_unit,
            "count": len(insights),
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "insights": insights,
        }

    def search_learning_knowledge(
        self,
        query: str,
        business_unit: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        q = f"%{query.strip().lower()}%"
        bounded_limit = max(1, min(limit, 100))
        cur = self.conn.cursor()

        if business_unit in {"CC", "ACS"}:
            cur.execute(
                """
                SELECT id, business_unit, source_type, source_ref, title, url, published_at, summary_text, relevance_score, tags_json
                FROM learning_items
                WHERE business_unit = ?
                  AND (
                    lower(title) LIKE ?
                    OR lower(COALESCE(summary_text, '')) LIKE ?
                    OR lower(COALESCE(transcript_text, '')) LIKE ?
                  )
                ORDER BY relevance_score DESC, created_at DESC
                LIMIT ?
                """,
                (business_unit, q, q, q, bounded_limit),
            )
            items_rows = cur.fetchall()
            cur.execute(
                """
                SELECT id, business_unit, learning_item_id, contact_id, insight_type, title, insight_text, confidence, priority, status, tags_json
                FROM learning_insights
                WHERE business_unit = ?
                  AND (
                    lower(title) LIKE ?
                    OR lower(insight_text) LIKE ?
                    OR lower(COALESCE(tags_json, '')) LIKE ?
                  )
                ORDER BY priority ASC, confidence DESC, created_at DESC
                LIMIT ?
                """,
                (business_unit, q, q, q, bounded_limit),
            )
            insights_rows = cur.fetchall()
        else:
            cur.execute(
                """
                SELECT id, business_unit, source_type, source_ref, title, url, published_at, summary_text, relevance_score, tags_json
                FROM learning_items
                WHERE
                  lower(title) LIKE ?
                  OR lower(COALESCE(summary_text, '')) LIKE ?
                  OR lower(COALESCE(transcript_text, '')) LIKE ?
                ORDER BY relevance_score DESC, created_at DESC
                LIMIT ?
                """,
                (q, q, q, bounded_limit),
            )
            items_rows = cur.fetchall()
            cur.execute(
                """
                SELECT id, business_unit, learning_item_id, contact_id, insight_type, title, insight_text, confidence, priority, status, tags_json
                FROM learning_insights
                WHERE
                  lower(title) LIKE ?
                  OR lower(insight_text) LIKE ?
                  OR lower(COALESCE(tags_json, '')) LIKE ?
                ORDER BY priority ASC, confidence DESC, created_at DESC
                LIMIT ?
                """,
                (q, q, q, bounded_limit),
            )
            insights_rows = cur.fetchall()
            return {
                "query": query,
                "business_unit": business_unit,
                "items": [dict(r) for r in items_rows],
                "insights": [dict(r) for r in insights_rows],
            }
        return {
            "query": query,
            "business_unit": business_unit,
            "items": [dict(r) for r in items_rows],
            "insights": [dict(r) for r in insights_rows],
        }

    def create_outreach_draft(
        self,
        business_unit: str,
        channel: str,
        recipient: str,
        body_text: str,
        subject: str | None = None,
        rationale: str | None = None,
        contact_id: int | None = None,
        source_insight_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "business_unit": business_unit,
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "body_text": body_text,
            "rationale": rationale,
            "contact_id": contact_id,
            "source_insight_ids": source_insight_ids or [],
        }
        approval_id = self.create_action_approval(
            business_unit=business_unit,
            action_type="outreach_send",
            payload=payload,
            state="proposed",
        )
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO outreach_drafts
            (business_unit, contact_id, channel, recipient, subject, body_text, rationale, source_insight_ids_json, status, approval_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
            """,
            (
                business_unit,
                contact_id,
                channel,
                recipient,
                subject,
                body_text,
                rationale,
                json.dumps(source_insight_ids or [], ensure_ascii=True),
                approval_id,
            ),
        )
        draft_id = int(cur.lastrowid)
        self.conn.commit()
        return {"draft_id": draft_id, "approval_id": approval_id}

    def sync_outreach_draft_approval(self, approval_id: int, approval_state: str) -> None:
        mapped = approval_state if approval_state in {"proposed", "approved", "rejected", "executed"} else "proposed"
        self.conn.execute(
            """
            UPDATE outreach_drafts
            SET status = ?, updated_at = datetime('now')
            WHERE approval_id = ?
            """,
            (mapped, approval_id),
        )
        self.conn.commit()

    def list_outreach_drafts(
        self,
        business_unit: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        if status:
            cur.execute(
                """
                SELECT id, business_unit, contact_id, channel, recipient, subject, body_text, rationale,
                       source_insight_ids_json, status, approval_id, created_at, updated_at
                FROM outreach_drafts
                WHERE business_unit = ? AND status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (business_unit, status, max(1, min(limit, 200))),
            )
        else:
            cur.execute(
                """
                SELECT id, business_unit, contact_id, channel, recipient, subject, body_text, rationale,
                       source_insight_ids_json, status, approval_id, created_at, updated_at
                FROM outreach_drafts
                WHERE business_unit = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (business_unit, max(1, min(limit, 200))),
            )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            raw_ids = item.get("source_insight_ids_json")
            if raw_ids:
                try:
                    item["source_insight_ids"] = json.loads(raw_ids)
                except json.JSONDecodeError:
                    item["source_insight_ids"] = []
            rows.append(item)
        return rows

    def enqueue_work_item(
        self,
        queue: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        business_unit: str | None = None,
        idempotency_key: str | None = None,
        priority: int = 100,
        created_by: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        if not queue.strip():
            raise ValueError("queue is required")
        if not task_type.strip():
            raise ValueError("task_type is required")
        if business_unit is not None and business_unit not in {"CC", "ACS"}:
            raise ValueError("business_unit must be CC or ACS when provided")

        try:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO work_items
                (queue, task_type, business_unit, status, priority, payload_json, idempotency_key, created_by, max_attempts)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    queue.strip(),
                    task_type.strip(),
                    business_unit,
                    int(priority),
                    json.dumps(payload or {}, ensure_ascii=True),
                    idempotency_key,
                    created_by,
                    max(1, int(max_attempts)),
                ),
            )
            self.conn.commit()
            return {"accepted": True, "duplicate": False, "work_item_id": int(cur.lastrowid)}
        except sqlite3.IntegrityError:
            if not idempotency_key:
                # Most likely a different constraint; surface as generic duplicate.
                return {"accepted": False, "duplicate": True, "work_item_id": None}
            cur = self.conn.execute("SELECT id, status FROM work_items WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            return {
                "accepted": False,
                "duplicate": True,
                "work_item_id": int(row["id"]) if row else None,
                "status": row["status"] if row else "unknown",
            }

    def get_work_item(self, work_item_id: int) -> dict[str, Any] | None:
        cur = self.conn.execute(
            """
            SELECT id, queue, task_type, business_unit, status, priority, payload_json, result_json, error_text,
                   attempts, max_attempts, idempotency_key, created_by, claimed_by, claimed_at, claim_expires_at,
                   created_at, updated_at
            FROM work_items
            WHERE id = ?
            """,
            (int(work_item_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        item = dict(row)
        for key in ("payload_json", "result_json"):
            raw = item.get(key)
            if raw:
                try:
                    item[key.replace("_json", "")] = json.loads(raw)
                except json.JSONDecodeError:
                    item[key.replace("_json", "")] = {"raw": raw}
        return item

    def list_work_items(
        self,
        *,
        queue: str | None = None,
        status: str | None = None,
        business_unit: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT id, queue, task_type, business_unit, status, priority, payload_json, result_json, error_text,
                   attempts, max_attempts, idempotency_key, created_by, claimed_by, claimed_at, claim_expires_at,
                   created_at, updated_at
            FROM work_items
            WHERE 1=1
        """
        params: list[Any] = []
        if queue:
            sql += " AND queue = ?"
            params.append(queue)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if business_unit in {"CC", "ACS"}:
            sql += " AND business_unit = ?"
            params.append(business_unit)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))

        cur = self.conn.execute(sql, tuple(params))
        out: list[dict[str, Any]] = []
        for row in cur.fetchall():
            item = dict(row)
            raw_payload = item.get("payload_json")
            if raw_payload:
                try:
                    item["payload"] = json.loads(raw_payload)
                except json.JSONDecodeError:
                    item["payload"] = {"raw": raw_payload}
            raw_result = item.get("result_json")
            if raw_result:
                try:
                    item["result"] = json.loads(raw_result)
                except json.JSONDecodeError:
                    item["result"] = {"raw": raw_result}
            out.append(item)
        return out

    def claim_work_items(
        self,
        *,
        worker_id: str,
        queues: list[str] | None = None,
        limit: int = 1,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        if not worker_id.strip():
            raise ValueError("worker_id is required")

        bounded_limit = max(1, min(int(limit), 50))
        bounded_lease = max(30, min(int(lease_seconds), 3600))

        queue_clause = ""
        params: list[Any] = []
        if queues:
            clean_queues = [q.strip() for q in queues if str(q or "").strip()]
            if clean_queues:
                queue_clause = " AND queue IN ({})".format(",".join(["?"] * len(clean_queues)))
                params.extend(clean_queues)

        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                f"""
                SELECT id
                FROM work_items
                WHERE
                  (status = 'queued'
                   OR (status = 'claimed' AND claim_expires_at IS NOT NULL AND claim_expires_at < datetime('now')))
                  AND attempts < max_attempts
                  {queue_clause}
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
                """,
                tuple(params + [bounded_limit]),
            )
            ids = [int(r["id"]) for r in cur.fetchall()]
            if not ids:
                self.conn.commit()
                return []

            claimed: list[int] = []
            for work_item_id in ids:
                cur.execute(
                    """
                    UPDATE work_items
                    SET status = 'claimed',
                        claimed_by = ?,
                        claimed_at = datetime('now'),
                        claim_expires_at = datetime('now', ?),
                        attempts = attempts + 1,
                        updated_at = datetime('now')
                    WHERE id = ?
                      AND (
                        status = 'queued'
                        OR (status = 'claimed' AND claim_expires_at IS NOT NULL AND claim_expires_at < datetime('now'))
                      )
                      AND attempts < max_attempts
                    """,
                    (worker_id.strip(), f"+{bounded_lease} seconds", work_item_id),
                )
                if cur.rowcount:
                    claimed.append(work_item_id)

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        results: list[dict[str, Any]] = []
        for work_item_id in claimed:
            item = self.get_work_item(work_item_id)
            if item:
                results.append(item)
        return results

    def complete_work_item(
        self,
        *,
        work_item_id: int,
        worker_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        cur = self.conn.execute(
            """
            UPDATE work_items
            SET status = 'succeeded',
                result_json = ?,
                error_text = NULL,
                updated_at = datetime('now')
            WHERE id = ?
              AND status = 'claimed'
              AND claimed_by = ?
            """,
            (json.dumps(result or {}, ensure_ascii=True), int(work_item_id), worker_id.strip()),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def fail_work_item(
        self,
        *,
        work_item_id: int,
        worker_id: str,
        error_text: str,
        error: dict[str, Any] | None = None,
    ) -> bool:
        # Keep error JSON inside result_json for compactness and a single payload field.
        payload = {"ok": False, "error": error or {}, "error_text": error_text}
        cur = self.conn.execute(
            """
            UPDATE work_items
            SET status = 'failed',
                result_json = ?,
                error_text = ?,
                updated_at = datetime('now')
            WHERE id = ?
              AND status = 'claimed'
              AND claimed_by = ?
            """,
            (
                json.dumps(payload, ensure_ascii=True),
                (error_text or "")[:2000],
                int(work_item_id),
                worker_id.strip(),
            ),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def add_interaction(
        self,
        business_unit: str,
        source: str,
        direction: str | None,
        content: str,
        idempotency_key: str,
        contact_id: int | None = None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO interactions (contact_id, business_unit, source, direction, content, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key)
            DO UPDATE SET
              content = excluded.content
            """,
            (contact_id, business_unit, source, direction, content, idempotency_key),
        )
        interaction_id = int(cur.lastrowid or 0)
        if interaction_id == 0:
            cur.execute("SELECT id FROM interactions WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            interaction_id = int(row["id"]) if row else 0
        self.conn.commit()
        return interaction_id

    def upsert_message_thread(
        self,
        business_unit: str,
        source: str,
        external_thread_id: str,
        latest_message_at: str | None,
        participants: list[str],
        message_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO message_threads
            (business_unit, source, external_thread_id, latest_message_at, participants_json, message_count, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_thread_id)
            DO UPDATE SET
              business_unit = excluded.business_unit,
              latest_message_at = COALESCE(excluded.latest_message_at, message_threads.latest_message_at),
              participants_json = excluded.participants_json,
              message_count = excluded.message_count,
              metadata_json = excluded.metadata_json,
              updated_at = datetime('now')
            """,
            (
                business_unit,
                source,
                external_thread_id,
                latest_message_at,
                json.dumps(participants, ensure_ascii=True),
                message_count,
                json.dumps(metadata or {}, ensure_ascii=True),
            ),
        )
        self.conn.commit()

    def _upsert_acs_client(
        self,
        full_name: str,
        phone: str | None,
        email: str | None,
    ) -> int:
        cur = self.conn.cursor()
        if email:
            cur.execute(
                """
                SELECT id FROM acs_clients
                WHERE lower(COALESCE(email, '')) = lower(?)
                LIMIT 1
                """,
                (email,),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])

        cur.execute(
            """
            INSERT INTO acs_clients (business_unit, full_name, phone, email)
            VALUES ('ACS', ?, ?, ?)
            """,
            (full_name, phone, email),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _upsert_acs_property(
        self,
        client_id: int,
        address_line1: str,
        city: str | None,
        state: str | None,
        postal_code: str | None,
        lat: float | None,
        lon: float | None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id
            FROM acs_properties
            WHERE client_id = ? AND lower(address_line1) = lower(?)
            LIMIT 1
            """,
            (client_id, address_line1),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])

        cur.execute(
            """
            INSERT INTO acs_properties
            (business_unit, client_id, address_line1, city, state, postal_code, lat, lon)
            VALUES ('ACS', ?, ?, ?, ?, ?, ?, ?)
            """,
            (client_id, address_line1, city, state, postal_code, lat, lon),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def create_acs_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_name = (payload.get("client_name") or "").strip()
        address_line1 = (payload.get("address_line1") or "").strip()
        title = (payload.get("title") or "").strip() or "Cleaning Job"
        if not client_name:
            raise ValueError("client_name is required")
        if not address_line1:
            raise ValueError("address_line1 is required")

        client_id = self._upsert_acs_client(
            full_name=client_name,
            phone=(payload.get("client_phone") or "").strip() or None,
            email=(payload.get("client_email") or "").strip() or None,
        )
        property_id = self._upsert_acs_property(
            client_id=client_id,
            address_line1=address_line1,
            city=(payload.get("city") or "").strip() or None,
            state=(payload.get("state") or "").strip() or None,
            postal_code=(payload.get("postal_code") or "").strip() or None,
            lat=float(payload["lat"]) if payload.get("lat") is not None else None,
            lon=float(payload["lon"]) if payload.get("lon") is not None else None,
        )

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO acs_jobs
            (business_unit, client_id, property_id, title, status, scheduled_start_at, scheduled_end_at, recurrence_rule, notes)
            VALUES ('ACS', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                property_id,
                title,
                (payload.get("status") or "scheduled").strip(),
                payload.get("scheduled_start_at"),
                payload.get("scheduled_end_at"),
                payload.get("recurrence_rule"),
                payload.get("notes"),
            ),
        )
        job_id = int(cur.lastrowid)

        if payload.get("geofence_radius_meters") and payload.get("lat") is not None and payload.get("lon") is not None:
            cur.execute(
                """
                INSERT INTO job_geofences (business_unit, job_id, center_lat, center_lon, radius_meters)
                VALUES ('ACS', ?, ?, ?, ?)
                """,
                (job_id, float(payload["lat"]), float(payload["lon"]), float(payload["geofence_radius_meters"])),
            )
        self.conn.commit()
        return self.get_acs_job(job_id) or {"id": job_id}

    def assign_acs_job(self, job_id: int, crew_member_name: str, role: str | None = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO acs_job_assignments (business_unit, job_id, crew_member_name, role, assignment_status)
            VALUES ('ACS', ?, ?, ?, 'assigned')
            """,
            (job_id, crew_member_name, role),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_acs_job_status(self, job_id: int, status: str) -> dict[str, Any] | None:
        self.conn.execute(
            """
            UPDATE acs_jobs
            SET status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (status, job_id),
        )
        self.conn.commit()
        return self.get_acs_job(job_id)

    def get_acs_job(self, job_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT j.*, c.full_name AS client_name, c.phone AS client_phone, c.email AS client_email,
                   p.address_line1, p.city, p.state, p.postal_code, p.lat, p.lon
            FROM acs_jobs j
            JOIN acs_clients c ON c.id = j.client_id
            JOIN acs_properties p ON p.id = j.property_id
            WHERE j.id = ?
            """,
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        out = dict(row)
        cur.execute(
            """
            SELECT id, crew_member_name, role, assignment_status, created_at
            FROM acs_job_assignments
            WHERE job_id = ?
            ORDER BY created_at ASC
            """,
            (job_id,),
        )
        out["assignments"] = [dict(r) for r in cur.fetchall()]
        return out

    def list_recent_message_threads(
        self,
        business_unit: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        if business_unit in {"CC", "ACS"}:
            cur.execute(
                """
                SELECT id, business_unit, source, external_thread_id, latest_message_at, participants_json, message_count
                FROM message_threads
                WHERE business_unit = ?
                ORDER BY COALESCE(latest_message_at, updated_at) DESC
                LIMIT ?
                """,
                (business_unit, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, business_unit, source, external_thread_id, latest_message_at, participants_json, message_count
                FROM message_threads
                ORDER BY COALESCE(latest_message_at, updated_at) DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            raw = item.get("participants_json")
            if raw:
                try:
                    item["participants"] = json.loads(raw)
                except json.JSONDecodeError:
                    item["participants"] = []
            rows.append(item)
        return rows

    def build_acs_reminder_preview(self, lead_minutes: int, now_iso: str | None = None) -> list[dict[str, Any]]:
        now = now_iso or (datetime.utcnow().isoformat(timespec="seconds") + "Z")
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT j.id, j.title, j.scheduled_start_at, c.full_name AS client_name, c.phone AS client_phone
            FROM acs_jobs j
            JOIN acs_clients c ON c.id = j.client_id
            WHERE j.status IN ('scheduled', 'confirmed')
              AND j.scheduled_start_at IS NOT NULL
            ORDER BY j.scheduled_start_at ASC
            LIMIT 100
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        reminders = []
        for row in rows:
            reminders.append(
                {
                    "job_id": row["id"],
                    "client_name": row["client_name"],
                    "client_phone": row["client_phone"],
                    "scheduled_start_at": row["scheduled_start_at"],
                    "lead_minutes": lead_minutes,
                    "draft_message": "Reminder: your cleaning is scheduled in {m} minutes.".format(m=lead_minutes),
                    "generated_at": now,
                }
            )
        return reminders

    def _haversine_meters(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        r = 6371000.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        d1 = math.radians(lat2 - lat1)
        d2 = math.radians(lon2 - lon1)
        a = math.sin(d1 / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d2 / 2) ** 2
        return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def ingest_crew_location_events(
        self,
        events: list[dict[str, Any]],
        provider: str = "traccar",
    ) -> dict[str, Any]:
        cur = self.conn.cursor()
        inserted = 0
        at_site = 0
        for event in events:
            device_external_id = str(event.get("device_id") or "").strip()
            if not device_external_id:
                continue
            observed_at = (event.get("observed_at") or "").strip() or datetime.utcnow().isoformat(timespec="seconds") + "Z"
            lat = float(event.get("lat"))
            lon = float(event.get("lon"))
            crew_member_name = (event.get("crew_member_name") or "").strip() or "Unknown Crew"

            cur.execute(
                """
                SELECT id FROM crew_devices WHERE provider = ? AND external_device_id = ? LIMIT 1
                """,
                (provider, device_external_id),
            )
            row = cur.fetchone()
            if row:
                device_id = int(row["id"])
            else:
                cur.execute(
                    """
                    INSERT INTO crew_members (business_unit, full_name) VALUES ('ACS', ?)
                    """,
                    (crew_member_name,),
                )
                crew_member_id = int(cur.lastrowid)
                cur.execute(
                    """
                    INSERT INTO crew_devices (business_unit, crew_member_id, provider, external_device_id)
                    VALUES ('ACS', ?, ?, ?)
                    """,
                    (crew_member_id, provider, device_external_id),
                )
                device_id = int(cur.lastrowid)

            cur.execute(
                """
                INSERT INTO crew_location_events
                (business_unit, crew_device_id, observed_at, lat, lon, speed_mps, heading, provider, raw_json)
                VALUES ('ACS', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    observed_at,
                    lat,
                    lon,
                    event.get("speed_mps"),
                    event.get("heading"),
                    provider,
                    json.dumps(event, ensure_ascii=True),
                ),
            )
            inserted += 1

            cur.execute(
                """
                SELECT g.center_lat, g.center_lon, g.radius_meters
                FROM job_geofences g
                JOIN acs_jobs j ON j.id = g.job_id
                WHERE j.status IN ('scheduled', 'in_progress', 'confirmed')
                """
            )
            for gf in cur.fetchall():
                dist = self._haversine_meters(lat, lon, float(gf["center_lat"]), float(gf["center_lon"]))
                if dist <= float(gf["radius_meters"]):
                    at_site += 1
                    break

        self.conn.commit()
        return {
            "provider": provider,
            "events_received": len(events),
            "events_ingested": inserted,
            "at_site_hits": at_site,
        }
