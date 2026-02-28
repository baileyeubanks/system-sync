#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import SETTINGS
from api.connectors.elevenlabs_connector import ElevenLabsConfig, ElevenLabsConnector
from api.connectors.google_connector import GoogleConfig, GoogleConnector
from api.connectors.imessage_connector import IMessageConfig, IMessageConnector
from api.connectors.wix_connector import WixConfig, WixConnector
from api.connectors.x_connector import XConfig, XConnector
from api.db import Database
from api.intent_router import route_intent
from api.path_guard import guard_runtime_paths
from api.system_blueprint import build_blueprint
from api.system_deployed import load_deployed_info
from api.system_ontology import build_system_ontology
from api.agent_auth import get_agent_id, resolve_business_unit, check_email_access

ROOT = Path(__file__).resolve().parents[1]
DB = Database(SETTINGS.db_path)
WIX = WixConnector(
    DB,
    WixConfig(
        enabled=SETTINGS.wix_sync_enabled,
        api_key=SETTINGS.wix_api_key,
        site_id=SETTINGS.wix_site_id,
        account_id=SETTINGS.wix_account_id,
    ),
)
ELEVEN = ElevenLabsConnector(
    ElevenLabsConfig(
        api_key=SETTINGS.elevenlabs_api_key,
        default_voice_id=SETTINGS.elevenlabs_default_voice_id,
        stt_model_id=SETTINGS.elevenlabs_stt_model_id,
        tts_model_id=SETTINGS.elevenlabs_tts_model_id,
    )
)
XAPI = XConnector(
    DB,
    XConfig(
        enabled=SETTINGS.x_api_enabled,
        bearer_token=SETTINGS.x_bearer_token,
        cap_usd=SETTINGS.x_monthly_spend_cap_usd,
        warning_ratio=SETTINGS.x_warning_ratio,
    ),
)
GOOGLE = GoogleConnector(
    GoogleConfig(
        oauth_access_token=SETTINGS.google_oauth_access_token,
        oauth_credentials_file=SETTINGS.google_oauth_credentials_file,
        oauth_token_file=SETTINGS.google_oauth_token_file,
        oauth_credentials_file_cc=SETTINGS.google_oauth_credentials_file_cc,
        oauth_token_file_cc=SETTINGS.google_oauth_token_file_cc,
        oauth_credentials_file_acs=SETTINGS.google_oauth_credentials_file_acs,
        oauth_token_file_acs=SETTINGS.google_oauth_token_file_acs,
        dwd_service_account_file=SETTINGS.google_dwd_service_account_file,
        dwd_impersonation_subject=SETTINGS.google_dwd_impersonation_subject,
        dwd_scopes=SETTINGS.google_dwd_scopes,
    ),
    db=DB,
)
IMESSAGE = IMessageConnector(
    DB,
    IMessageConfig(
        enabled=SETTINGS.imessage_enabled,
        export_root=SETTINGS.imessage_export_root,
        send_enabled_cc=SETTINGS.imessage_send_enabled_cc,
        send_enabled_acs=SETTINGS.imessage_send_enabled_acs,
        sender_user_cc=SETTINGS.imessage_sender_user_cc,
        sender_user_acs=SETTINGS.imessage_sender_user_acs,
        rate_limit_per_minute=SETTINGS.imessage_rate_limit_per_minute,
    ),
)


def _json(body: Any, status: int = 200) -> tuple[bytes, int]:
    return json.dumps(body, ensure_ascii=True).encode("utf-8"), status


def _is_business_unit(value: str | None) -> bool:
    return value in {"CC", "ACS"}


def _voice_router_response(transcript: str, business_unit: str, idempotency_key: str) -> dict[str, Any]:
    route = route_intent(transcript)
    intent = route.get("intent", "unknown")

    if intent == "contact_lookup":
        matches = DB.search_contacts(route.get("query", transcript), business_unit=business_unit, limit=5)
        return {"intent": intent, "matches": matches, "query": route.get("query")}

    if intent == "quote_status":
        # CC uses Wix mirror. ACS remains local-first and independent from Wix availability.
        target_bu = "CC" if business_unit == "CC" else "ACS"
        snapshot = DB.get_billing_snapshot(target_bu)
        return {"intent": intent, "billing": snapshot}

    if intent == "job_status":
        preview = DB.build_acs_reminder_preview(lead_minutes=30)
        return {"intent": intent, "acs_jobs_preview": preview[:5], "count": len(preview)}

    if intent == "follow_up_capture":
        follow_up_id = DB.add_follow_up(
            business_unit=business_unit,
            notes=transcript,
            idempotency_key=idempotency_key,
        )
        return {"intent": intent, "follow_up_id": follow_up_id}

    if intent == "daily_brief":
        brief = DB.daily_brief(business_unit=business_unit)
        return {"intent": intent, "daily_brief": brief}

    return {"intent": "unknown", "note": "No matching voice workflow found."}


