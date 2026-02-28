#!/usr/bin/env python3
"""
intro_matcher.py — Find contacts who can intro you to a company or person.
Usage: python3 intro_matcher.py "Crunch Fitness"
       python3 intro_matcher.py "commercial real estate Houston"

Searches contact brain by company/notes/tags for direct matches,
then looks for 2nd-degree connections via shared companies.

2026-02-22
"""
import sqlite3, sys

DB = "/Users/_mxappservice/blaze-data/contacts/contacts.db"


def search_direct(conn, query):
    """Find contacts directly at a company or matching query."""
    q = f"%{query}%"
    return conn.execute("""
        SELECT name, company, role, email, phone, client_status,
               priority_score, last_contacted, how_we_know_them, notes
        FROM contacts
        WHERE (company LIKE ? OR notes LIKE ? OR business_tags LIKE ? OR how_we_know_them LIKE ?)
          AND name NOT LIKE '%Unknown%'
        ORDER BY priority_score DESC
        LIMIT 10
    """, (q, q, q, q)).fetchall()


def search_company_network(conn, query):
    """Find contacts at companies that might connect to the target."""
    # Extract industry keywords from the query
    keywords = [w for w in query.lower().split() if len(w) > 3]
    results = []
    for kw in keywords[:3]:
        rows = conn.execute("""
            SELECT name, company, role, priority_score, last_contacted
            FROM contacts
            WHERE (company LIKE ? OR business_tags LIKE ?)
              AND priority_score >= 40
              AND name NOT LIKE '%Unknown%'
            ORDER BY priority_score DESC
            LIMIT 5
        """, (f"%{kw}%", f"%{kw}%")).fetchall()
        for r in rows:
            if not any(x["name"] == r["name"] for x in results):
                results.append(r)
    return results[:8]


def format_days(ts):
    if not ts: return "never"
    try:
        from datetime import datetime, date
        d = datetime.fromisoformat(ts.replace("Z","")).date()
        n = (date.today() - d).days
        if n < 7: return f"{n}d ago"
        if n < 30: return f"{n//7}w ago"
        return f"{n//30}mo ago"
    except: return "?"


def run():
    if len(sys.argv) < 2:
        print("Usage: intro_matcher.py <company or keyword>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row

    direct = search_direct(conn, query)
    nearby = search_company_network(conn, query) if not direct else []

    conn.close()

    print(f"\nINTRO MATCH: '{query}'")
    print("─" * 48)

    if direct:
        print(f"\nDIRECT CONNECTIONS ({len(direct)}):")
        for r in direct:
            co = f" @ {r['company']}" if r['company'] else ""
            role = f" [{r['role']}]" if r['role'] else ""
            last = format_days(r['last_contacted'])
            print(f"  {r['name']}{role}{co}")
            print(f"    Score: {r['priority_score']:.0f}/100 | Last: {last} | Status: {r['client_status'] or '?'}")
            if r['how_we_know_them']:
                print(f"    How: {r['how_we_know_them']}")
    else:
        print("\nNo direct connections found.")

    if nearby:
        print(f"\nNEARBY NETWORK ({len(nearby)} — possible intros):")
        for r in nearby:
            co = f" @ {r['company']}" if r['company'] else ""
            last = format_days(r['last_contacted'])
            print(f"  {r['name']}{co} | Score: {r['priority_score']:.0f} | Last: {last}")

    if not direct and not nearby:
        print("No connections found. This is a cold outreach.")

    print("─" * 48)


if __name__ == "__main__":
    run()
