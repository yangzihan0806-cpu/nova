"""Single-process SQLite storage; all SQL is protected by LOCK."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

LOCK = threading.Lock()
DB_PATH = Path(__file__).resolve().parent / "tartangrid.db"
LEASE_TTL_S = float(os.getenv("TG_LEASE_TTL_S", "10"))
HEARTBEAT_S = float(os.getenv("TG_HEARTBEAT_S", "3"))
OFFLINE_AFTER_S = float(os.getenv("TG_OFFLINE_AFTER_S", "12"))
MAX_ATTEMPTS = int(os.getenv("TG_MAX_ATTEMPTS", "3"))
MAX_ACTIVE_JOBS = int(os.getenv("TG_MAX_ACTIVE_JOBS", "2"))
SPOT_CHECKS = int(os.getenv("TG_SPOT_CHECKS", "8"))
_conn: sqlite3.Connection | None = None


def _legacy_credit_iters(result_json):
    """Read accepted v1.1 results on demand; never rewrite old task rows."""
    return sum(json.loads(result_json)) if result_json is not None else 0


class _IntegerSum:
    """Preserve exact totals even when valid per-task runtimes exceed int64 in sum."""

    def __init__(self):
        self.total = 0

    def step(self, value):
        if value is not None:
            self.total += int(value)

    def finalize(self):
        # SQLite cannot return a Python integer above int64; decode at the API edge.
        return str(self.total)


SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
  id               TEXT PRIMARY KEY,         -- "wkr_" + 8 hex
  name             TEXT NOT NULL,
  worker_type      TEXT NOT NULL,            -- browser | native
  token            TEXT NOT NULL,
  slots            INTEGER NOT NULL DEFAULT 1,
  has_gpu          INTEGER NOT NULL DEFAULT 0,
  task_types       TEXT NOT NULL,            -- JSON 数组，服务端与 allowlist 取交集
  client_info      TEXT,
  paused           INTEGER NOT NULL DEFAULT 0,
  disconnected     INTEGER NOT NULL DEFAULT 0,
  offline_notified INTEGER NOT NULL DEFAULT 0,
  last_heartbeat   REAL NOT NULL,
  registered_at    REAL NOT NULL,
  tasks_done       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,              -- "job_" + 6 hex
  task_type   TEXT NOT NULL,
  params_json TEXT NOT NULL,
  status      TEXT NOT NULL,                 -- queued | running | completed | failed | cancelled
  task_count  INTEGER NOT NULL,
  created_at  REAL NOT NULL,
  started_at  REAL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS tasks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        TEXT NOT NULL,
  idx           INTEGER NOT NULL,
  task_type     TEXT NOT NULL,
  input_json    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | leased | done | failed
  worker_id     TEXT,
  lease_token   TEXT,
  lease_expires REAL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  result_json   TEXT,
  done_by       TEXT,
  runtime_ms    INTEGER,
  done_at       REAL,
  done_seq      INTEGER                           -- = 完成时插入的 task_done 事件 id
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, job_id, idx);
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  type      TEXT NOT NULL,
  job_id    TEXT,
  task_id   INTEGER,
  worker_id TEXT,
  message   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          REAL NOT NULL,
  request_id  TEXT NOT NULL UNIQUE,
  decision    TEXT NOT NULL CHECK(decision IN ('allowed','denied')),
  reason_code TEXT NOT NULL,
  details     TEXT NOT NULL,
  job_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_decision ON security_events(decision);
"""


def get_conn():
    """Return the shared connection. Execute SQL only inside tx() or LOCK."""
    if _conn is None:
        raise RuntimeError("Database is not initialized")
    return _conn


def init_db():
    global _conn
    with LOCK:
        if _conn is not None:
            _conn.close()
            _conn = None
        path = Path(os.getenv("TG_DB_PATH", str(DB_PATH)))
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.getenv("TG_RESET_DB", "0") == "1":
            path.unlink(missing_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        # stdlib callback keeps the aggregate independent of SQLite JSON1 builds.
        _conn.create_function('legacy_credit_iters', 1, _legacy_credit_iters, deterministic=True)
        _conn.create_aggregate('integer_sum', 1, _IntegerSum)
        _conn.executescript(SCHEMA)
        # Upgrade deployed databases in place. Keep old values NULL so reads
        # can apply the documented worker-id/result fallback without rewriting.
        _conn.execute("BEGIN IMMEDIATE")
        try:
            for table, additions in (
                ("workers", (("device_id", "TEXT"), ("owner", "TEXT"))),
                ("tasks", (("credit_iters", "INTEGER"),)),
            ):
                columns = {row["name"] for row in _conn.execute(f"PRAGMA table_info({table})")}
                for name, kind in additions:
                    if name not in columns:
                        _conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            _conn.execute("CREATE INDEX IF NOT EXISTS idx_workers_device ON workers(device_id)")
            # Covering index for the contributions aggregate, so it never reads the large
            # result_json rows (the tasks table is ~140 MB after a demo afternoon).
            _conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_credit "
                          "ON tasks(status, done_by, done_at, credit_iters, runtime_ms)")
            # One-time backfill of credit for results accepted before credit_iters existed.
            # Without it, every /api/contributions read re-parses all legacy result_json
            # under the global DB lock (~2 s for ~2000 demo tiles; the Dashboard polls it
            # every 5 s), stalling leases and heartbeats for every worker. Same values
            # as the read-time fallback, which stays in place as a safety net.
            _conn.execute(
                "UPDATE tasks SET credit_iters = legacy_credit_iters(result_json) "
                "WHERE status='done' AND credit_iters IS NULL AND result_json IS NOT NULL"
            )
            _conn.execute("COMMIT")
        except BaseException:
            if _conn.in_transaction:
                _conn.execute("ROLLBACK")
            _conn.close()
            _conn = None
            raise


def close_db():
    global _conn
    with LOCK:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def tx():
    with LOCK:
        conn = get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
