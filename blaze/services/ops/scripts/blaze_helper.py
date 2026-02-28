import subprocess, json, sqlite3, os, time
from datetime import datetime

DATA_ROOT = "/Users/_mxappservice/blaze-data"
BLAZE_DB = "%s/blaze.db" % DATA_ROOT
EVENT_STREAM_DB = "%s/event_stream.db" % DATA_ROOT
OPENCLAW = "/usr/local/bin/openclaw"

# ---- Cost lookup (per 1M tokens) ----
MODEL_COSTS = {
    "gemini-3-flash":     {"in": 0.10, "out": 0.40},
    "gemini-3-pro-low":   {"in": 1.25, "out": 5.00},
    "gemini-3-pro":       {"in": 1.25, "out": 5.00},
    "claude-sonnet":      {"in": 3.00, "out": 15.00},
    "claude-haiku":       {"in": 0.25, "out": 1.25},
    "claude-opus":        {"in": 15.00, "out": 75.00},
    "qwen-2.5-14b":       {"in": 0.00, "out": 0.00},  # local
}

def estimate_cost(model, tokens_in, tokens_out):
    costs = MODEL_COSTS.get(model, {"in": 1.0, "out": 5.0})
    return round((tokens_in * costs["in"] + tokens_out * costs["out"]) / 1_000_000, 6)

def ask_blaze(prompt, agent="main", timeout=60):
    start_time = time.time()
    result_text = ""
    success = 1
    error_msg = None
    try:
        result = subprocess.run(
            [OPENCLAW, "agent", "--agent", agent, "--message", prompt, "--json"],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
        )
        if result.returncode != 0:
            success = 0
            error_msg = result.stderr.strip()[:200]
            result_text = "CLI_ERROR: %s" % error_msg
        else:
            raw = result.stdout.strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                result_text = raw
                _auto_log(agent, prompt, result_text, success, error_msg, start_time)
                return result_text

            for extractor in [
                lambda d: d["result"]["payloads"][0]["text"],
                lambda d: d["result"]["text"],
                lambda d: d["payloads"][0]["text"],
            ]:
                try:
                    result_text = extractor(data)
                    break
                except (KeyError, IndexError, TypeError):
                    pass
            if not result_text:
                for key in ("response", "text", "content", "message"):
                    try:
                        val = data[key]
                        if val:
                            result_text = val
                            break
                    except (KeyError, TypeError):
                        pass
            if not result_text:
                result_text = str(data)
    except subprocess.TimeoutExpired:
        success = 0
        error_msg = "timeout after %ds" % timeout
        result_text = "CLI_ERROR: timeout"
    except Exception as e:
        success = 0
        error_msg = str(e)
        result_text = "CLI_ERROR: %s" % e

    _auto_log(agent, prompt, result_text, success, error_msg, start_time)
    return result_text

def _auto_log(agent, prompt, response, success, error, start_time):
    """Auto-log usage after every ask_blaze call."""
    tokens_in = len(prompt) // 4
    tokens_out = len(str(response)) // 4
    model = "gemini-3-flash"
    cost = estimate_cost(model, tokens_in, tokens_out)
    log_usage(agent, model, tokens_in, tokens_out, cost, success, error)

def _open_db(path):
    """Open a SQLite connection with WAL mode and busy timeout."""
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_db(name):
    """All operational tables live in blaze.db. Events in event_stream.db."""
    if name == "events":
        return _open_db(EVENT_STREAM_DB)
    return _open_db(BLAZE_DB)

def log_usage(job, model, tokens_in, tokens_out, cost, success=1, error=None):
    try:
        db = get_db("usage")
        db.execute(
            "INSERT INTO api_calls (ts,job_name,model,provider,tokens_in,tokens_out,cost_usd,workflow,success,error_message) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), job, model, "antigravity", tokens_in, tokens_out, cost, job, success, error)
        )
        db.commit()
        db.close()
    except:
        pass

def log_cron(job, status, summary, error=None):
    try:
        db = get_db("cron")
        now = datetime.utcnow().isoformat()
        db.execute(
            "INSERT INTO cron_runs (job_name,started_at,completed_at,status,output_summary,error_message) VALUES (?,?,?,?,?,?)",
            (job, now, now, status, summary, error)
        )
        db.commit()
        db.close()
    except:
        pass

def update_daily_summary():
    """Roll up today's api_calls into daily_summary."""
    try:
        db = get_db("usage")
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT COUNT(*), SUM(tokens_in), SUM(tokens_out), SUM(cost_usd) FROM api_calls WHERE ts LIKE ?",
            (today + "%",)
        ).fetchone()
        if row and row[0] > 0:
            top = db.execute(
                "SELECT workflow, SUM(cost_usd) as total FROM api_calls WHERE ts LIKE ? GROUP BY workflow ORDER BY total DESC LIMIT 1",
                (today + "%",)
            ).fetchone()
            db.execute(
                "INSERT OR REPLACE INTO daily_summary (date, total_calls, total_tokens_in, total_tokens_out, total_cost_usd, most_expensive_workflow) VALUES (?,?,?,?,?,?)",
                (today, row[0], row[1] or 0, row[2] or 0, row[3] or 0, top[0] if top else "")
            )
            db.commit()
        db.close()
    except:
        pass
