from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ENV_FILE_CANDIDATES = [
    Path(__file__).resolve().parents[1] / ".env",
    Path(__file__).resolve().parents[1] / "ops" / "env.local",
]


def _load_env_file(path: Path, *, override_existing: bool = False) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Policy:
        # - Never override a value that is explicitly set in the process environment.
        # - Allow ops/env.local to override values loaded from a repo ".env" file.
        if not key:
            continue
        if key in os.environ and os.environ.get(key, "") != "" and not override_existing:
            continue
        os.environ[key] = value


for candidate in ENV_FILE_CANDIDATES:
    _load_env_file(candidate, override_existing=(candidate.name == "env.local"))


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8899"))
    db_path: str = os.getenv(
        "CONTACT_DB_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "contact_brain_v4.db"),
    )

    business_guardrails_enabled: bool = _env_bool("BUSINESS_GUARDRAILS_ENABLED", "true")
    runtime_role: str = os.getenv("RUNTIME_ROLE", "operator").strip().lower()

    wix_sync_enabled: bool = _env_bool("WIX_SYNC_ENABLED", "true")
    wix_api_key: str = os.getenv("WIX_API_KEY", "")
    wix_site_id: str = os.getenv("WIX_SITE_ID", "")
    wix_account_id: str = os.getenv("WIX_ACCOUNT_ID", "")

    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    voice_enabled: bool = _env_bool("VOICE_ENABLED", "false")
    elevenlabs_default_voice_id: str = os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "")
    elevenlabs_stt_model_id: str = os.getenv("ELEVENLABS_STT_MODEL_ID", "scribe_v1")
    elevenlabs_tts_model_id: str = os.getenv("ELEVENLABS_TTS_MODEL_ID", "eleven_turbo_v2_5")

    x_api_enabled: bool = _env_bool("X_API_ENABLED", "true")
    x_bearer_token: str = os.getenv("X_BEARER_TOKEN", "")
    x_monthly_spend_cap_usd: float = float(os.getenv("X_MONTHLY_SPEND_CAP_USD", "25"))
    x_warning_ratio: float = float(os.getenv("X_WARNING_RATIO", "0.8"))
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")

    google_oauth_access_token: str = os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN", "")
    google_oauth_credentials_file: str = os.getenv("GOOGLE_OAUTH_CREDENTIALS_FILE", "")
    google_oauth_token_file: str = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "")
    google_oauth_credentials_file_cc: str = os.getenv("GOOGLE_OAUTH_CREDENTIALS_FILE_CC", "")
    google_oauth_token_file_cc: str = os.getenv("GOOGLE_OAUTH_TOKEN_FILE_CC", "")
    google_oauth_credentials_file_acs: str = os.getenv("GOOGLE_OAUTH_CREDENTIALS_FILE_ACS", "")
    google_oauth_token_file_acs: str = os.getenv("GOOGLE_OAUTH_TOKEN_FILE_ACS", "")
    google_dwd_service_account_file: str = os.getenv("GOOGLE_DWD_SERVICE_ACCOUNT_FILE", "")
    google_dwd_impersonation_subject: str = os.getenv("GOOGLE_DWD_IMPERSONATION_SUBJECT", "")
    google_dwd_scopes: str = os.getenv(
        "GOOGLE_DWD_SCOPES", "https://www.googleapis.com/auth/admin.directory.user.readonly"
    )

    imessage_enabled: bool = _env_bool("IMESSAGE_ENABLED", "true")
    imessage_export_root: str = os.getenv("IMESSAGE_EXPORT_ROOT", "/Users/Shared/Blaze-V4/imessage")
    imessage_send_enabled_cc: bool = _env_bool("IMESSAGE_SEND_ENABLED_CC", "false")
    imessage_send_enabled_acs: bool = _env_bool("IMESSAGE_SEND_ENABLED_ACS", "false")
    imessage_sender_user_cc: str = os.getenv("IMESSAGE_SENDER_USER_CC", "blazeops")
    imessage_sender_user_acs: str = os.getenv("IMESSAGE_SENDER_USER_ACS", "acsops")
    imessage_rate_limit_per_minute: int = int(os.getenv("IMESSAGE_RATE_LIMIT_PER_MINUTE", "10"))
    business_os_manifest_path: str = os.getenv(
        "BUSINESS_OS_MANIFEST_PATH",
        str(Path(__file__).resolve().parents[1] / "ops" / "business_os_manifest.json"),
    )
    master_operator_secret_ref: str = os.getenv("MASTER_OPERATOR_SECRET_REF", "keychain:blazev4/master")


SETTINGS = Settings()
