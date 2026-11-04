"""SQLite storage. One file, no ORM.

Privacy rule: no table has a column for prompt or response text.
"""
import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);

-- Provider keys the user pasted. The key itself is Fernet-encrypted.
CREATE TABLE IF NOT EXISTS provider_keys (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    label         TEXT NOT NULL,
    key_enc       BLOB NOT NULL,
    fingerprint   TEXT NOT NULL,
    masked        TEXT NOT NULL,
    plan          TEXT NOT NULL DEFAULT 'auto',   -- auto | free | paid
    status        TEXT NOT NULL DEFAULT 'unchecked',
    models_json   TEXT,
    key_info_json TEXT,                            -- provider's own key metadata
    last_checked  REAL,
    created_at    REAL NOT NULL,
    UNIQUE(user_id, fingerprint)
);

-- One virtual key per app. Apps call the proxy with it; KeyLens swaps in the real key.
CREATE TABLE IF NOT EXISTS apps (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_id          INTEGER NOT NULL REFERENCES provider_keys(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'other',  -- backend | cli | browser | notebook | other
    vkey_hash       TEXT UNIQUE NOT NULL,
    vkey_hint       TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    budget_usd      REAL,                           -- monthly list-price budget, optional
    created_at      REAL NOT NULL
);

-- One row per proxied request (metadata only).
CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY,
    user_id           INTEGER NOT NULL,
    key_id            INTEGER NOT NULL,
    app_id            INTEGER,
    ts                REAL NOT NULL,
    provider          TEXT NOT NULL,
    endpoint          TEXT NOT NULL,
    model             TEXT,
    stream            INTEGER NOT NULL DEFAULT 0,
    status            INTEGER,
    error_type        TEXT,
    source_class      TEXT,
    source_detail     TEXT,
    client_ip         TEXT,
    user_agent        TEXT,
    origin            TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    reasoning_tokens  INTEGER DEFAULT 0,
    cached_tokens     INTEGER DEFAULT 0,
    latency_ms        REAL,
    ttfb_ms           REAL,
    queue_ms          REAL,
    compute_ms        REAL,
    region            TEXT,
    finish_reason     TEXT,
    list_cost_usd     REAL DEFAULT 0,
    actual_cost_usd   REAL DEFAULT 0,
    upstream_id       TEXT,
    raw_meta          TEXT                           -- JSON: headers + usage block, never bodies
);
CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_key ON events(key_id, ts);

-- Rate-limit snapshots read from provider responses, used for ghost-usage inference.
CREATE TABLE IF NOT EXISTS ratelimit_snapshots (
    id                 INTEGER PRIMARY KEY,
    key_id             INTEGER NOT NULL,
    ts                 REAL NOT NULL,
    model              TEXT,
    limit_requests     INTEGER,
    remaining_requests INTEGER,
    reset_requests_s   REAL,
    limit_tokens       INTEGER,
    remaining_tokens   INTEGER,
    reset_tokens_s     REAL,
    via                TEXT NOT NULL               -- proxy | probe
);
CREATE INDEX IF NOT EXISTS idx_rl_key ON ratelimit_snapshots(key_id, model, ts);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def jload(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
