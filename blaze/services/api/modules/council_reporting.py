from __future__ import annotations

from api.db import Database


def build_council_report(db: Database) -> dict:
    cc = db.daily_brief("CC")
    acs = db.daily_brief("ACS")
    return {
        "cc": cc,
        "acs": acs,
        "totals": {
            "contacts": cc["contacts_total"] + acs["contacts_total"],
            "open_follow_ups": cc["open_follow_ups"] + acs["open_follow_ups"],
        },
    }

