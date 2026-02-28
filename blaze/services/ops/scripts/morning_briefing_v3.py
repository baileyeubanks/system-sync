#!/usr/bin/env python3
"""
Morning Briefing — Content Co-op V4
Bailey's daily command brief.
Text via Telegram + branded HTML email via Gmail DWD.

Covers: Marketing, Operations, Recruitment, Finance, Expansion.
Python 3.9 compatible.
"""

import sys
import os
import json
import sqlite3
import time
import re
import html as html_mod
import subprocess
import base64
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Config ─────────────────────────────────────
DATA_ROOT = "/Users/_mxappservice/blaze-data"
BLAZE_DB = "%s/blaze.db" % DATA_ROOT
KNOWLEDGE_DB = "%s/knowledge.db" % DATA_ROOT
EVENT_LOG_DB = "%s/event_stream.db" % DATA_ROOT
CONTACTS_DB = "%s/contacts/contacts.db" % DATA_ROOT
BLAZE_LOGS = "/Users/_mxappservice/blaze-logs"
FAILURE_MEMORY = "%s/failure-memory.json" % BLAZE_LOGS
SCRIPTS_DIR = "/Users/_mxappservice/ACS_CC_AUTOBOT/blaze-v4/ops/scripts"
SA_FILE = "/Users/_mxappservice/.gemini/antigravity/playground/perihelion-armstrong/service_account.json"
EMAIL_FROM = "blaze@contentco-op.com"
EMAIL_TO = "bailey@contentco-op.com"
BAILEY_TG = "telegram:7747110667"

CRYPTO_API = "https://api.crypto.com/v2/public/get-ticker"
CRYPTO_MAP = {
    "BTC-USD": "BTC_USDT", "ETH-USD": "ETH_USDT", "SOL-USD": "SOL_USDT",
    "BTC": "BTC_USDT", "ETH": "ETH_USDT", "SOL": "SOL_USDT",
    "DOGE": "DOGE_USDT", "XRP": "XRP_USDT", "ADA": "ADA_USDT",
    "AVAX": "AVAX_USDT", "LINK": "LINK_USDT", "DOT": "DOT_USDT",
    "CRO": "CRO_USDT", "ATOM": "ATOM_USDT",
}

SEP = "\u2501" * 48

sys.path.insert(0, SCRIPTS_DIR)


# ── Helpers ────────────────────────────────────

def _open_db(path):
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _esc(s):
    if not s:
        return ""
    return html_mod.escape(str(s))


# ── Data Fetchers (all return structured data) ─


def fetch_failures():
    try:
        if not os.path.exists(FAILURE_MEMORY):
            return []
        with open(FAILURE_MEMORY) as f:
            data = json.load(f)
        return data.get("failures", [])[-3:]
    except Exception:
        return []


def fetch_calendar():
    """Today's events for bailey@contentco-op.com."""
    try:
        from google_api_manager import get_todays_events
        events = get_todays_events("bailey@contentco-op.com")
        if not events:
            return []
        results = []
        for ev in events:
            results.append({
                "time": ev.get("time", ""),
                "title": ev.get("title", ""),
                "location": ev.get("location", ""),
            })
        return results
    except Exception:
        return []


def fetch_emails():
    """Recent scored emails (both accounts)."""
    results = []
    try:
        if os.path.exists(EVENT_LOG_DB):
            conn = _open_db(EVENT_LOG_DB)
            if conn:
                rows = conn.execute(
                    "SELECT sender, subject, score FROM events "
                    "WHERE source='gmail' "
                    "AND created_at > datetime('now', '-12 hours') "
                    "AND score >= 30 ORDER BY score DESC LIMIT 8"
                ).fetchall()
                conn.close()
                for sender, subject, score in rows:
                    results.append({
                        "sender": sender or "?",
                        "subject": subject or "",
                        "score": score or 0,
                    })
                if len(results) >= 2:
                    return results
    except Exception:
        pass
    try:
        from google_api_manager import get_recent_emails
        for acct in ["bailey@contentco-op.com", "caio@astrocleanings.com"]:
            for e in get_recent_emails(acct, max_results=4):
                results.append({
                    "sender": e.get("from", "?"),
                    "subject": e.get("subject", ""),
                    "score": 0,
                })
    except Exception:
        pass
    return results[:8]


def fetch_imessage():
    """Unread iMessage summary."""
    try:
        import imessage_reader
        result = imessage_reader.get_unread_summary(hours=24)
        return result if result else "No unread messages."
    except Exception:
        return "iMessage unavailable."


