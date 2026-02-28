from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from api.db import Database


@dataclass
class WixConfig:
    enabled: bool
    api_key: str
    site_id: str
    account_id: str


class WixConnector:
    def __init__(self, db: Database, config: WixConfig) -> None:
        self.db = db
        self.config = config

    def _json_request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.config.api_key,
        }
        if self.config.site_id:
            headers["wix-site-id"] = self.config.site_id
        elif self.config.account_id:
            headers["wix-account-id"] = self.config.account_id
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = request.Request(url=url, method=method, headers=headers, data=body)
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    def _resolve_idempotency_key(self, payload: dict[str, Any], key_name: str) -> str:
        user_key = (payload.get("idempotency_key") or "").strip()
        if user_key:
            return user_key
        source_payload = payload.get(key_name)
        if source_payload:
            source = json.dumps(source_payload, sort_keys=True, default=str)
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
            return f"auto-{digest}"
        return "auto-{token}".format(token=secrets.token_hex(12))

    def smoke_probe(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "reason": "WIX_SYNC_DISABLED"}
        if not self.config.api_key:
            return {"ok": False, "reason": "WIX_API_KEY_MISSING"}
        if not self.config.site_id and not self.config.account_id:
            return {"ok": False, "reason": "WIX_SITE_OR_ACCOUNT_ID_MISSING"}
        try:
            response = self._json_request(
                "POST",
                "https://www.wixapis.com/contacts/v4/contacts/query",
                {"query": {"paging": {"offset": 0, "limit": 1}}},
            )
            contacts = response.get("contacts", [])
            return {"ok": True, "contacts_seen": len(contacts)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def sync_contacts(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "reason": "WIX_SYNC_DISABLED"}

        contacts = payload.get("contacts")
        pulled_live = False
        if contacts is None:
            if not self.config.api_key:
                return {"ok": False, "reason": "WIX_API_KEY_MISSING"}
            live_payload = {"query": {"paging": {"offset": 0, "limit": 100}}}
            response = self._json_request(
                "POST",
                "https://www.wixapis.com/contacts/v4/contacts/query",
                live_payload,
            )
            contacts = response.get("contacts", [])
            pulled_live = True

        idempotency_key = self._resolve_idempotency_key(payload, "contacts")
        job = self.db.begin_ingestion_job(
            provider="wix",
            business_unit="CC",
            job_type="contacts_sync",
            idempotency_key=idempotency_key,
            details={"contacts_seen": len(contacts), "source": "wix_live" if pulled_live else "payload"},
        )
        if not job["accepted"]:
            return {
                "ok": True,
                "duplicate": True,
                "idempotency_key": idempotency_key,
                "status": job.get("status", "duplicate"),
            }

        upserted = 0
        for c in contacts:
            primary_email = None
            emails = c.get("primaryInfo", {}).get("emails") or c.get("emails") or []
            if emails:
                first = emails[0]
                if isinstance(first, dict):
                    primary_email = first.get("email")
                elif isinstance(first, str):
                    primary_email = first

            full_name = (
                c.get("info", {}).get("name", {}).get("fullName")
                or c.get("primaryInfo", {}).get("name")
                or c.get("name")
                or "Unknown"
            )
            external_id = c.get("id") or c.get("_id") or primary_email or full_name
            company = c.get("info", {}).get("company") or c.get("primaryInfo", {}).get("company")

            self.db.upsert_contact_from_external(
                business_unit="CC",
                full_name=full_name,
                primary_email=primary_email,
                company=company,
                source_of_truth="wix",
                provider="wix_contact",
                external_id=str(external_id),
                metadata=c,
            )
            upserted += 1

        self.db.upsert_sync_cursor("wix_contacts", "CC", None, "ok")
        self.db.finalize_ingestion_job(
            provider="wix",
            business_unit="CC",
            job_type="contacts_sync",
            idempotency_key=idempotency_key,
            status="ok",
            details={"contacts_seen": len(contacts), "contacts_upserted": upserted},
        )
        return {
            "ok": True,
            "business_unit": "CC",
            "source": "wix_live" if pulled_live else "payload",
            "contacts_seen": len(contacts),
            "contacts_upserted": upserted,
            "write_policy": "read_mirror_only",
            "idempotency_key": idempotency_key,
        }

    def sync_billing(self, payload: dict[str, Any]) -> dict[str, Any]:
        invoices = payload.get("invoices", [])
        quotes = payload.get("quotes", [])
        idempotency_key = self._resolve_idempotency_key(payload, "quotes")
        job = self.db.begin_ingestion_job(
            provider="wix",
            business_unit="CC",
            job_type="billing_sync",
            idempotency_key=idempotency_key,
            details={"invoices_seen": len(invoices), "quotes_seen": len(quotes)},
        )
        if not job["accepted"]:
            return {
                "ok": True,
                "duplicate": True,
                "idempotency_key": idempotency_key,
                "status": job.get("status", "duplicate"),
            }

        for inv in invoices:
            external_ref = str(inv.get("id") or inv.get("invoiceId") or inv.get("number") or "unknown_invoice")
            self.db.add_billing_link(
                business_unit="CC",
                provider="wix_invoice",
                external_ref=external_ref,
                metadata=inv,
            )

        for quote in quotes:
            external_ref = str(quote.get("id") or quote.get("quoteId") or quote.get("number") or "unknown_quote")
            self.db.add_billing_link(
                business_unit="CC",
                provider="wix_quote",
                external_ref=external_ref,
                metadata=quote,
            )

        self.db.upsert_sync_cursor("wix_billing", "CC", None, "ok")
        self.db.finalize_ingestion_job(
            provider="wix",
            business_unit="CC",
            job_type="billing_sync",
            idempotency_key=idempotency_key,
            status="ok",
            details={"invoices_mirrored": len(invoices), "quotes_mirrored": len(quotes)},
        )
        return {
            "ok": True,
            "business_unit": "CC",
            "write_policy": "read_mirror_only",
            "invoices_mirrored": len(invoices),
            "quotes_mirrored": len(quotes),
            "idempotency_key": idempotency_key,
            "note": "Wix billing mirror expects webhook/payload feed from Wix backend integration.",
        }
