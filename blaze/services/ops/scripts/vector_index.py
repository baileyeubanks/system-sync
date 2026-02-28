#!/usr/bin/env python3
"""
vector_index.py — RAG embedding pipeline for Blaze V4
Runs on Mac Mini (use /opt/homebrew/bin/python3 — needs sqlite-vec + openai)

Modes:
  --embed-supabase   Embed unindexed rows in Supabase (contacts, interactions,
                     source_videos, creative_briefs)
  --embed-local      Embed knowledge.db (youtube_insights, goals) via sqlite-vec
  --search <query>   Semantic search across all local + Supabase data
  --status           Show embedding coverage stats
  --install-check    Verify sqlite-vec is installed and working

LaunchAgent: com.blaze.vector-index (nightly 9PM, after knowledge-harvest at 8PM)

Notes:
  - Python 3.9 compat (no walrus, no backslash in f-strings)
  - sqlite-vec arm64 wheel works via sqlite_vec.load(conn)
  - Supabase calls via direct HTTP (no supabase-py dep needed)
  - OpenAI text-embedding-3-small (1536 dims, $0.02/1M tokens)
"""

import argparse
import json
import os
import sqlite3
import struct
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────────

OPENAI_KEY       = os.environ.get('OPENAI_API_KEY', '')
SUPABASE_URL     = 'https://briokwdoonawhxisbydy.supabase.co'
SUPABASE_KEY     = os.environ.get('SUPABASE_SERVICE_KEY', '')
KNOWLEDGE_DB     = os.path.expanduser('~/blaze-data/knowledge.db')
LOG_PATH         = os.path.expanduser('~/blaze-logs/vector_index.log')
EMBED_MODEL      = 'text-embedding-3-small'
EMBED_DIMS       = 1536
BATCH_SIZE       = 96     # OpenAI embeddings batch limit
ACS_BIZ_ID       = '0ade82e3-ffe9-4c17-ae59-fc4bd198482b'

