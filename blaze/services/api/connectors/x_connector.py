from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from api.db import Database


@dataclass
class XConfig:
    enabled: bool
    bearer_token: str
    cap_usd: float
    warning_ratio: float = 0.8


class XConnector:
    def __init__(self, db: Database, config: XConfig) -> None:
        self.db = db
        self.config = config
        if not self.config.enabled:
            self.db.set_x_enabled(False, cap_usd=self.config.cap_usd)

    def get_usage(self) -> dict[str, Any]:
        local = self.db.get_x_usage(self.config.cap_usd)
        local["policy_enabled"] = self.config.enabled
        local["kill_switch"] = not self.config.enabled

        if not self.config.enabled:
            local["enabled"] = False
            local["status"] = "disabled_by_policy"
            local["remote_usage"] = None
            return local

        remote = None
        if self.config.bearer_token:
            remote = self._fetch_remote_usage()
        local["remote_usage"] = remote
        if local["ratio"] >= self.config.warning_ratio and local["ratio"] < 1.0:
            local["status"] = "warning"
        return local

    def record_usage(self, amount_usd: float) -> dict[str, Any]:
        if not self.config.enabled:
            self.db.set_x_enabled(False, cap_usd=self.config.cap_usd)
            return {
                "provider": "x",
                "enabled": False,
                "status": "disabled_by_policy",
                "kill_switch": True,
                "cap_usd": self.config.cap_usd,
            }

        usage = self.db.record_x_spend(amount_usd, self.config.cap_usd)
        if usage["ratio"] >= 1.0:
            self.db.set_x_enabled(False, cap_usd=self.config.cap_usd)
            usage["enabled"] = False
            usage["status"] = "cap_reached_auto_disabled"
            usage["kill_switch"] = True
        elif usage["ratio"] >= self.config.warning_ratio:
            usage["status"] = "warning"
            usage["kill_switch"] = False
        else:
            usage["kill_switch"] = False
        return usage

    def _fetch_remote_usage(self) -> dict[str, Any] | None:
        req = request.Request(
            url="https://api.x.com/2/usage",
            method="GET",
            headers={"Authorization": "Bearer {token}".format(token=self.config.bearer_token)},
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            return {"error": "HTTP {code}".format(code=exc.code), "detail": detail}
        except Exception as exc:
            return {"error": str(exc)}

