from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.connectors.wix_connector import WixConfig, WixConnector
from api.db import Database


class WixPartitioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "test.db")
        self.db = Database(db_path)
        self.wix = WixConnector(self.db, WixConfig(enabled=True, api_key="", site_id="", account_id=""))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_wix_read_mirror_integrity(self) -> None:
        payload = {
            "idempotency_key": "sync-001",
            "contacts": [
                {
                    "id": "wix-1",
                    "name": "Rocco Richards",
                    "emails": [{"email": "rocco@example.com"}],
                    "primaryInfo": {"company": "Richards LLC"},
                }
            ],
        }
        result = self.wix.sync_contacts(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["write_policy"], "read_mirror_only")
        self.assertEqual(result["business_unit"], "CC")
        self.assertEqual(result["contacts_upserted"], 1)

    def test_wix_sync_does_not_overwrite_acs(self) -> None:
        acs_id = self.db.upsert_contact_from_external(
            business_unit="ACS",
            full_name="Rocco Richards",
            primary_email="rocco@example.com",
            company="Astro Client",
            source_of_truth="local",
            provider="manual",
            external_id="acs-rocco",
            metadata={"note": "local"},
        )

        self.wix.sync_contacts(
            {
                "idempotency_key": "sync-002",
                "contacts": [
                    {
                        "id": "wix-2",
                        "name": "Rocco Richards",
                        "emails": [{"email": "rocco@example.com"}],
                        "primaryInfo": {"company": "Content Client"},
                    }
                ],
            }
        )

        acs_contact = self.db.get_unified_contact(acs_id)
        self.assertIsNotNone(acs_contact)
        self.assertEqual(acs_contact["contact"]["business_unit"], "ACS")
        self.assertEqual(acs_contact["contact"]["source_of_truth"], "local")

        matches = self.db.search_contacts("rocco@example.com")
        units = sorted([m["business_unit"] for m in matches])
        self.assertIn("ACS", units)
        self.assertIn("CC", units)

    def test_wix_ingestion_idempotency(self) -> None:
        payload = {
            "idempotency_key": "sync-003",
            "contacts": [{"id": "wix-3", "name": "Nipur", "emails": [{"email": "nipur@example.com"}]}],
        }
        first = self.wix.sync_contacts(payload)
        second = self.wix.sync_contacts(payload)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(second.get("duplicate", False))


if __name__ == "__main__":
    unittest.main()
