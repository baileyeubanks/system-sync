from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from api.db import Database
from api.system_ontology import build_system_ontology


class SystemOntologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(str(self.root / "test.db"))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _settings(self, manifest_path: Path):
        return types.SimpleNamespace(business_os_manifest_path=str(manifest_path))

    def test_ontology_includes_program_env_table_and_endpoint_nodes(self) -> None:
        manifest = {
            "version": "1",
            "system_name": "Test",
            "programs": [
                {
                    "id": "core",
                    "name": "Core",
                    "required_env": ["BUSINESS_GUARDRAILS_ENABLED"],
                    "required_env_any": [["GOOGLE_OAUTH_TOKEN_FILE_CC", "GOOGLE_OAUTH_ACCESS_TOKEN"]],
                    "required_tables": ["contacts"],
                    "required_endpoints": ["GET /api/contacts/search"],
                    "optional": False,
                }
            ],
            "build_sequence": [],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with mock.patch.dict(
            "os.environ",
            {
                "BUSINESS_GUARDRAILS_ENABLED": "true",
                "GOOGLE_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            ontology = build_system_ontology(self._settings(manifest_path), self.db)

        node_ids = {n["id"] for n in ontology["nodes"]}
        edge_types = {e["type"] for e in ontology["edges"]}

        self.assertIn("program:core", node_ids)
        self.assertIn("env:BUSINESS_GUARDRAILS_ENABLED", node_ids)
        self.assertIn("env:GOOGLE_OAUTH_TOKEN_FILE_CC", node_ids)
        self.assertIn("env:GOOGLE_OAUTH_ACCESS_TOKEN", node_ids)
        self.assertIn("table:contacts", node_ids)
        self.assertIn("endpoint:GET /api/contacts/search", node_ids)

        self.assertIn("requires_env", edge_types)
        self.assertIn("requires_env_any", edge_types)
        self.assertIn("any_of_env", edge_types)
        self.assertIn("requires_table", edge_types)
        self.assertIn("requires_endpoint", edge_types)

    def test_ontology_program_meta_includes_status_fields(self) -> None:
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
            "build_sequence": [],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with mock.patch.dict("os.environ", {}, clear=False):
            ontology = build_system_ontology(self._settings(manifest_path), self.db)

        programs = [n for n in ontology["nodes"] if n.get("type") == "program"]
        self.assertEqual(len(programs), 1)
        meta = programs[0]["meta"]
        self.assertEqual(meta["program_id"], "voice")
        self.assertIn(meta["status"], {"blocked", "blocked_disabled", "ready", "disabled_optional", "unknown"})
        self.assertIn("ELEVENLABS_API_KEY", meta.get("missing_env", []))


if __name__ == "__main__":
    unittest.main()

