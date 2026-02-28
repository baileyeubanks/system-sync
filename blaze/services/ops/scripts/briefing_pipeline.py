#!/usr/bin/env python3
"""
briefing_pipeline.py — Deal pipeline section for morning briefing.
Imported by morning_briefing_v3.py as a clean module (no heredoc).
2026-02-22
"""
import sqlite3
from datetime import date

CONTACTS_DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"


def get_pipeline():
    """Return deal pipeline summary for morning briefing."""
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT name, company, deal_stage, deal_close_by, client_status
            FROM contacts
            WHERE deal_stage IN ('active','proposal_sent','prospecting')
            ORDER BY CASE deal_stage
              WHEN 'proposal_sent' THEN 1
              WHEN 'active' THEN 2
              WHEN 'prospecting' THEN 3
            END, deal_close_by
        """).fetchall()

        new_leads = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE deal_stage='new_lead'"
        ).fetchone()[0]

        conn.close()

        lines = []
        for r in rows:
            co = f" @ {r['company']}" if r['company'] else ""
            stage_label = {
                "proposal_sent": "PROPOSAL",
                "active":        "ACTIVE",
                "prospecting":   "TARGET",
            }.get(r['deal_stage'], r['deal_stage'].upper())

            close_str = ""
            if r['deal_close_by']:
                try:
                    close_date = date.fromisoformat(r['deal_close_by'])
                    days_left = (close_date - date.today()).days
                    close_str = f" [close {r['deal_close_by']} — {days_left}d]"
                    if days_left < 0:
                        close_str = f" [OVERDUE {abs(days_left)}d]"
                except Exception:
                    close_str = f" [close {r['deal_close_by']}]"

            lines.append(f"  [{stage_label}] {r['name']}{co}{close_str}")

        if new_leads:
            lines.append(f"  [{new_leads} NEW LEADS from Wix] \u2192 run WHOIS on top contacts")

        return "\n".join(lines) if lines else "No active deals."

    except Exception as e:
        return f"Pipeline unavailable: {e}"


if __name__ == "__main__":
    print(get_pipeline())