class Handler(BaseHTTPRequestHandler):
    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _agent_id(self) -> str:
        return get_agent_id(self.headers)

    def _enforce_bu(self, requested_bu: str | None) -> tuple[str | None, bool]:
        """Resolve and enforce business_unit for current agent.
        Returns (business_unit, ok). Sends 403 and returns (None, False) if denied.
        """
        agent_id = self._agent_id()
        bu, ok, err = resolve_business_unit(agent_id, requested_bu)
        if not ok:
            self._send({"error": err, "agent_id": agent_id}, status=403)
            return None, False
        return bu, True

    def _send(self, body: Any, status: int = 200) -> None:
        payload, code = _json(body, status)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/health":
            self._send(
                {
                    "status": "ok",
                    "service": "blaze-v4-api",
                    "business_guardrails_enabled": SETTINGS.business_guardrails_enabled,
                    "runtime_role": SETTINGS.runtime_role,
                }
            )
            return

        m = re.match(r"^/api/contacts/unified/(\d+)$", route)
        if m:
            contact_id = int(m.group(1))
            contact = DB.get_unified_contact(contact_id)
            if not contact:
                self._send({"error": "not found"}, status=404)
                return
            self._send(contact)
            return

        if route == "/api/contacts/search":
            q = (query.get("q", [""])[0] or "").strip()
            if not q:
                self._send({"error": "q is required"}, status=400)
                return
            raw_bu = (query.get("business_unit", [None])[0] or None)
            if raw_bu and not _is_business_unit(raw_bu):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            business_unit, ok = self._enforce_bu(raw_bu)
            if not ok:
                return
            limit = int(query.get("limit", ["10"])[0])
            self._send(
                {
                    "query": q,
                    "results": DB.search_contacts(q, business_unit=business_unit, limit=max(1, min(50, limit))),
                }
            )
            return

        if route == "/api/learning/digest":
            business_unit = (query.get("business_unit", ["CC"])[0] or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            limit = int(query.get("limit", ["20"])[0] or 20)
            tag = (query.get("tag", [""])[0] or "").strip() or None
            self._send(DB.list_learning_digest(business_unit=business_unit, limit=limit, tag=tag))
            return

        if route == "/api/learning/search":
            q = (query.get("q", [""])[0] or "").strip()
            if not q:
                self._send({"error": "q is required"}, status=400)
                return
            business_unit = (query.get("business_unit", [""])[0] or "").upper() or None
            if business_unit and not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            limit = int(query.get("limit", ["20"])[0] or 20)
            self._send(DB.search_learning_knowledge(query=q, business_unit=business_unit, limit=limit))
            return

        if route == "/api/outreach/drafts":
            business_unit = (query.get("business_unit", ["CC"])[0] or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            status = (query.get("status", [""])[0] or "").strip() or None
            limit = int(query.get("limit", ["50"])[0] or 50)
            self._send(
                {
                    "business_unit": business_unit,
                    "results": DB.list_outreach_drafts(
                        business_unit=business_unit,
                        status=status,
                        limit=max(1, min(limit, 200)),
                    ),
                }
            )
            return

        if route == "/api/integrations/x/usage":
            self._send(XAPI.get_usage())
            return

        if route == "/api/system/blueprint":
            self._send(build_blueprint(SETTINGS, DB))
            return

        if route == "/api/system/ontology":
            self._send(build_system_ontology(SETTINGS, DB))
            return

        if route == "/api/system/deployed":
            self._send(load_deployed_info(ROOT))
            return

        if route == "/api/system/tasks":
            snapshot = build_blueprint(SETTINGS, DB)
            self._send(
                {
                    "summary": snapshot.get("summary", {}),
                    "phases": snapshot.get("phases", []),
                    "missing_requirements": snapshot.get("missing_requirements", []),
                }
            )
            return

        if route == "/api/billing/snapshot":
            business_unit = (query.get("business_unit", ["CC"])[0] or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            self._send(DB.get_billing_snapshot(business_unit))
            return

        if route == "/api/brief/daily":
            raw_bu = (query.get("business_unit", [""])[0] or "").upper()
            if raw_bu and _is_business_unit(raw_bu):
                business_unit, ok = self._enforce_bu(raw_bu)
                if not ok:
                    return
            else:
                business_unit, ok = self._enforce_bu(None)
                if not ok:
                    return
            self._send(DB.daily_brief(business_unit=business_unit))
            return

        if route == "/api/imessage/threads/recent":
            business_unit = (query.get("business_unit", [""])[0] or "").upper()
            limit = int(query.get("limit", ["50"])[0] or 50)
            self._send(
                {
                    "results": DB.list_recent_message_threads(
                        business_unit=business_unit if _is_business_unit(business_unit) else None,
                        limit=max(1, min(limit, 500)),
                    )
                }
            )
            return

        m = re.match(r"^/api/approvals/(\d+)$", route)
        if m:
            approval = DB.get_action_approval(int(m.group(1)))
            if not approval:
                self._send({"error": "approval not found"}, status=404)
                return
            self._send(approval)
            return

        m = re.match(r"^/api/acs/jobs/(\d+)$", route)
        if m:
            job = DB.get_acs_job(int(m.group(1)))
            if not job:
                self._send({"error": "job not found"}, status=404)
                return
            self._send(job)
            return

        self._send({"error": "not found", "path": self.path}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send({"error": "invalid json"}, status=400)
            return

        if route == "/api/sync/wix/contacts":
            result = WIX.sync_contacts(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/sync/wix/billing":
            result = WIX.sync_billing(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/sync/imessage/export":
            result = IMESSAGE.ingest_export(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/sync/imessage/chatdb":
            result = IMESSAGE.ingest_chatdb(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/imessage/send/propose":
            result = IMESSAGE.propose_send(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/imessage/send":
            result = IMESSAGE.send_with_approval(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/learning/source":
            business_unit = (body.get("business_unit") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            source_type = (body.get("source_type") or "").strip()
            source_ref = (body.get("source_ref") or "").strip()
            if not source_type or not source_ref:
                self._send({"error": "source_type and source_ref are required"}, status=400)
                return
            source_id = DB.upsert_learning_source(
                business_unit=business_unit,
                source_type=source_type,
                source_ref=source_ref,
                title=(body.get("title") or "").strip() or None,
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
                active=bool(body.get("active", True)),
            )
            self._send({"ok": True, "source_id": source_id, "business_unit": business_unit})
            return

        if route == "/api/learning/item":
            business_unit = (body.get("business_unit") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            source_type = (body.get("source_type") or "").strip()
            title = (body.get("title") or "").strip()
            if not source_type or not title:
                self._send({"error": "source_type and title are required"}, status=400)
                return
            tags = body.get("tags")
            if tags is not None and not isinstance(tags, list):
                self._send({"error": "tags must be a list when provided"}, status=400)
                return
            learning_item_id = DB.add_learning_item(
                business_unit=business_unit,
                source_type=source_type,
                source_ref=(body.get("source_ref") or "").strip() or None,
                title=title,
                url=(body.get("url") or "").strip() or None,
                published_at=(body.get("published_at") or "").strip() or None,
                transcript_text=(body.get("transcript_text") or "").strip() or None,
                summary_text=(body.get("summary_text") or "").strip() or None,
                relevance_score=float(body.get("relevance_score") or 0),
                tags=tags,
                idempotency_key=(body.get("idempotency_key") or "").strip() or None,
                source_id=int(body.get("source_id")) if body.get("source_id") else None,
            )
            self._send({"ok": True, "learning_item_id": learning_item_id, "business_unit": business_unit})
            return

        if route == "/api/learning/insight":
            business_unit = (body.get("business_unit") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            insight_type = (body.get("insight_type") or "").strip()
            title = (body.get("title") or "").strip()
            insight_text = (body.get("insight_text") or "").strip()
            if not insight_type or not title or not insight_text:
                self._send({"error": "insight_type, title, and insight_text are required"}, status=400)
                return
            tags = body.get("tags")
            if tags is not None and not isinstance(tags, list):
                self._send({"error": "tags must be a list when provided"}, status=400)
                return
            insight_id = DB.add_learning_insight(
                business_unit=business_unit,
                insight_type=insight_type,
                title=title,
                insight_text=insight_text,
                confidence=float(body.get("confidence") or 0.5),
                priority=int(body.get("priority") or 3),
                learning_item_id=int(body.get("learning_item_id")) if body.get("learning_item_id") else None,
                contact_id=int(body.get("contact_id")) if body.get("contact_id") else None,
                tags=tags,
                status=(body.get("status") or "active").strip(),
            )
            self._send({"ok": True, "insight_id": insight_id, "business_unit": business_unit})
            return

        if route == "/api/outreach/drafts/propose":
            business_unit = (body.get("business_unit") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return
            channel = (body.get("channel") or "").strip()
            recipient = (body.get("recipient") or "").strip()
            body_text = (body.get("body_text") or "").strip()
            if not channel or not recipient or not body_text:
                self._send({"error": "channel, recipient, and body_text are required"}, status=400)
                return
            source_insight_ids = body.get("source_insight_ids") or []
            if not isinstance(source_insight_ids, list):
                self._send({"error": "source_insight_ids must be a list"}, status=400)
                return
            normalized_source_insight_ids: list[int] = []
            for raw_id in source_insight_ids:
                try:
                    normalized_source_insight_ids.append(int(raw_id))
                except (TypeError, ValueError):
                    self._send({"error": "source_insight_ids must contain integers"}, status=400)
                    return
            result = DB.create_outreach_draft(
                business_unit=business_unit,
                channel=channel,
                recipient=recipient,
                body_text=body_text,
                subject=(body.get("subject") or "").strip() or None,
                rationale=(body.get("rationale") or "").strip() or None,
                contact_id=int(body.get("contact_id")) if body.get("contact_id") else None,
                source_insight_ids=normalized_source_insight_ids,
            )
            self._send({"ok": True, "business_unit": business_unit, **result})
            return

        m = re.match(r"^/api/approvals/(\d+)/(approve|reject)$", route)
        if m:
            approval_id = int(m.group(1))
            target_state = "approved" if m.group(2) == "approve" else "rejected"
            updated = DB.set_action_approval_state(approval_id, target_state)
            if not updated:
                self._send({"error": "approval not found"}, status=404)
                return
            DB.sync_outreach_draft_approval(approval_id=approval_id, approval_state=target_state)
            self._send({"ok": True, "approval": updated})
            return

        if route == "/api/voice/transcribe":
            business_unit = (body.get("business_unit", "CC") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return

            idempotency_key = body.get("idempotency_key") or secrets.token_hex(12)
            started = time()
            result = ELEVEN.transcribe(body.get("audio_base64"), body.get("text_hint"))
            transcript = (result.get("text") or body.get("text_hint") or "").strip()
            route = _voice_router_response(transcript, business_unit=business_unit, idempotency_key=idempotency_key)
            latency_ms = int((time() - started) * 1000)

            DB.add_voice_event(
                business_unit=business_unit,
                intent=route.get("intent", "transcribe"),
                transcript=transcript,
                confidence=result.get("confidence"),
                latency_ms=latency_ms,
                status="ok" if result.get("ok") else "error",
                idempotency_key=idempotency_key,
                details={"stt_mode": result.get("mode"), "route": route},
            )
            self._send(
                {
                    "ok": bool(result.get("ok")),
                    "business_unit": business_unit,
                    "stt": result,
                    "route": route,
                    "idempotency_key": idempotency_key,
                },
                status=200 if result.get("ok") else 502,
            )
            return

        if route == "/api/voice/speak":
            business_unit = (body.get("business_unit", "CC") or "CC").upper()
            if not _is_business_unit(business_unit):
                self._send({"error": "business_unit must be CC or ACS"}, status=400)
                return

            text = (body.get("text") or "").strip()
            result = ELEVEN.speak(text, body.get("voice_id"))
            idempotency_key = body.get("idempotency_key") or secrets.token_hex(12)
            DB.add_voice_event(
                business_unit=business_unit,
                intent=body.get("intent", "speak"),
                transcript=text,
                confidence=None,
                latency_ms=result.get("latency_ms"),
                status="ok" if result.get("ok") else "error",
                idempotency_key=idempotency_key,
                details={"tts_mode": result.get("mode"), "voice_id": result.get("voice_id")},
            )
            self._send(result, status=200 if result.get("ok") else 502)
            return

        if route == "/api/integrations/x/log-usage":
            amount_usd = float(body.get("amount_usd", 0.0))
            if amount_usd < 0:
                self._send({"error": "amount_usd must be >= 0"}, status=400)
                return
            usage = XAPI.record_usage(amount_usd)
            self._send(usage)
            return

        if route == "/api/integrations/google/smoke":
            subject = (body.get("delegated_subject") or "").strip() or None
            business_unit = (body.get("business_unit") or "").strip().upper() or None
            self._send(GOOGLE.hybrid_smoke(subject, business_unit=business_unit))
            return

        if route == "/api/sync/google/gmail":
            # Minimal ingestion to populate the Contact Brain with recent Gmail context.
            # Enforce email access per agent
            agent_id = self._agent_id()
            req_bu = (body.get("business_unit") or "").upper()
            if req_bu:
                bu_val, ok = self._enforce_bu(req_bu)
                if not ok:
                    return
            result = GOOGLE.gmail_ingest_recent(body)
            self._send(result, status=200 if result.get("ok") else 400)
            return

        if route == "/api/integrations/smoke":
            subject = (body.get("delegated_subject") or "").strip() or None
            self._send(
                {
                    # Always report both business lanes. Avoid the "default" lane because
                    # V4 is partitioned and may not set GOOGLE_OAUTH_TOKEN_FILE.
                    "google": {
                        "CC": GOOGLE.hybrid_smoke(subject, business_unit="CC"),
                        "ACS": GOOGLE.hybrid_smoke(subject, business_unit="ACS"),
                    },
                    "wix": WIX.smoke_probe(),
                    "x": XAPI.get_usage(),
                    "elevenlabs": ELEVEN.transcribe(None, text_hint="smoke probe"),
                }
            )
            return

        if route == "/api/acs/jobs":
            try:
                job = DB.create_acs_job(body)
            except ValueError as exc:
                self._send({"ok": False, "error": str(exc)}, status=400)
                return
            self._send({"ok": True, "job": job})
            return

        m = re.match(r"^/api/acs/jobs/(\d+)/assign$", route)
        if m:
            job_id = int(m.group(1))
            crew_member_name = (body.get("crew_member_name") or "").strip()
            if not crew_member_name:
                self._send({"ok": False, "error": "crew_member_name is required"}, status=400)
                return
            assignment_id = DB.assign_acs_job(job_id, crew_member_name=crew_member_name, role=body.get("role"))
            self._send({"ok": True, "assignment_id": assignment_id, "job_id": job_id})
            return

        m = re.match(r"^/api/acs/jobs/(\d+)/status$", route)
        if m:
            job_id = int(m.group(1))
            status = (body.get("status") or "").strip()
            if not status:
                self._send({"ok": False, "error": "status is required"}, status=400)
                return
            updated = DB.update_acs_job_status(job_id, status)
            if not updated:
                self._send({"ok": False, "error": "job not found"}, status=404)
                return
            self._send({"ok": True, "job": updated})
            return

        if route == "/api/acs/reminders/preview":
            lead_minutes = int(body.get("lead_minutes") or 30)
            preview = DB.build_acs_reminder_preview(lead_minutes=lead_minutes)
            self._send({"ok": True, "count": len(preview), "reminders": preview})
            return

        if route == "/api/acs/crew/location/sync":
            events = body.get("events") or []
            if not isinstance(events, list):
                self._send({"ok": False, "error": "events must be a list"}, status=400)
                return
            result = DB.ingest_crew_location_events(events, provider=(body.get("provider") or "traccar"))
            self._send({"ok": True, **result})
            return

        self._send({"error": "not found", "path": self.path}, status=404)


def main() -> None:
    if SETTINGS.business_guardrails_enabled:
        path_hits = []
        path_hits.extend(guard_runtime_paths(ROOT / "ops"))
        path_hits.extend(guard_runtime_paths(ROOT))
        if path_hits:
            raise SystemExit("Path normalization gate failed:\n" + "\n".join(sorted(set(path_hits))))

    server = ThreadingHTTPServer((SETTINGS.api_host, SETTINGS.api_port), Handler)
    print("Blaze-V4 API listening on http://{host}:{port}".format(host=SETTINGS.api_host, port=SETTINGS.api_port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        DB.close()


if __name__ == "__main__":
    main()
