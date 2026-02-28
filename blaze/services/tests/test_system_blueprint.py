from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from api.db import Database
from api.system_blueprint import build_blueprint


class SystemBlueprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(str(self.root / "test.db"))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _settings(self, manifest_path: Path):
        return types.SimpleNamespace(business_os_manifest_path=str(manifest_path))

    def test_program_ready_when_tables_and_env_present(self) -> None:
        manifest = {
            "version": "1",
            "system_name": "Test",
            "programs": [
                {
                    "id": "core",
                    "name": "Core",
                    "required_env": ["BUSINESS_GUARDRAILS_ENABLED"],
                    "required_tables": ["contacts"],
                    "optional": False,
                }
            ],
            "build_sequence": [{"id": "phase_2", "title": "Core", "tasks": ["x"]}],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with mock.patch.dict("os.environ", {"BUSINESS_GUARDRAILS_ENABLED": "true"}, clear=False):
            snapshot = build_blueprint(self._settings(manifest_path), self.db)

        self.assertEqual(snapshot["summary"]["overall_status"], "ready")
        self.assertEqual(snapshot["programs"][0]["status"], "ready")

    def test_program_blocked_when_required_env_missing(self) -> None:
        manifest = {
            "version": "1",
            "system_name": "Test",
            "programs": [
                {
                    "id": "voice",
                    "name": "Voice",
                    "required_env": ["ELEVENLABS_API_KEY"],
                    "required_tables": ["voice_events"],
                    "optional": False,
                }
            ],
            "build_sequence": [{"id": "phase_3", "title": "Integrations", "tasks": ["x"]}],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with mock.patch.dict("os.environ", {}, clear=False):
            snapshot = build_blueprint(self._settings(manifest_path), self.db)

        self.assertEqual(snapshot["summary"]["overall_status"], "blocked")
        self.assertEqual(snapshot["programs"][0]["status"], "blocked")
        self.assertIn("ELEVENLABS_API_KEY", snapshot["programs"][0]["missing_env"])

    def test_optional_program_can_be_disabled(self) -> None:
        manifest = {
            "version": "1",
            "system_name": "Test",
            "programs": [
                {
                    "id": "x_research",
                    "name": "X",
                    "enable_env": "X_API_ENABLED",
                    "required_env": ["X_BEARER_TOKEN"],
                    "required_tables": ["integration_usage"],
                    "optional": True,
                }
            ],
            "build_sequence": [{"id": "phase_3", "title": "Integrations", "tasks": ["x"]}],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with mock.patch.dict("os.environ", {"X_API_ENABLED": "false"}, clear=False):
            snapshot = build_blueprint(self._settings(manifest_path), self.db)

        self.assertEqual(snapshot["programs"][0]["status"], "disabled_optional")


if __name__ == "__main__":
    unittest.main()
