from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.db import Database


class LearningEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_learning_digest_returns_active_insights(self) -> None:
        source_id = self.db.upsert_learning_source(
            business_unit="CC",
            source_type="youtube",
            source_ref="channel:example",
            title="Example Channel",
            metadata={"topic": "video-production"},
        )
        learning_item_id = self.db.add_learning_item(
            business_unit="CC",
            source_type="youtube",
            source_ref="video:abc123",
            title="Corporate interview framing tips",
            summary_text="Great structure for executive interviews.",
            relevance_score=0.93,
            tags=["video", "corporate"],
            idempotency_key="li:abc123",
            source_id=source_id,
        )
        self.db.add_learning_insight(
            business_unit="CC",
            learning_item_id=learning_item_id,
            insight_type="outreach_angle",
            title="Use executive narrative framing",
            insight_text="Lead with retention and trust outcomes.",
            confidence=0.82,
            priority=1,
            tags=["outreach", "energy"],
        )

        digest = self.db.list_learning_digest("CC", limit=10, tag="outreach")
        self.assertEqual(digest["business_unit"], "CC")
        self.assertEqual(digest["count"], 1)
        self.assertEqual(digest["insights"][0]["insight_type"], "outreach_angle")

    def test_learning_search_returns_items_and_insights(self) -> None:
        learning_item_id = self.db.add_learning_item(
            business_unit="ACS",
            source_type="youtube",
            source_ref="video:cleaningops",
            title="Cleaning route optimization",
            summary_text="Dispatch and routing automation for field teams.",
            relevance_score=0.88,
            tags=["dispatch"],
            idempotency_key="li:cleaningops",
        )
        self.db.add_learning_insight(
            business_unit="ACS",
            learning_item_id=learning_item_id,
            insight_type="ops",
            title="Batch jobs by geography",
            insight_text="Reduce drive time by zone assignments.",
            confidence=0.9,
            priority=2,
            tags=["dispatch", "routing"],
        )

        result = self.db.search_learning_knowledge("dispatch", business_unit="ACS", limit=10)
        self.assertTrue(len(result["items"]) >= 1)
        self.assertTrue(len(result["insights"]) >= 1)

    def test_outreach_draft_syncs_with_approval_state(self) -> None:
        created = self.db.create_outreach_draft(
            business_unit="CC",
            channel="email",
            recipient="decision.maker@example.com",
            subject="Pilot video concept",
            body_text="Draft proposal body",
            rationale="Matched from learning insights",
            source_insight_ids=[1, 2],
        )
        approval_id = created["approval_id"]
        drafts = self.db.list_outreach_drafts("CC", status="proposed", limit=10)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["approval_id"], approval_id)

        self.db.set_action_approval_state(approval_id, "approved")
        self.db.sync_outreach_draft_approval(approval_id, "approved")
        approved = self.db.list_outreach_drafts("CC", status="approved", limit=10)
        self.assertEqual(len(approved), 1)


if __name__ == "__main__":
    unittest.main()
