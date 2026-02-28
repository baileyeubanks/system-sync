from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api.connectors.imessage_connector import IMessageConfig, IMessageConnector
from api.db import Database


class IMessageLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(str(self.root / "test.db"))
        self.export_dir = self.root / "imessage" / "CC" / "export"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        (self.export_dir / "+15015550101.txt").write_text(
            "[2026-02-17 10:00:00]\n+15015550101\nHey Bailey\n"
        )
        self.connector = IMessageConnector(
            db=self.db,
            config=IMessageConfig(
                enabled=True,
                export_root=str(self.root / "imessage"),
                send_enabled_cc=True,
                send_enabled_acs=False,
                sender_user_cc="tester",
                sender_user_acs="acsops",
                rate_limit_per_minute=10,
            ),
        )

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_export_ingest_upserts_threads_and_contacts(self) -> None:
        result = self.connector.ingest_export(
            {
                "business_unit": "CC",
                "export_dir": str(self.export_dir),
                "idempotency_key": "unit-test-export",
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["threads_upserted"], 1)
        threads = self.db.list_recent_message_threads("CC", limit=5)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["business_unit"], "CC")

    def test_send_requires_approved_state(self) -> None:
        proposed = self.connector.propose_send(
            {"business_unit": "CC", "recipient": "+15015550101", "message": "Hello"}
        )
        self.assertTrue(proposed["ok"])
        attempt = self.connector.send_with_approval({"approval_id": proposed["approval_id"]})
        self.assertFalse(attempt["ok"])
        self.assertIn("approval must be in approved state", attempt["reason"])

    @mock.patch("subprocess.run")
    @mock.patch("os.getenv")
    def test_send_executes_when_approved(self, mock_getenv, mock_run) -> None:
        mock_getenv.return_value = "tester"
        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        proposed = self.connector.propose_send(
            {"business_unit": "CC", "recipient": "+15015550101", "message": "Approved message"}
        )
        approval_id = proposed["approval_id"]
        self.db.set_action_approval_state(approval_id, "approved")
        sent = self.connector.send_with_approval({"approval_id": approval_id})
        self.assertTrue(sent["ok"])
        approval = self.db.get_action_approval(approval_id)
        self.assertEqual(approval["state"], "executed")


if __name__ == "__main__":
    unittest.main()