os.makedirs(os.path.expanduser('~/blaze-logs'), exist_ok=True)

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = ts + ' ' + msg
    print(line)
    try:
        with open(LOG_PATH, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


# ── OpenAI embeddings ─────────────────────────────────────────────────────────

def embed_batch(texts):
    """Embed a batch of texts. Returns list of 1536-dim float lists."""
    if not OPENAI_KEY:
        raise RuntimeError('OPENAI_API_KEY not set')

    # Sanitize: replace newlines, truncate to 8000 chars (safe for most models)
    cleaned = [str(t).replace('\n', ' ')[:8000] for t in texts]

    payload = json.dumps({
        'input': cleaned,
        'model': EMBED_MODEL,
        'encoding_format': 'float',
    }).encode()

    req = urllib.request.Request(
        'https://api.openai.com/v1/embeddings',
        data=payload,
        headers={
            'Authorization': 'Bearer ' + OPENAI_KEY,
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    # Sort by index to preserve order
    items = sorted(data['data'], key=lambda x: x['index'])
    return [item['embedding'] for item in items]


def embed_single(text):
    return embed_batch([text])[0]


# ── Supabase HTTP helpers ─────────────────────────────────────────────────────

def sb_get(path, params=None):
    url = SUPABASE_URL + '/rest/v1/' + path
    if params:
        url += '?' + '&'.join(k + '=' + str(v) for k, v in params.items())
    req = urllib.request.Request(url, headers={
        'apikey': SUPABASE_KEY,
        'Authorization': 'Bearer ' + SUPABASE_KEY,
        'Accept': 'application/json',
        'Prefer': 'count=exact',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def sb_patch(path, record_id, data):
    url = SUPABASE_URL + '/rest/v1/' + path + '?id=eq.' + record_id
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload, method='PATCH',
        headers={
            'apikey': SUPABASE_KEY,
            'Authorization': 'Bearer ' + SUPABASE_KEY,
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def sb_rpc(fn_name, params):
    url = SUPABASE_URL + '/rest/v1/rpc/' + fn_name
    payload = json.dumps(params).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            'apikey': SUPABASE_KEY,
            'Authorization': 'Bearer ' + SUPABASE_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ── sqlite-vec helpers ────────────────────────────────────────────────────────

def load_sqlite_vec(conn):
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:
        log('sqlite-vec load failed: ' + str(exc))
        return False


def serialize_vec(v):
    """Pack float list to binary for sqlite-vec."""
    return struct.pack(str(len(v)) + 'f', *v)


def ensure_vec_tables(conn):
    """Create vec0 virtual tables alongside existing tables in knowledge.db."""
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_insights
        USING vec0(
            insight_rowid INTEGER PRIMARY KEY,
            embedding FLOAT[1536]
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_goals
        USING vec0(
            goal_rowid INTEGER PRIMARY KEY,
            embedding FLOAT[1536]
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_queue
        USING vec0(
            queue_rowid INTEGER PRIMARY KEY,
            embedding FLOAT[1536]
        )
    """)
    conn.commit()


# ── Supabase embedding ────────────────────────────────────────────────────────

def embed_table(table, text_fn, id_col='id'):
    """
    Fetch rows with embedding IS NULL, embed, patch back.
    text_fn(row) → string to embed.
    """
    log('embedding ' + table + ' ...')
    total = 0
    offset = 0
    limit = BATCH_SIZE

    while True:
        rows = sb_get(table, {
            'embedding': 'is.null',
            'select': '*',
            'limit': limit,
            'offset': offset,
        })
        if not rows:
            break

        texts = []
        ids = []
        for row in rows:
            t = text_fn(row)
            if t and t.strip():
                texts.append(t)
                ids.append(str(row[id_col]))

        if not texts:
            offset += limit
            continue

        try:
            embeddings = embed_batch(texts)
        except Exception as exc:
            log('embed_batch error: ' + str(exc))
            time.sleep(5)
            continue

        for rec_id, emb in zip(ids, embeddings):
            try:
                sb_patch(table, rec_id, {'embedding': emb})
            except Exception as exc:
                log('patch error ' + rec_id + ': ' + str(exc))

        total += len(ids)
        log('  ' + table + ': embedded ' + str(total) + ' rows so far')

        if len(rows) < limit:
            break
        offset += limit
        time.sleep(0.5)   # rate limit politeness

    log(table + ' done: ' + str(total) + ' rows embedded')
    return total


def embed_supabase():
    if not SUPABASE_KEY:
        log('SUPABASE_SERVICE_KEY not set — skipping Supabase embedding')
        return

    # contacts: name + company + ai_summary + city
    embed_table('contacts', lambda r: ' '.join(filter(None, [
        r.get('name'), r.get('company'),
        r.get('ai_summary'), r.get('city'), r.get('state'),
    ])))

    # interactions: type + summary
    embed_table('interactions', lambda r: ' '.join(filter(None, [
        r.get('type'), r.get('summary'),
        json.dumps(r.get('payload') or {}),
    ])))

    # creative_briefs: objective + key_messages + audience + company
    embed_table('creative_briefs', lambda r: ' '.join(filter(None, [
        r.get('contact_name'), r.get('company'),
        r.get('objective'), r.get('key_messages'), r.get('audience'),
        r.get('deliverables'),
    ])))

    # source_videos: title + first 4000 chars of transcript (chunks later)
    def video_text(r):
        title = r.get('title') or ''
        transcript = (r.get('transcript') or '')[:4000]
        return (title + ' ' + transcript).strip()

    embed_table('source_videos', video_text)


# ── Local knowledge.db embedding ──────────────────────────────────────────────

def embed_local():
    if not os.path.exists(KNOWLEDGE_DB):
        log('knowledge.db not found at ' + KNOWLEDGE_DB)
        return

    conn = sqlite3.connect(KNOWLEDGE_DB)
    if not load_sqlite_vec(conn):
        log('sqlite-vec not available — run: pip3 install sqlite-vec')
        conn.close()
        return

    ensure_vec_tables(conn)

    # youtube_insights
    rows = conn.execute("""
        SELECT yi.rowid, yi.insight
        FROM youtube_insights yi
        WHERE NOT EXISTS (
            SELECT 1 FROM vec_insights vi WHERE vi.insight_rowid = yi.rowid
        )
        LIMIT 200
    """).fetchall()

    if rows:
        log('embedding ' + str(len(rows)) + ' youtube_insights ...')
        texts = [r[1] for r in rows]
        rowids = [r[0] for r in rows]
        embeddings = embed_batch(texts)
        for rowid, emb in zip(rowids, embeddings):
            conn.execute(
                'INSERT OR REPLACE INTO vec_insights(insight_rowid, embedding) VALUES (?, ?)',
                [rowid, serialize_vec(emb)]
            )
        conn.commit()
        log('youtube_insights: embedded ' + str(len(rows)))

    # goals
    rows = conn.execute("""
        SELECT g.rowid, g.type || ': ' || g.goal
        FROM goals g
        WHERE NOT EXISTS (
            SELECT 1 FROM vec_goals vg WHERE vg.goal_rowid = g.rowid
        )
        LIMIT 200
    """).fetchall()

    if rows:
        log('embedding ' + str(len(rows)) + ' goals ...')
        texts = [r[1] for r in rows]
        rowids = [r[0] for r in rows]
        embeddings = embed_batch(texts)
        for rowid, emb in zip(rowids, embeddings):
            conn.execute(
                'INSERT OR REPLACE INTO vec_goals(goal_rowid, embedding) VALUES (?, ?)',
                [rowid, serialize_vec(emb)]
            )
        conn.commit()
        log('goals: embedded ' + str(len(rows)))

    # youtube_queue (done items with summaries)
    rows = conn.execute("""
        SELECT yq.rowid, yq.title || ' ' || coalesce(yq.summary, '')
        FROM youtube_queue yq
        WHERE yq.status = 'done'
          AND (yq.summary IS NOT NULL AND yq.summary != '')
          AND NOT EXISTS (
              SELECT 1 FROM vec_queue vq WHERE vq.queue_rowid = yq.rowid
          )
        LIMIT 200
    """).fetchall()

    if rows:
        log('embedding ' + str(len(rows)) + ' youtube_queue items ...')
        texts = [r[1] for r in rows]
        rowids = [r[0] for r in rows]
        embeddings = embed_batch(texts)
        for rowid, emb in zip(rowids, embeddings):
            conn.execute(
                'INSERT OR REPLACE INTO vec_queue(queue_rowid, embedding) VALUES (?, ?)',
                [rowid, serialize_vec(emb)]
            )
        conn.commit()
        log('youtube_queue: embedded ' + str(len(rows)))

    conn.close()


# ── Search ────────────────────────────────────────────────────────────────────

def search(query, limit=10):
    results = []

    # 1. Local knowledge.db via sqlite-vec
    if os.path.exists(KNOWLEDGE_DB):
        conn = sqlite3.connect(KNOWLEDGE_DB)
        if load_sqlite_vec(conn):
            try:
                ensure_vec_tables(conn)
                qemb = embed_single(query)
                qbytes = serialize_vec(qemb)

                # youtube_insights — subquery so LIMIT hits the vec0 table directly
                knn_rows = conn.execute("""
                    SELECT insight_rowid, distance
                    FROM vec_insights
                    WHERE embedding MATCH ?
                    LIMIT ?
                """, [qbytes, limit]).fetchall()
                for rowid, dist in knn_rows:
                    yi = conn.execute(
                        'SELECT insight, channel_name FROM youtube_insights WHERE rowid = ?',
                        [rowid]
                    ).fetchone()
                    if yi:
                        results.append({
                            'source': 'youtube_insight',
                            'score': round(1 - dist, 3),
                            'text': yi[0][:200],
                            'meta': yi[1],
                        })

                # goals
                knn_rows = conn.execute("""
                    SELECT goal_rowid, distance
                    FROM vec_goals
                    WHERE embedding MATCH ?
                    LIMIT ?
                """, [qbytes, limit]).fetchall()
                for rowid, dist in knn_rows:
                    g = conn.execute(
                        'SELECT type, goal FROM goals WHERE rowid = ?',
                        [rowid]
                    ).fetchone()
                    if g:
                        results.append({
                            'source': 'goal',
                            'score': round(1 - dist, 3),
                            'text': g[0] + ': ' + g[1],
                            'meta': '',
                        })
            except Exception as exc:
                log('local search error: ' + str(exc))
        conn.close()

    # 2. Supabase contacts via RPC
    if SUPABASE_KEY:
        try:
            qemb = embed_single(query)
            rows = sb_rpc('match_contacts_semantic', {
                'query_embedding': qemb,
                'match_count': limit,
                'match_threshold': 0.3,
            })
            for r in rows:
                results.append({
                    'source': 'contact',
                    'score': round(r.get('similarity', 0), 3),
                    'text': (r.get('name') or '') + ' — ' + (r.get('ai_summary') or '')[:150],
                    'meta': r.get('phone') or r.get('email') or '',
                })
        except Exception as exc:
            log('Supabase contact search error: ' + str(exc))

    # Sort by score descending
    results.sort(key=lambda x: -x['score'])
    return results[:limit]


# ── Status ────────────────────────────────────────────────────────────────────

def status():
    print('\n=== RAG Embedding Status ===\n')

    # Local
    if os.path.exists(KNOWLEDGE_DB):
        conn = sqlite3.connect(KNOWLEDGE_DB)
        load_sqlite_vec(conn)
        try:
            ensure_vec_tables(conn)
            n_insights = conn.execute('SELECT count(*) FROM youtube_insights').fetchone()[0]
            v_insights = conn.execute('SELECT count(*) FROM vec_insights').fetchone()[0]
            n_goals    = conn.execute('SELECT count(*) FROM goals').fetchone()[0]
            v_goals    = conn.execute('SELECT count(*) FROM vec_goals').fetchone()[0]
            n_queue    = conn.execute("SELECT count(*) FROM youtube_queue WHERE status='done' AND summary IS NOT NULL").fetchone()[0]
            v_queue    = conn.execute('SELECT count(*) FROM vec_queue').fetchone()[0]
            print('knowledge.db (local):')
            print('  youtube_insights : ' + str(v_insights) + '/' + str(n_insights) + ' embedded')
            print('  goals            : ' + str(v_goals)    + '/' + str(n_goals)    + ' embedded')
            print('  youtube_queue    : ' + str(v_queue)    + '/' + str(n_queue)    + ' embedded')
        except Exception as exc:
            print('knowledge.db error: ' + str(exc))
        conn.close()
    else:
        print('knowledge.db: NOT FOUND')

    # Supabase
    if SUPABASE_KEY:
        print('\nSupabase (remote):')
        for table in ['contacts', 'interactions', 'source_videos', 'creative_briefs']:
            try:
                total = sb_get(table, {'select': 'id'})
                embedded = sb_get(table, {'select': 'id', 'embedding': 'not.is.null'})
                print('  ' + table.ljust(20) + ': ' +
                      str(len(embedded)) + '/' + str(len(total)) + ' embedded')
            except Exception as exc:
                print('  ' + table + ': error — ' + str(exc))
    else:
        print('\nSupabase: SUPABASE_SERVICE_KEY not set')

    print()


# ── Install check ─────────────────────────────────────────────────────────────

def install_check():
    print('Checking sqlite-vec ...')
    try:
        import sqlite_vec
        conn = sqlite3.connect(':memory:')
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        ver = conn.execute('SELECT vec_version()').fetchone()[0]
        conn.close()
        print('sqlite-vec OK: version ' + ver)
    except ImportError:
        print('sqlite-vec NOT installed. Run: pip3 install sqlite-vec')
        return False
    except Exception as exc:
        print('sqlite-vec load error: ' + str(exc))
        return False

    print('Checking OpenAI key ...')
    if OPENAI_KEY:
        print('OPENAI_API_KEY: set')
    else:
        print('OPENAI_API_KEY: NOT set')
        return False

    print('Checking Supabase key ...')
    if SUPABASE_KEY:
        print('SUPABASE_SERVICE_KEY: set')
    else:
        print('SUPABASE_SERVICE_KEY: NOT set')

    print('\nAll checks passed.' if OPENAI_KEY else '\nMissing keys.')
    return bool(OPENAI_KEY)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Blaze V4 RAG embedding pipeline')
    parser.add_argument('--embed-supabase', action='store_true')
    parser.add_argument('--embed-local',    action='store_true')
    parser.add_argument('--embed-all',      action='store_true', help='Run both local + Supabase embedding')
    parser.add_argument('--search',         metavar='QUERY')
    parser.add_argument('--status',         action='store_true')
    parser.add_argument('--install-check',  action='store_true')
    args = parser.parse_args()

    if args.install_check:
        install_check()

    elif args.status:
        status()

    elif args.search:
        results = search(args.search)
        if not results:
            print('No results found.')
        else:
            for i, r in enumerate(results, 1):
                print(str(i) + '. [' + r['source'] + '] score=' + str(r['score']))
                print('   ' + r['text'])
                if r['meta']:
                    print('   ' + r['meta'])
                print()

    elif args.embed_supabase:
        embed_supabase()

    elif args.embed_local:
        embed_local()

    elif args.embed_all:
        embed_local()
        embed_supabase()

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
