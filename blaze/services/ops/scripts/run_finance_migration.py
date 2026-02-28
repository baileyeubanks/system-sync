#!/usr/bin/env python3
"""
Run the finance schema migration against Supabase PostgreSQL.
Uses psycopg2 with the direct PostgreSQL pooler connection.

Usage:
    python3 run_finance_migration.py <db_password>

Find the password in: Supabase Dashboard -> Settings -> Database -> Connection string
"""

import sys
import os

PROJECT_REF = "briokwdoonawhxisbydy"
DB_HOST = "aws-0-us-east-1.pooler.supabase.com"
DB_PORT = 6543
DB_NAME = "postgres"
DB_USER = "postgres." + PROJECT_REF


def main():
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        if len(sys.argv) > 1:
            password = sys.argv[1]
        else:
            print("Usage: python3 run_finance_migration.py <db_password>")
            print("  or: SUPABASE_DB_PASSWORD=xxx python3 run_finance_migration.py")
            print("")
            print("Find the password in Supabase Dashboard:")
            print("  Settings -> Database -> Connection string -> Password")
            sys.exit(1)

    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip3 install psycopg2-binary")
        sys.exit(1)

    # Find the SQL file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "finance_migration.sql"),
        "/tmp/finance_migration.sql",
        os.path.expanduser("~/ACS_CC_AUTOBOT/blaze-v4/ops/scripts/finance_migration.sql"),
    ]
    sql_path = None
    for c in candidates:
        if os.path.exists(c):
            sql_path = c
            break

    if not sql_path:
        print("ERROR: Cannot find finance_migration.sql")
        sys.exit(1)

    with open(sql_path, "r") as f:
        sql = f.read()

    print("Connecting to Supabase PostgreSQL (%s)..." % PROJECT_REF)

    # Try multiple connection strategies
    hosts_to_try = [
        (DB_HOST, DB_PORT, "pooler (6543)"),
        (DB_HOST, 5432, "pooler (5432)"),
        ("db." + PROJECT_REF + ".supabase.co", 5432, "direct (5432)"),
        ("db." + PROJECT_REF + ".supabase.co", 6543, "direct (6543)"),
    ]

    conn = None
    for host, port, label in hosts_to_try:
        try:
            print("  Trying %s:%d (%s)..." % (host, port, label))
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=DB_NAME,
                user=DB_USER,
                password=password,
                sslmode="require",
                connect_timeout=10,
            )
            print("  Connected via %s!" % label)
            break
        except psycopg2.OperationalError as e:
            err_str = str(e).strip().split("\n")[0]
            print("  Failed: %s" % err_str)
            continue

    if conn is None:
        print("\nERROR: Could not connect to any Supabase PostgreSQL endpoint.")
        print("Check your database password in Supabase Dashboard -> Settings -> Database")
        sys.exit(1)

    conn.autocommit = True
    cur = conn.cursor()

    print("\nRunning finance schema migration...")

    try:
        # Execute the full migration
        cur.execute(sql)
        print("Migration executed successfully!")
    except psycopg2.Error as e:
        print("SQL error: %s" % e)
        sys.exit(1)

    # Verify tables
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'finance'
        ORDER BY table_name;
    """)
    tables = [row[0] for row in cur.fetchall()]
    print("\nFinance schema tables (%d):" % len(tables))
    for t in tables:
        cur.execute("SELECT COUNT(*) FROM finance.%s" % t)
        count = cur.fetchone()[0]
        print("  finance.%-20s %d rows" % (t, count))

    cur.close()
    conn.close()
    print("\nDone. Finance schema is ready.")


if __name__ == "__main__":
    main()
