from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from api.db import Database


@dataclass
class GoogleConfig:
    oauth_access_token: str = ""
    oauth_credentials_file: str = ""
    oauth_token_file: str = ""
    oauth_credentials_file_cc: str = ""
    oauth_token_file_cc: str = ""
    oauth_credentials_file_acs: str = ""
    oauth_token_file_acs: str = ""
    dwd_service_account_file: str = ""
    dwd_impersonation_subject: str = ""
    dwd_scopes: str = ""


class GoogleConnector:
    def __init__(self, config: GoogleConfig, db: Database | None = None) -> None:
        self.config = config
        self.db = db

    def _refresh_access_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_uri: str = "https://oauth2.googleapis.com/token",
    ) -> tuple[str, str | None]:
        """
        Refresh an OAuth access token using a refresh token (stdlib-only).
        Returns (access_token, expiry_iso_or_none). Raises on failure.
        """
        form = parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        req = request.Request(
            url=token_uri,
            method="POST",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw else {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("refresh returned no access_token")
        expires_in = int(data.get("expires_in") or 0)
        expiry = None
        if expires_in > 0:
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        return token, expiry

    def _needs_refresh(self, raw: dict[str, Any]) -> bool:
        token = str(raw.get("access_token") or raw.get("token") or "").strip()
        refresh_token = str(raw.get("refresh_token") or "").strip()
        if not refresh_token:
            return False
        if not token:
            return True
        expiry_raw = str(raw.get("expiry") or "").strip()
        if not expiry_raw:
            # If expiry is unknown, err on the side of refreshing (safe + cheap).
            return True
        try:
            # google-auth writes an RFC3339-ish string; accept both with/without tz.
            expiry_dt = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            return expiry_dt <= (datetime.now(timezone.utc) + timedelta(minutes=5))
        except Exception:
            return True

    def _token_from_file(self, token_file: str) -> str:
        if not token_file:
            return ""
        path = Path(token_file).expanduser()
        if not path.exists():
            return ""
        try:
            raw = json.loads(path.read_text(errors="ignore"))
        except Exception:
            return ""
        if isinstance(raw, dict):
            # Support:
            # 1) simple access_token files: {"access_token": "..."}
            # 2) refresh-capable files produced by google-auth-oauthlib:
            #    {"token": "...", "refresh_token":"...", "client_id":"...", "client_secret":"...", "scopes":[...], "expiry":"..."}
            token = str(raw.get("access_token") or raw.get("token") or "").strip()
            refresh_token = str(raw.get("refresh_token") or "").strip()
            if refresh_token and self._needs_refresh(raw):
                client_id = str(raw.get("client_id") or "").strip()
                client_secret = str(raw.get("client_secret") or "").strip()
                if client_id and client_secret:
                    try:
                        new_token, expiry = self._refresh_access_token(
                            refresh_token=refresh_token,
                            client_id=client_id,
                            client_secret=client_secret,
                        )
                        raw["token"] = new_token
                        raw["access_token"] = new_token
                        if expiry:
                            raw["expiry"] = expiry
                        path.write_text(json.dumps(raw, indent=2, ensure_ascii=True) + "\n")
                        return new_token
                    except Exception:
                        # Fall back to whatever is on disk.
                        return token
            return token
        return ""

    def _resolve_oauth_token(self, business_unit: str | None = None) -> str:
        bu = (business_unit or "").upper()
        if bu == "CC":
            return self._token_from_file(self.config.oauth_token_file_cc) or self.config.oauth_access_token
        if bu == "ACS":
            return self._token_from_file(self.config.oauth_token_file_acs) or self.config.oauth_access_token
        return self._token_from_file(self.config.oauth_token_file) or self.config.oauth_access_token

    def _resolve_credentials_file(self, business_unit: str | None = None) -> str:
        bu = (business_unit or "").upper()
        if bu == "CC":
            return self.config.oauth_credentials_file_cc or self.config.oauth_credentials_file
        if bu == "ACS":
            return self.config.oauth_credentials_file_acs or self.config.oauth_credentials_file
        return self.config.oauth_credentials_file

    def validate_oauth_token(self, business_unit: str | None = None) -> dict[str, Any]:
        token = self._resolve_oauth_token(business_unit)
        if not token:
            bu = (business_unit or "").upper() or "default"
            return {"ok": False, "lane": "oauth", "reason": f"OAuth token missing for {bu}"}

        query = parse.urlencode({"access_token": token})
        url = "https://oauth2.googleapis.com/tokeninfo?{query}".format(query=query)
        req = request.Request(url=url, method="GET")
        try:
            with request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            return {
                "ok": True,
                "lane": "oauth",
                "business_unit": (business_unit or "").upper() or None,
                "credentials_file": self._resolve_credentials_file(business_unit),
                "token_info": data,
            }
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            return {"ok": False, "lane": "oauth", "error": "HTTP {code}".format(code=exc.code), "detail": detail}
        except Exception as exc:
            return {"ok": False, "lane": "oauth", "error": str(exc)}

    def validate_dwd_impersonation(
        self,
        delegated_subject: str | None = None,
        action: str = "dwd_smoke",
    ) -> dict[str, Any]:
        subject = (delegated_subject or self.config.dwd_impersonation_subject or "").strip()
        if not self.config.dwd_service_account_file:
            return {"ok": False, "lane": "dwd", "reason": "GOOGLE_DWD_SERVICE_ACCOUNT_FILE missing"}
        if not subject:
            return {"ok": False, "lane": "dwd", "reason": "GOOGLE_DWD_IMPERSONATION_SUBJECT missing"}
        if not Path(self.config.dwd_service_account_file).exists():
            return {"ok": False, "lane": "dwd", "reason": "service account file not found"}

        try:
            from google.auth.transport.requests import Request as GoogleRequest  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except Exception:
            return {
                "ok": False,
                "lane": "dwd",
                "reason": "google-auth not installed",
                "install_hint": "pip install google-auth",
            }

        try:
            scopes = [s.strip() for s in self.config.dwd_scopes.split(",") if s.strip()]
            credentials = service_account.Credentials.from_service_account_file(
                self.config.dwd_service_account_file,
                scopes=scopes,
            ).with_subject(subject)
            credentials.refresh(GoogleRequest())
            result = {
                "ok": True,
                "lane": "dwd",
                "subject": subject,
                "scopes": scopes,
                "token_expiry": str(credentials.expiry),
            }
            if self.db:
                self.db.add_admin_audit_log(
                    provider="google",
                    delegated_subject=subject,
                    action=action,
                    status="ok",
                    details={"lane": "dwd", "scopes": scopes},
                )
            return result
        except Exception as exc:
            if self.db:
                self.db.add_admin_audit_log(
                    provider="google",
                    delegated_subject=subject,
                    action=action,
                    status="error",
                    details={"error": str(exc)},
                )
            return {"ok": False, "lane": "dwd", "error": str(exc), "subject": subject}

    def hybrid_smoke(self, delegated_subject: str | None = None, business_unit: str | None = None) -> dict[str, Any]:
        oauth = self.validate_oauth_token(business_unit=business_unit)
        dwd = self.validate_dwd_impersonation(
            delegated_subject=delegated_subject,
            action="hybrid_smoke",
        )
        fallback_lane = None
        if not dwd.get("ok") and oauth.get("ok"):
            fallback_lane = "oauth_non_admin"
        return {
            "oauth": oauth,
            "dwd": dwd,
            "business_unit": (business_unit or "").upper() or None,
            "fallback_lane": fallback_lane,
            "admin_actions_allowed": bool(dwd.get("ok")),
        }

    def _gmail_request(self, method: str, url: str, token: str) -> dict[str, Any]:
        req = request.Request(
            url=url,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    def _extract_emails(self, header_value: str | None) -> list[str]:
        if not header_value:
            return []
        # Supports: "Name <a@b.com>, c@d.com"
        emails: list[str] = []
        for _, addr in getaddresses([header_value]):
            _, parsed = parseaddr(addr)
            candidate = (parsed or addr).strip().lower()
            if "@" in candidate:
                emails.append(candidate)
        # de-dupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for e in emails:
            if e not in seen:
                out.append(e)
                seen.add(e)
        return out

    def gmail_ingest_recent(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Minimal Gmail ingestion:
        - Pull N recent messages for a business unit
        - Create/refresh contacts based on email participants
        - Store threads + a lightweight interaction record (subject + snippet + headers)
        """
        if not self.db:
            return {"ok": False, "reason": "DB_NOT_CONFIGURED"}

        business_unit = (payload.get("business_unit") or "").strip().upper()
        if business_unit not in {"CC", "ACS"}:
            return {"ok": False, "reason": "business_unit must be CC or ACS"}

        token = self._resolve_oauth_token(business_unit=business_unit)
        if not token:
            return {"ok": False, "reason": f"OAuth token missing for {business_unit}"}

        max_messages = int(payload.get("max_messages") or 50)
        max_messages = max(1, min(max_messages, 200))
        query = (payload.get("q") or "").strip()
        if not query:
            days = int(payload.get("since_days") or 30)
            days = max(1, min(days, 365))
            query = f"newer_than:{days}d"

        idempotency_key = (payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            # Stable enough for a run; message-level dedupe happens via per-message idempotency keys.
            idempotency_key = f"gmail-recent:{business_unit}:{hash(query) & 0xffffffff}:{max_messages}"

        job = self.db.begin_ingestion_job(
            provider="google",
            business_unit=business_unit,
            job_type="gmail_recent",
            idempotency_key=idempotency_key,
            details={"q": query, "max_messages": max_messages},
        )
        if not job.get("accepted"):
            return {"ok": True, "duplicate": True, "idempotency_key": idempotency_key, "status": job.get("status")}

        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        list_url = base + "/messages?" + parse.urlencode({"maxResults": max_messages, "q": query})
        try:
            listing = self._gmail_request("GET", list_url, token)
        except Exception as exc:
            self.db.finalize_ingestion_job(
                provider="google",
                business_unit=business_unit,
                job_type="gmail_recent",
                idempotency_key=idempotency_key,
                status="error",
                details={"error": str(exc)},
            )
            return {"ok": False, "error": str(exc), "business_unit": business_unit}

        msgs = listing.get("messages") or []
        if not isinstance(msgs, list):
            msgs = []

        threads_upserted = 0
        interactions_upserted = 0
        contacts_upserted = 0
        last_internal_ms: int | None = None

        for m in msgs:
            msg_id = str((m or {}).get("id") or "").strip()
            if not msg_id:
                continue
            get_url = (
                base
                + f"/messages/{parse.quote(msg_id)}?"
                + parse.urlencode(
                    {
                        "format": "metadata",
                        "metadataHeaders": ["From", "To", "Cc", "Subject", "Date", "Message-Id"],
                    },
                    doseq=True,
                )
            )
            try:
                data = self._gmail_request("GET", get_url, token)
            except Exception:
                continue

            thread_id = str(data.get("threadId") or "").strip() or msg_id
            snippet = str(data.get("snippet") or "").strip()
            internal_ms = None
            try:
                internal_ms = int(data.get("internalDate") or 0) or None
            except Exception:
                internal_ms = None
            if internal_ms is not None:
                last_internal_ms = max(last_internal_ms or 0, internal_ms)

            headers = {}
            for h in (data.get("payload") or {}).get("headers") or []:
                if not isinstance(h, dict):
                    continue
                name = str(h.get("name") or "").strip()
                value = str(h.get("value") or "").strip()
                if name:
                    headers[name.lower()] = value

            from_raw = headers.get("from")
            to_raw = headers.get("to")
            cc_raw = headers.get("cc")
            subject = headers.get("subject") or ""
            date_hdr = headers.get("date")

            from_emails = self._extract_emails(from_raw)
            to_emails = self._extract_emails(to_raw)
            cc_emails = self._extract_emails(cc_raw)
            participants = []
            for e in from_emails + to_emails + cc_emails:
                if e not in participants:
                    participants.append(e)

            # Direction heuristic: if the sender is not "me", treat as inbound.
            # If uncertain, store as unknown; we still capture the interaction.
            direction = "unknown"
            if from_emails and to_emails:
                # If message is from someone else to someone (likely us), inbound.
                direction = "in"
                # If message is from us to others, outbound.
                # We don't know "me" reliably without extra config; best-effort:
                if business_unit == "CC" and any("contentco-op" in e for e in from_emails):
                    direction = "out"
                if business_unit == "ACS" and any("astrocleanings" in e for e in from_emails):
                    direction = "out"

            # Primary counterpart contact: choose the first non-company email when possible.
            counterpart = None
            if direction == "in":
                counterpart = from_emails[0] if from_emails else None
            elif direction == "out":
                counterpart = to_emails[0] if to_emails else None
            else:
                counterpart = (from_emails or to_emails or [None])[0]

            contact_id = None
            if counterpart:
                contact_id = self.db.upsert_contact_from_external(
                    business_unit=business_unit,
                    full_name=counterpart,
                    primary_email=counterpart,
                    company=None,
                    source_of_truth="google",
                    provider="google_gmail_email",
                    external_id=counterpart,
                    metadata={"thread_id": thread_id},
                )
                contacts_upserted += 1

            # Thread + interaction
            latest_iso = None
            if internal_ms:
                latest_iso = datetime.utcfromtimestamp(internal_ms / 1000).isoformat(timespec="seconds") + "Z"
            elif date_hdr:
                latest_iso = date_hdr

            self.db.upsert_message_thread(
                business_unit=business_unit,
                source="google_gmail",
                external_thread_id=thread_id,
                latest_message_at=latest_iso,
                participants=participants,
                message_count=1,
                metadata={"subject": subject, "date": date_hdr, "message_id": msg_id},
            )
            threads_upserted += 1

            interaction_payload = {
                "subject": subject,
                "snippet": snippet,
                "from": from_raw,
                "to": to_raw,
                "cc": cc_raw,
                "date": date_hdr,
                "thread_id": thread_id,
                "message_id": msg_id,
            }
            self.db.add_interaction(
                business_unit=business_unit,
                source="google_gmail",
                direction=direction,
                content=json.dumps(interaction_payload, ensure_ascii=True),
                idempotency_key=f"gmail:{business_unit}:{msg_id}",
                contact_id=contact_id,
            )
            interactions_upserted += 1

        self.db.upsert_sync_cursor("google_gmail", business_unit, str(last_internal_ms) if last_internal_ms else None, "ok")
        self.db.finalize_ingestion_job(
            provider="google",
            business_unit=business_unit,
            job_type="gmail_recent",
            idempotency_key=idempotency_key,
            status="ok",
            details={
                "q": query,
                "max_messages": max_messages,
                "messages_seen": len(msgs),
                "threads_upserted": threads_upserted,
                "interactions_upserted": interactions_upserted,
                "contacts_upserted": contacts_upserted,
                "cursor_internalDateMs": last_internal_ms,
            },
        )
        return {
            "ok": True,
            "business_unit": business_unit,
            "q": query,
            "messages_seen": len(msgs),
            "threads_upserted": threads_upserted,
            "interactions_upserted": interactions_upserted,
            "contacts_upserted": contacts_upserted,
            "idempotency_key": idempotency_key,
        }
