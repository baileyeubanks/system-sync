#!/usr/bin/env python3
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import dependencies
from api.config import SETTINGS
from api.connectors.elevenlabs_connector import ElevenLabsConfig, ElevenLabsConnector
from api.connectors.google_connector import GoogleConfig, GoogleConnector
from api.connectors.imessage_connector import IMessageConfig, IMessageConnector
from api.connectors.wix_connector import WixConfig, WixConnector
from api.connectors.x_connector import XConfig, XConnector
from api.db import Database
from api.path_guard import guard_runtime_paths
from api.routers import (
    dashboard,
    whatsapp,
    acs,
    approvals,
    billing,
    brief,
    contacts,
    health,
    imessage,
    integrations,
    learning,
    outreach,
    sync,
    system,
    voice,
    brand,
    quote,
    property,
)

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    if SETTINGS.business_guardrails_enabled:
        path_hits = []
        path_hits.extend(guard_runtime_paths(ROOT / "ops"))
        path_hits.extend(guard_runtime_paths(ROOT))
        if path_hits:
            raise SystemExit(
                "Path normalization gate failed:\n" + "\n".join(sorted(set(path_hits)))
            )

    db = Database(SETTINGS.db_path)
    wix = WixConnector(
        db,
        WixConfig(
            enabled=SETTINGS.wix_sync_enabled,
            api_key=SETTINGS.wix_api_key,
            site_id=SETTINGS.wix_site_id,
            account_id=SETTINGS.wix_account_id,
        ),
    )
    eleven = ElevenLabsConnector(
        ElevenLabsConfig(
            api_key=SETTINGS.elevenlabs_api_key,
            default_voice_id=SETTINGS.elevenlabs_default_voice_id,
            stt_model_id=SETTINGS.elevenlabs_stt_model_id,
            tts_model_id=SETTINGS.elevenlabs_tts_model_id,
        )
    )
    xapi = XConnector(
        db,
        XConfig(
            enabled=SETTINGS.x_api_enabled,
            bearer_token=SETTINGS.x_bearer_token,
            cap_usd=SETTINGS.x_monthly_spend_cap_usd,
            warning_ratio=SETTINGS.x_warning_ratio,
        ),
    )
    google = GoogleConnector(
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
        db=db,
    )
    imessage_conn = IMessageConnector(
        db,
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

    dependencies._state.update({
        "db": db,
        "wix": wix,
        "eleven": eleven,
        "xapi": xapi,
        "google": google,
        "imessage": imessage_conn,
        "settings": SETTINGS,
        "root": ROOT,
    })

    print("Blaze-V4 FastAPI ready on http://{h}:{p}".format(
        h=SETTINGS.api_host, p=SETTINGS.api_port
    ))

    yield

    # --- Shutdown ---
    db.close()
    dependencies._state.clear()


app = FastAPI(
    title="Blaze V4 API",
    version="4.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal server error", "detail": str(exc)},
    )


# Mount all routers
app.include_router(health.router)
app.include_router(contacts.router)
app.include_router(learning.router)
app.include_router(outreach.router)
app.include_router(imessage.router)
app.include_router(voice.router)
app.include_router(integrations.router)
app.include_router(sync.router)
app.include_router(billing.router)
app.include_router(brief.router)
app.include_router(approvals.router)
app.include_router(acs.router)
app.include_router(whatsapp.router)
app.include_router(system.router)
app.include_router(brand.router)
app.include_router(quote.router)
app.include_router(property.router)


app.include_router(dashboard.router)

# Static files + dashboard shortcut
_STATIC = Path(__file__).resolve().parent / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

@app.get("/dashboard", include_in_schema=False)
def serve_dashboard():
    return FileResponse(str(_STATIC / "dashboard.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=SETTINGS.api_host,
        port=SETTINGS.api_port,
        loop="uvloop",
        workers=1,
        log_level="warning",
    )
