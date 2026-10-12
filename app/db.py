import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
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


def connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


_conn = connect()


def add_item(**fields) -> int:
    fields["created_at"] = datetime.now(config.TZ).isoformat(timespec="seconds")
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = _conn.execute(f"INSERT INTO items ({cols}) VALUES ({marks})", tuple(fields.values()))
    _conn.commit()
    return cur.lastrowid


def update_item(item_id: int, **fields) -> None:
    assert set(fields) <= set(FIELDS), fields
    sets = ", ".join(f"{k} = ?" for k in fields)
    _conn.execute(f"UPDATE items SET {sets} WHERE id = ?", (*fields.values(), item_id))
    _conn.commit()


def get_item(item_id: int) -> sqlite3.Row | None:
    return _conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def items(state: str | None = None) -> list[sqlite3.Row]:
    if state:
        return _conn.execute("SELECT * FROM items WHERE state = ? ORDER BY id", (state,)).fetchall()
    return _conn.execute("SELECT * FROM items ORDER BY id").fetchall()


def mark_sent(item_id: int, key: str) -> bool:
    """Record a notification; returns False if it was already sent."""
    try:
        _conn.execute("INSERT INTO sent (item_id, key) VALUES (?, ?)", (item_id, key))
        _conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
