"""Storage: Postgres when DATABASE_URL is set (ThalesOps), otherwise a local SQLite file."""
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config

log = logging.getLogger(__name__)
POSTGRES = bool(config.DATABASE_URL)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS items (
    id           {"SERIAL PRIMARY KEY" if POSTGRES else "INTEGER PRIMARY KEY"},
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'unknown',   -- open | closed | unknown (what the page says)
    open_date    TEXT,                              -- YYYY-MM-DD
    close_date   TEXT,                              -- YYYY-MM-DD
    state        TEXT NOT NULL DEFAULT 'watching',  -- watching | applied | dropped (what you did)
    remind_on    TEXT,                              -- one-off snooze reminder date
    page_hash    TEXT,
    created_at   TEXT NOT NULL,
    last_checked TEXT
);
CREATE TABLE IF NOT EXISTS sent (
    item_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    PRIMARY KEY (item_id, key)
);
"""

FIELDS = ("url", "title", "summary", "status", "open_date", "close_date",
          "state", "remind_on", "page_hash", "last_checked")

_conn = None


def _connect():
    if POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(config.DATABASE_URL, autocommit=True, row_factory=dict_row)
    else:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, isolation_level=None)  # autocommit
        conn.row_factory = sqlite3.Row
    for statement in filter(str.strip, SCHEMA.split(";")):
        conn.execute(statement)
    return conn


def _run(sql: str, params: tuple = ()):
    """Execute one statement; reconnect once if Postgres dropped the connection."""
    global _conn
    if POSTGRES:
        sql = sql.replace("?", "%s")
    for attempt in (1, 2):
        if _conn is None:
            _conn = _connect()
        try:
            return _conn.execute(sql, params)
        except Exception as exc:
            if not POSTGRES or attempt == 2 or not _conn.closed:
                raise
            log.warning("Database connection lost (%s), reconnecting", exc)
            _conn = None


def init() -> None:
    _run("SELECT 1")
    log.info("Using %s", "Postgres (DATABASE_URL)" if POSTGRES else f"SQLite ({config.DB_PATH})")


def add_item(**fields) -> int:
    fields["created_at"] = datetime.now(config.TZ).isoformat(timespec="seconds")
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    row = _run(f"INSERT INTO items ({cols}) VALUES ({marks}) RETURNING id",
               tuple(fields.values())).fetchone()
    return row["id"]


def update_item(item_id: int, **fields) -> None:
    assert set(fields) <= set(FIELDS), fields
    sets = ", ".join(f"{k} = ?" for k in fields)
    _run(f"UPDATE items SET {sets} WHERE id = ?", (*fields.values(), item_id))


def get_item(item_id: int):
    return _run("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def items(state: str | None = None) -> list:
    if state:
        return _run("SELECT * FROM items WHERE state = ? ORDER BY id", (state,)).fetchall()
    return _run("SELECT * FROM items ORDER BY id").fetchall()


def mark_sent(item_id: int, key: str) -> bool:
    """Record a notification; returns False if it was already sent."""
    cur = _run("INSERT INTO sent (item_id, key) VALUES (?, ?) ON CONFLICT DO NOTHING", (item_id, key))
    return cur.rowcount == 1


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
