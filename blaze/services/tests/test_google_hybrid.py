from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from api.connectors.google_connector import GoogleConfig, GoogleConnector
from api.db import Database


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCreds:
    expiry = "2099-01-01T00:00:00Z"

    @classmethod
    def from_service_account_file(cls, *_args, **_kwargs):
        return cls()

    def with_subject(self, _subject):
        return self

    def refresh(self, _request):
        return None


class GoogleHybridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        self.sa_file = Path(self.tmp.name) / "service-account.json"
        self.sa_file.write_text("{}")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    @mock.patch("urllib.request.urlopen")
    def test_oauth_success_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse({"audience": "client", "scope": "x"})
        cfg = GoogleConfig(
            oauth_access_token="token",
            dwd_service_account_file=str(self.sa_file),
            dwd_impersonation_subject="admin@example.com",
            dwd_scopes="scope1,scope2",
        )
        connector = GoogleConnector(cfg, db=self.db)
        result = connector.validate_oauth_token()
        self.assertTrue(result["ok"])
        self.assertEqual(result["lane"], "oauth")

    @mock.patch("urllib.request.urlopen")
    def test_oauth_success_from_token_file(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse({"audience": "client", "scope": "x"})
        token_file = Path(self.tmp.name) / "oauth-token.json"
        token_file.write_text(json.dumps({"access_token": "from-file-token"}))
        cfg = GoogleConfig(
            oauth_access_token="",
            oauth_token_file_cc=str(token_file),
            dwd_service_account_file=str(self.sa_file),
            dwd_impersonation_subject="admin@example.com",
            dwd_scopes="scope1,scope2",
        )
        connector = GoogleConnector(cfg, db=self.db)
        result = connector.validate_oauth_token(business_unit="CC")
        self.assertTrue(result["ok"])
        self.assertEqual(result["lane"], "oauth")
        self.assertEqual(result["business_unit"], "CC")

    def test_dwd_success_path(self) -> None:
        google_module = types.ModuleType("google")
        google_auth_module = types.ModuleType("google.auth")
        google_auth_transport_module = types.ModuleType("google.auth.transport")
        google_auth_transport_requests_module = types.ModuleType("google.auth.transport.requests")
        google_auth_transport_requests_module.Request = lambda: object()
        google_oauth2_module = types.ModuleType("google.oauth2")
        google_oauth2_service_account_module = types.ModuleType("google.oauth2.service_account")
        google_oauth2_service_account_module.Credentials = _FakeCreds

        module_patch = {
            "google": google_module,
            "google.auth": google_auth_module,
            "google.auth.transport": google_auth_transport_module,
            "google.auth.transport.requests": google_auth_transport_requests_module,
            "google.oauth2": google_oauth2_module,
            "google.oauth2.service_account": google_oauth2_service_account_module,
        }

        cfg = GoogleConfig(
            oauth_access_token="",
            dwd_service_account_file=str(self.sa_file),
            dwd_impersonation_subject="admin@example.com",
            dwd_scopes="scope1,scope2",
        )
        with mock.patch.dict("sys.modules", module_patch):
            connector = GoogleConnector(cfg, db=self.db)
            result = connector.validate_dwd_impersonation(action="unit_test")
            self.assertTrue(result["ok"])
            self.assertEqual(result["lane"], "dwd")
            self.assertEqual(result["subject"], "admin@example.com")

    @mock.patch("urllib.request.urlopen")
    def test_hybrid_fallback_when_dwd_fails(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse({"audience": "client"})
        cfg = GoogleConfig(
            oauth_access_token="token",
            dwd_service_account_file="",
            dwd_impersonation_subject="",
            dwd_scopes="scope1",
        )
        connector = GoogleConnector(cfg, db=self.db)
        result = connector.hybrid_smoke()
        self.assertTrue(result["oauth"]["ok"])
        self.assertFalse(result["dwd"]["ok"])
        self.assertEqual(result["fallback_lane"], "oauth_non_admin")


if __name__ == "__main__":
    unittest.main()