def fetch_news():
    """News headlines from event stream (RSS + X)."""
    try:
        if not os.path.exists(EVENT_LOG_DB):
            return []
        conn = _open_db(EVENT_LOG_DB)
        if not conn:
            return []
        rows = conn.execute(
            "SELECT sender, subject, body, source FROM events "
            "WHERE source IN ('x_news', 'rss_news') "
            "AND created_at > datetime('now', '-24 hours') "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        results = []
        seen = set()
        for sender, subject, body, source in rows:
            text = subject if subject else body
            if not text or text in seen:
                continue
            seen.add(text)
            tag = sender.upper()[:18] if sender else source.upper()
            results.append({"tag": tag, "headline": text[:120]})
            if len(results) >= 7:
                break
        return results
    except Exception:
        return []


def fetch_youtube():
    """Recent YouTube insights."""
    try:
        conn = sqlite3.connect(KNOWLEDGE_DB)
        rows = conn.execute(
            "SELECT channel_name, insight FROM youtube_insights "
            "ORDER BY id DESC LIMIT 3"
        ).fetchall()
        conn.close()
        results = []
        for channel, insight in rows:
            results.append({
                "channel": channel[:20] if channel else "?",
                "insight": insight[:140] if insight else "",
            })
        return results
    except Exception:
        return []


def _fetch_ticker(instrument):
    try:
        url = "%s?instrument_name=%s" % (CRYPTO_API, instrument)
        req = Request(url, headers={"User-Agent": "Blaze/4.0"})
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        result = data.get("result", {}).get("data", [])
        if isinstance(result, list) and result:
            return result[0]
        if isinstance(result, dict):
            return result
        return None
    except Exception:
        return None


def fetch_markets():
    """Crypto watchlist + top mover + CoinDesk headline."""
    tickers_raw = []
    try:
        conn = sqlite3.connect(KNOWLEDGE_DB)
        tickers_raw = [r[0] for r in conn.execute(
            "SELECT ticker FROM watchlist LIMIT 12"
        ).fetchall()]
        conn.close()
    except Exception:
        pass

    if not tickers_raw:
        return {"tickers": [], "top_mover": None, "headline": None, "stocks": []}

    crypto_pairs = []
    stocks = []
    for t in tickers_raw:
        inst = CRYPTO_MAP.get(t)
        if inst:
            crypto_pairs.append((t, inst))
        else:
            stocks.append(t)

    tickers = []
    top_mover = None
    top_change = 0.0

    if crypto_pairs:
        ticker_data = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {}
            for display, inst in crypto_pairs:
                futs[pool.submit(_fetch_ticker, inst)] = display
            for fut in as_completed(futs):
                display = futs[fut]
                try:
                    ticker_data[display] = fut.result()
                except Exception:
                    ticker_data[display] = None

        for t, _ in crypto_pairs:
            d = ticker_data.get(t)
            if not d:
                tickers.append({
                    "symbol": t, "price": 0, "change": 0,
                    "price_fmt": "--", "alert": False,
                })
                continue
            price = float(d.get("a", d.get("last", 0)))
            change = float(d.get("c", d.get("change", 0))) * 100

            if price >= 1000:
                p_fmt = "${:,.0f}".format(price)
            elif price >= 1:
                p_fmt = "${:.2f}".format(price)
            else:
                p_fmt = "${:.4f}".format(price)

            tickers.append({
                "symbol": t, "price": price, "change": change,
                "price_fmt": p_fmt, "alert": abs(change) >= 5,
            })

            if abs(change) > abs(top_change):
                top_change = change
                top_mover = {"symbol": t, "change": change}

    # CoinDesk headline
    headline = None
    try:
        if os.path.exists(EVENT_LOG_DB):
            conn = _open_db(EVENT_LOG_DB)
            if conn:
                row = conn.execute(
                    "SELECT subject FROM events "
                    "WHERE source='rss_news' AND sender='CoinDesk' "
                    "AND created_at > datetime('now', '-24 hours') "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                conn.close()
                if row:
                    headline = row[0]
    except Exception:
        pass

    return {
        "tickers": tickers,
        "top_mover": top_mover if top_mover and abs(top_change) >= 3 else None,
        "headline": headline,
        "stocks": stocks,
    }


def fetch_relationships():
    """Follow-ups due + contacts going cold + active clients."""
    data = {"followups": [], "cold": [], "clients": []}
    try:
        conn = sqlite3.connect(CONTACTS_DB, timeout=5)

        due = conn.execute(
            "SELECT name, company, client_status, follow_up_due, priority_score "
            "FROM contacts WHERE follow_up_due IS NOT NULL "
            "AND follow_up_due <= date('now', '+7 days') "
            "AND priority_score >= 50 "
            "ORDER BY priority_score DESC, follow_up_due LIMIT 5"
        ).fetchall()
        for r in due:
            co = r[1].rstrip(";").strip() if r[1] and r[1].strip() else ""
            data["followups"].append({
                "name": r[0], "company": co, "due": r[3],
            })

        cold = conn.execute(
            "SELECT name, company, last_contacted, priority_score "
            "FROM contacts WHERE priority_score >= 60 "
            "AND last_contacted IS NOT NULL "
            "AND julianday('now') - julianday(last_contacted) >= 14 "
            "AND (follow_up_due IS NULL OR follow_up_due > date('now')) "
            "ORDER BY priority_score DESC LIMIT 5"
        ).fetchall()
        for r in cold:
            co = r[1].rstrip(";").strip() if r[1] and r[1].strip() else ""
            try:
                days = (date.today() - datetime.fromisoformat(
                    r[2].replace("Z", "")
                ).date()).days
            except Exception:
                days = 0
            data["cold"].append({
                "name": r[0], "company": co, "days": days,
            })

        clients = conn.execute(
            "SELECT name, company FROM contacts "
            "WHERE client_status = 'active-client' "
            "ORDER BY priority_score DESC LIMIT 3"
        ).fetchall()
        for r in clients:
            co = r[1].rstrip(";").strip() if r[1] and r[1].strip() else ""
            data["clients"].append({"name": r[0], "company": co})

        conn.close()
    except Exception:
        pass
    return data


def fetch_pipeline():
    """Deal pipeline summary."""
    try:
        from briefing_pipeline import get_pipeline
        result = get_pipeline()
        return result if result else "No active deals."
    except Exception:
        return "Pipeline unavailable."


def fetch_goals():
    """Active goals from knowledge.db."""
    try:
        conn = sqlite3.connect(KNOWLEDGE_DB)
        lg = [r[0] for r in conn.execute(
            "SELECT goal FROM goals WHERE type='long' AND status='active' "
            "ORDER BY id LIMIT 4"
        ).fetchall()]
        sg = [r[0] for r in conn.execute(
            "SELECT goal FROM goals WHERE type='short' AND status='active' "
            "ORDER BY id LIMIT 4"
        ).fetchall()]
        conn.close()
        return {"long": lg, "short": sg}
    except Exception:
        return {"long": [], "short": []}


def fetch_overnight():
    """Last 12h cron runs."""
    try:
        from blaze_helper import get_db
        db = get_db("cron")
        jobs = db.execute(
            "SELECT cr.job_name, cr.status, cr.output_summary "
            "FROM cron_runs cr INNER JOIN ("
            "  SELECT job_name, MAX(completed_at) as max_at "
            "  FROM cron_runs WHERE completed_at > datetime('now', '-12 hours') "
            "  GROUP BY job_name"
            ") latest ON cr.job_name = latest.job_name "
            "AND cr.completed_at = latest.max_at "
            "ORDER BY cr.completed_at DESC LIMIT 10"
        ).fetchall()
        db.close()
        results = []
        for j, s, sm in jobs:
            results.append({
                "job": j, "status": s,
                "summary": sm or s,
            })
        return results
    except Exception:
        return []


def fetch_cost():
    """API cost last 24h."""
    try:
        from blaze_helper import get_db
        db = get_db("usage")
        row = db.execute(
            "SELECT SUM(cost_usd), COUNT(*) FROM api_calls "
            "WHERE ts > datetime('now', '-24 hours')"
        ).fetchone()
        db.close()
        return "$%.4f across %d calls" % (row[0] or 0, row[1] or 0)
    except Exception:
        return "$0.00"


def fetch_stream_stats():
    """Event stream activity summary."""
    try:
        if not os.path.exists(EVENT_LOG_DB):
            return "Not started."
        conn = _open_db(EVENT_LOG_DB)
        if not conn:
            return "Unavailable."
        row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN action='pushed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN action='batched' THEN 1 ELSE 0 END) "
            "FROM events WHERE created_at > datetime('now', '-24 hours')"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return "%d events, %d pushed, %d batched" % (
                row[0], row[1] or 0, row[2] or 0
            )
        return "No events yet."
    except Exception:
        return "Unavailable."


# ── Text Builder (Telegram) ───────────────────

def build_text(data):
    now = datetime.now()
    day_str = now.strftime("%A %b %-d").upper()
    time_str = now.strftime("%-I:%M %p")

    out = []
    out.append("GOOD MORNING, BAILEY -- %s" % day_str)
    out.append(SEP)

    failures = data.get("failures", [])
    if failures:
        out.append("")
        out.append("NEEDS ATTENTION")
        out.append(SEP)
        for item in failures:
            out.append("  ! %s" % item)

    cal = data.get("calendar", [])
    out.append("")
    out.append("TODAY")
    out.append(SEP)
    if cal:
        for ev in cal:
            loc = " (%s)" % ev["location"] if ev.get("location") else ""
            out.append("  %s - %s%s" % (ev["time"], ev["title"], loc))
    else:
        out.append("  No events scheduled.")

    emails = data.get("emails", [])
    out.append("")
    out.append("EMAIL  %s" % time_str)
    out.append(SEP)
    if emails:
        for e in emails:
            out.append("  -> %s - %s" % (e["sender"], e["subject"]))
    else:
        out.append("  Inbox clear.")

    imsg = data.get("imessage", "")
    out.append("")
    out.append("iMESSAGE")
    out.append(SEP)
    for line in imsg.split("\n"):
        if line.strip():
            out.append("  %s" % line.strip())

    youtube = data.get("youtube", [])
    if youtube:
        out.append("")
        out.append("WATCH")
        out.append(SEP)
        for yt in youtube:
            out.append("  [%s] %s" % (yt["channel"], yt["insight"]))

    news = data.get("news", [])
    if news:
        out.append("")
        out.append("NEWS")
        out.append(SEP)
        for n in news:
            out.append("  [%s] %s" % (n["tag"], n["headline"]))

    markets = data.get("markets", {})
    tickers = markets.get("tickers", [])
    if tickers:
        out.append("")
        out.append("MARKETS")
        out.append(SEP)
        tm = markets.get("top_mover")
        if tm:
            arrow = "UP" if tm["change"] >= 0 else "DOWN"
            out.append("  TOP MOVER: %s %s %.1f%%" % (
                tm["symbol"], arrow, abs(tm["change"])
            ))
            out.append("")
        for tk in tickers:
            sign = "+" if tk["change"] >= 0 else ""
            alert = "  *" if tk["alert"] else ""
            out.append("  %-10s %s (%s%.2f%%)%s" % (
                tk["symbol"], tk["price_fmt"], sign, tk["change"], alert
            ))
        hl = markets.get("headline")
        if hl:
            out.append("")
            out.append("  COINDESK: %s" % hl[:90])

    rels = data.get("relationships", {})
    has_rels = (rels.get("followups") or rels.get("cold") or rels.get("clients"))
    if has_rels:
        out.append("")
        out.append("RELATIONSHIPS")
        out.append(SEP)
        for fu in rels.get("followups", []):
            co = " @ %s" % fu["company"] if fu["company"] else ""
            out.append("  -> %s%s [due %s]" % (fu["name"], co, fu["due"]))
        for c in rels.get("cold", []):
            co = " @ %s" % c["company"] if c["company"] else ""
            out.append("  -> %s%s [%dd silent]" % (c["name"], co, c["days"]))

    pipeline = data.get("pipeline", "")
    if pipeline and pipeline != "No active deals." and pipeline != "Pipeline unavailable.":
        out.append("")
        out.append("PIPELINE")
        out.append(SEP)
        for line in pipeline.split("\n"):
            if line.strip():
                out.append("  %s" % line.strip())

    goals = data.get("goals", {})
    lg = goals.get("long", [])
    sg = goals.get("short", [])
    if lg or sg:
        out.append("")
        out.append("GOALS IN MOTION")
        out.append(SEP)
        if lg:
            out.append("  LONG TERM:")
            for g in lg:
                out.append("  - %s" % g)
        if sg:
            out.append("  THIS WEEK:")
            for g in sg:
                out.append("  -> %s" % g)

    overnight = data.get("overnight", [])
    if overnight:
        out.append("")
        out.append("OVERNIGHT")
        out.append(SEP)
        for ov in overnight:
            icon = "OK" if ov["status"] == "success" else "FAIL"
            out.append("  %s %s: %s" % (icon, ov["job"], ov["summary"]))
    cost = data.get("cost", "")
    if cost:
        out.append("  %s" % cost)
    stats = data.get("stream_stats", "")
    if stats:
        out.append("  Events: %s" % stats)

    elapsed = data.get("elapsed", 0)
    out.append("")
    out.append(SEP)
    out.append("  Built in %.1fs. Reply here if you need anything." % elapsed)
    out.append(SEP)

    return "\n".join(out)


# ── HTML Builder (Email) ──────────────────────

def build_html(data):
    """Branded Content Co-op dark template — gold accent."""
    h = []
    e = _esc

    now = datetime.now()
    date_short = now.strftime("%b %-d")
    tmrw_day = (now + timedelta(days=1)).strftime("%A").upper()

    cal = data.get("calendar", [])
    emails = data.get("emails", [])
    failures = data.get("failures", [])
    news = data.get("news", [])
    youtube = data.get("youtube", [])
    markets = data.get("markets", {})
    tickers = markets.get("tickers", [])
    rels = data.get("relationships", {})
    goals = data.get("goals", {})
    overnight = data.get("overnight", [])
    elapsed = data.get("elapsed", 0)

    n_meetings = len(cal)
    n_emails = len(emails)
    n_actions = len(failures)
    n_news = len(news)

    # ── Document ──
    h.append('<!DOCTYPE html>')
    h.append('<html><head><meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    h.append('<title>CC Daily Brief</title></head>')
    h.append('<body style="margin:0;padding:0;background:#060e1a;'
             'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
             'Roboto,sans-serif;">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
             'style="background:#060e1a;padding:24px 0;">')
    h.append('<tr><td align="center">')
    h.append('<table width="540" cellpadding="0" cellspacing="0" '
             'style="background:#0f1620;border-radius:16px;overflow:hidden;'
             'border:1px solid rgba(240,180,41,0.12);">')

    # ── Header ──
    h.append('<tr><td style="background:linear-gradient(180deg,#0f1620 0%%,'
             '#17202e 50%%,#0f1620 100%%);padding:28px 24px 20px;'
             'border-bottom:1px solid rgba(240,180,41,0.1);">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0"><tr>')
    h.append('<td width="52" valign="top">')
    h.append('<div style="width:44px;height:44px;border-radius:10px;'
             'background:linear-gradient(135deg,#f0b429,#f5c842);'
             'text-align:center;line-height:44px;font-size:18px;'
             'font-weight:800;color:#0f1620;font-family:-apple-system,'
             'BlinkMacSystemFont,sans-serif;">CC</div>')
    h.append('</td>')
    h.append('<td style="padding-left:12px;">')
    h.append('<div style="color:#f0b429;font-size:10px;font-weight:700;'
             'letter-spacing:3px;margin-bottom:4px;">CONTENT CO-OP</div>')
    h.append('<div style="color:#e6edf3;font-size:22px;font-weight:800;'
             'letter-spacing:-0.5px;">Daily Command Brief</div>')
    h.append('</td>')
    h.append('<td align="right" valign="top">')
    h.append('<div style="color:#7d8590;font-size:12px;text-align:right;">'
             + e(now.strftime("%A")) + '<br>')
    h.append('<span style="color:#f0b429;font-size:18px;font-weight:800;">'
             + e(date_short) + '</span></div>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # ── Pulse Bar ──
    h.append('<tr><td style="padding:20px 24px 0;">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
             'style="border-radius:10px;overflow:hidden;"><tr>')

    pulse = [
        (str(n_meetings), "TODAY", "rgba(240,180,41,0.12)", "#f0b429"),
        (str(n_emails), "INBOX", "rgba(88,166,255,0.10)", "#58a6ff"),
        (str(n_actions), "ALERTS", "rgba(230,124,115,0.10)", "#e67c73"),
        (str(n_news), "NEWS", "rgba(102,187,106,0.10)", "#66bb6a"),
    ]
    for val, label, bg, fg in pulse:
        h.append('<td width="25%%" style="background:' + bg
                 + ';padding:14px 0;text-align:center;">')
        h.append('<div style="color:' + fg
                 + ';font-size:24px;font-weight:800;">' + val + '</div>')
        h.append('<div style="color:#546e8a;font-size:9px;font-weight:700;'
                 'letter-spacing:1.5px;margin-top:2px;">' + label + '</div>')
        h.append('</td>')
    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Action Items / Failures ──
    if failures:
        h.append('<tr><td style="padding:20px 24px 16px;">')
        h.append('<table width="100%%" cellpadding="0" cellspacing="0" '
                 'style="background:rgba(230,124,115,0.05);'
                 'border:1px solid rgba(230,124,115,0.12);border-radius:10px;">')
        h.append('<tr><td style="padding:16px 20px;">')
        h.append('<div style="color:#e67c73;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;margin-bottom:10px;">'
                 '&#9888; NEEDS ATTENTION</div>')
        h.append('<table width="100%%" cellpadding="0" cellspacing="0">')
        for item in failures:
            h.append('<tr><td style="padding:3px 0;color:#e6edf3;font-size:13px;">')
            h.append('<span style="color:#e67c73;margin-right:8px;">'
                     '&#8227;</span> ' + e(item))
            h.append('</td></tr>')
        h.append('</table></td></tr></table>')
        h.append('</td></tr>')

    # ── Today's Schedule ──
    h.append('<tr><td style="padding:8px 24px 0;">')
    h.append('<div style="color:#f0b429;font-size:10px;font-weight:800;'
             'letter-spacing:2px;">TODAY\'S SCHEDULE</div>')
    h.append('<div style="height:2px;background:linear-gradient(to right,'
             '#f0b429,rgba(240,180,41,0.2),transparent);margin-top:8px;'
             'border-radius:1px;"></div>')
    h.append('</td></tr>')
    h.append('<tr><td style="padding:14px 24px;">')

    if cal:
        h.append('<table width="100%%" cellpadding="0" cellspacing="0">')
        for ev in cal:
            h.append('<tr>')
            h.append('<td width="70" style="color:#f0b429;font-size:12px;'
                     'font-weight:700;padding:6px 0;">' + e(ev["time"]) + '</td>')
            h.append('<td style="color:#e6edf3;font-size:13px;font-weight:600;'
                     'padding:6px 0;">' + e(ev["title"]))
            if ev.get("location"):
                h.append('<br><span style="color:#7d8590;font-size:11px;'
                         'font-weight:400;">' + e(ev["location"]) + '</span>')
            h.append('</td></tr>')
        h.append('</table>')
    else:
        h.append('<div style="color:#7d8590;font-size:13px;">No events scheduled.</div>')
    h.append('</td></tr>')

    # ── Email ──
    if emails:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#58a6ff;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">INBOX</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#58a6ff,rgba(88,166,255,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8590;font-size:12px;line-height:2.1;">')
        for em in emails:
            sender = e(em["sender"].split("<")[0].strip())
            subj = e(em["subject"])
            h.append('<span style="color:#e6edf3;font-weight:600;">'
                     + sender + '</span> &mdash; ' + subj + '<br>')
        h.append('</div></td></tr>')

    # ── Markets ──
    if tickers:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#f0b429;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">MARKETS</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#f0b429,rgba(240,180,41,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')

        # Top mover badge
        tm = markets.get("top_mover")
        if tm:
            tm_color = "#66bb6a" if tm["change"] >= 0 else "#e67c73"
            tm_arrow = "&#9650;" if tm["change"] >= 0 else "&#9660;"
            h.append('<div style="background:rgba(240,180,41,0.06);'
                     'border:1px solid rgba(240,180,41,0.12);'
                     'border-radius:8px;padding:10px 16px;margin-bottom:14px;">')
            h.append('<span style="color:#f0b429;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">TOP MOVER</span>')
            h.append('<span style="color:#e6edf3;font-size:14px;font-weight:700;'
                     'margin-left:10px;">' + e(tm["symbol"]) + '</span>')
            h.append('<span style="color:' + tm_color
                     + ';font-size:14px;font-weight:700;margin-left:6px;">'
                     + tm_arrow + ' %.1f%%</span>' % abs(tm["change"]))
            h.append('</div>')

        # Ticker table
        h.append('<table width="100%%" cellpadding="0" cellspacing="0">')
        for tk in tickers:
            chg_color = "#66bb6a" if tk["change"] >= 0 else "#e67c73"
            sign = "+" if tk["change"] >= 0 else ""
            alert_mark = " &#9733;" if tk["alert"] else ""
            h.append('<tr>')
            h.append('<td style="padding:5px 0;color:#e6edf3;font-size:12px;'
                     'font-weight:700;width:60px;">' + e(tk["symbol"]) + '</td>')
            h.append('<td style="padding:5px 0;color:#e6edf3;font-size:12px;">'
                     + e(tk["price_fmt"]) + '</td>')
            h.append('<td align="right" style="padding:5px 0;color:' + chg_color
                     + ';font-size:12px;font-weight:600;">'
                     + sign + '%.2f%%' % tk["change"] + alert_mark + '</td>')
            h.append('</tr>')
        h.append('</table>')

        hl = markets.get("headline")
        if hl:
            h.append('<div style="margin-top:12px;padding:10px 14px;'
                     'background:rgba(88,166,255,0.05);'
                     'border-radius:8px;border:1px solid rgba(88,166,255,0.1);">')
            h.append('<span style="color:#58a6ff;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">COINDESK</span>')
            h.append('<div style="color:#e6edf3;font-size:12px;margin-top:4px;">'
                     + e(hl[:100]) + '</div>')
            h.append('</div>')

        h.append('</td></tr>')

    # ── News ──
    if news:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#66bb6a;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">NEWS</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#66bb6a,rgba(102,187,106,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8590;font-size:12px;line-height:2;">')
        for n in news:
            h.append('<span style="color:#f0b429;font-weight:600;font-size:10px;">'
                     '[' + e(n["tag"]) + ']</span> '
                     '<span style="color:#e6edf3;">'
                     + e(n["headline"]) + '</span><br>')
        h.append('</div></td></tr>')

    # ── Learning (YouTube) ──
    if youtube:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#ce93d8;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">LEARNING</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#ce93d8,rgba(206,147,216,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8590;font-size:12px;line-height:2;">')
        for yt in youtube:
            h.append('<span style="color:#ce93d8;font-weight:600;font-size:10px;">'
                     '[' + e(yt["channel"]) + ']</span> '
                     '<span style="color:#e6edf3;">'
                     + e(yt["insight"]) + '</span><br>')
        h.append('</div></td></tr>')

    # ── Relationships ──
    fu = rels.get("followups", [])
    cold = rels.get("cold", [])
    if fu or cold:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#ffa726;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">RELATIONSHIPS</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#ffa726,rgba(255,167,38,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8590;font-size:12px;line-height:2;">')
        if fu:
            h.append('<span style="color:#ffa726;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">FOLLOW UP</span><br>')
            for f in fu:
                co = " @ " + e(f["company"]) if f["company"] else ""
                h.append('<span style="color:#e6edf3;font-weight:600;">'
                         + e(f["name"]) + '</span>' + co
                         + ' <span style="color:#546e8a;">[due '
                         + e(f["due"]) + ']</span><br>')
        if cold:
            if fu:
                h.append('<br>')
            h.append('<span style="color:#e67c73;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">GOING COLD</span><br>')
            for c in cold:
                co = " @ " + e(c["company"]) if c["company"] else ""
                h.append('<span style="color:#e6edf3;font-weight:600;">'
                         + e(c["name"]) + '</span>' + co
                         + ' <span style="color:#546e8a;">['
                         + str(c["days"]) + 'd silent]</span><br>')
        h.append('</div></td></tr>')

    # ── Goals ──
    lg = goals.get("long", [])
    sg = goals.get("short", [])
    if lg or sg:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#f0b429;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">GOALS IN MOTION</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#f0b429,rgba(240,180,41,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#7d8590;font-size:12px;line-height:2;">')
        if lg:
            h.append('<span style="color:#f0b429;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">LONG TERM</span><br>')
            for g in lg:
                h.append('<span style="color:#e6edf3;">- ' + e(g) + '</span><br>')
        if sg:
            if lg:
                h.append('<br>')
            h.append('<span style="color:#58a6ff;font-size:10px;font-weight:700;'
                     'letter-spacing:1px;">THIS WEEK</span><br>')
            for g in sg:
                h.append('<span style="color:#e6edf3;">&rarr; '
                         + e(g) + '</span><br>')
        h.append('</div></td></tr>')

    # ── Operations (compact) ──
    if overnight:
        h.append('<tr><td style="padding:8px 24px 0;">')
        h.append('<div style="color:#546e8a;font-size:10px;font-weight:800;'
                 'letter-spacing:2px;">OPERATIONS</div>')
        h.append('<div style="height:2px;background:linear-gradient(to right,'
                 '#546e8a,rgba(84,110,138,0.2),transparent);margin-top:8px;'
                 'border-radius:1px;"></div>')
        h.append('</td></tr>')
        h.append('<tr><td style="padding:14px 24px;">')
        h.append('<div style="color:#546e8a;font-size:11px;line-height:1.9;">')

        ok_count = sum(1 for o in overnight if o["status"] == "success")
        fail_count = sum(1 for o in overnight if o["status"] != "success")
        h.append('<span style="color:#66bb6a;">%d OK</span>' % ok_count)
        if fail_count:
            h.append(' &bull; <span style="color:#e67c73;">%d FAIL</span>' % fail_count)
        h.append(' &bull; %d jobs overnight<br>' % len(overnight))

        for ov in overnight:
            if ov["status"] != "success":
                h.append('<span style="color:#e67c73;">FAIL</span> '
                         '<span style="color:#7d8590;">'
                         + e(ov["job"]) + ': ' + e(ov["summary"]) + '</span><br>')

        cost = data.get("cost", "")
        if cost:
            h.append('<span style="color:#7d8590;">' + e(cost) + '</span><br>')
        stats = data.get("stream_stats", "")
        if stats:
            h.append('<span style="color:#7d8590;">Events: '
                     + e(stats) + '</span>')

        h.append('</div></td></tr>')

    # ── Footer ──
    h.append('<tr><td style="padding:24px 24px;">')
    h.append('<table width="100%%" cellpadding="0" cellspacing="0"><tr>')
    h.append('<td style="color:#2d4058;font-size:11px;">'
             'Content Co-op &bull; Houston, TX</td>')
    h.append('<td align="right" style="color:#1e3048;font-size:10px;">'
             'Powered by Blaze &bull; Built in %.1fs</td>' % elapsed)
    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Close ──
    h.append('</table></td></tr></table>')
    h.append('</body></html>')

    return '\n'.join(h)


# ── Delivery ──────────────────────────────────

def send_telegram(message):
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    cmd = [
        "/usr/local/bin/openclaw", "message", "send",
        "--channel", "telegram",
        "--account", "main",
        "--target", BAILEY_TG,
        "--message", message,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode == 0:
            return True, "sent"
        return False, result.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as ex:
        return False, str(ex)


def send_email(html_body, text_body, subject):
    """Send branded HTML email via Gmail DWD."""
    try:
        from google.oauth2 import service_account as sa_mod
        from googleapiclient.discovery import build

        creds = sa_mod.Credentials.from_service_account_file(
            SA_FILE,
            scopes=["https://www.googleapis.com/auth/gmail.compose"],
        )
        creds = creds.with_subject(EMAIL_FROM)
        gmail = build("gmail", "v1", credentials=creds)

        msg = MIMEMultipart("alternative")
        msg["To"] = EMAIL_TO
        msg["From"] = "Blaze <%s>" % EMAIL_FROM
        msg["Subject"] = subject

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        return True, "sent"
    except Exception as ex:
        return False, str(ex)[:200]


def log_cron_result(status, summary, error=None):
    try:
        from blaze_helper import log_cron
        log_cron("morning_briefing_v3", status, summary)
    except Exception:
        try:
            conn = _open_db(BLAZE_DB)
            if conn:
                now_str = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                conn.execute(
                    "INSERT INTO cron_runs "
                    "(job_name, started_at, completed_at, status, "
                    "output_summary, error_message) VALUES (?, ?, ?, ?, ?, ?)",
                    ("morning_briefing_v3", now_str, now_str,
                     status, summary, error),
                )
                conn.commit()
                conn.close()
        except Exception:
            pass


# ── Main ──────────────────────────────────────

def main():
    start = time.time()

    # Parallel data fetch
    results = {}
    fetch_tasks = {
        "calendar": fetch_calendar,
        "emails": fetch_emails,
        "imessage": fetch_imessage,
        "news": fetch_news,
        "youtube": fetch_youtube,
        "markets": fetch_markets,
        "relationships": fetch_relationships,
        "pipeline": fetch_pipeline,
        "goals": fetch_goals,
        "overnight": fetch_overnight,
        "cost": fetch_cost,
        "stream_stats": fetch_stream_stats,
        "failures": fetch_failures,
    }

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fn): name for name, fn in fetch_tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as ex:
                if name in ("markets", "goals", "relationships"):
                    results[name] = {} if name != "markets" else {
                        "tickers": [], "top_mover": None,
                        "headline": None, "stocks": [],
                    }
                elif name in ("calendar", "emails", "news", "youtube",
                              "overnight", "failures"):
                    results[name] = []
                else:
                    results[name] = "Error: %s" % ex

    data = dict(results)
    data["elapsed"] = time.time() - start

    text = build_text(data)
    html = build_html(data)

    test_mode = "--test" in sys.argv

    if test_mode:
        print(text)
        with open("/tmp/cc_briefing_preview.html", "w") as f:
            f.write(html)
        print("\nHTML preview: /tmp/cc_briefing_preview.html")
        return

    # Send Telegram
    tg_ok, tg_detail = send_telegram(text)

    # Send email
    now = datetime.now()
    n_mtg = len(data.get("calendar", []))
    subject = "Daily Command Brief \u2014 %s %s" % (
        now.strftime("%a"), now.strftime("%b %-d"),
    )
    email_ok, email_detail = send_email(html, text, subject)

    # Report
    parts = []
    if tg_ok:
        parts.append("telegram:ok")
    else:
        parts.append("telegram:FAIL(%s)" % tg_detail[:60])
    if email_ok:
        parts.append("email:ok")
    else:
        parts.append("email:FAIL(%s)" % email_detail[:60])

    status = "success" if (tg_ok or email_ok) else "fail"
    summary = "%s (%.1fs)" % (", ".join(parts), data["elapsed"])
    log_cron_result(status, summary)

    print("CC Briefing V4: %s" % ", ".join(parts))

    if not tg_ok and not email_ok:
        print(text)
        sys.exit(1)


if __name__ == "__main__":
    main()
