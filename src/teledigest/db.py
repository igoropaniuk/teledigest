from __future__ import annotations

import datetime as dt
import sqlite3

from .config import get_config, log
from .text_sanitize import sanitize_text


def init_db():
    db_path = get_config().storage.db_path
    log.info("Initializing SQLite database at %s", db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Main table: one row per message
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            channel TEXT,
            date TEXT,
            text TEXT
        )
        """
    )

    # FTS virtual table for full-text search (RAG retrieval)
    try:
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(
                id,
                channel,
                date,
                text
            )
            """
        )
        log.info("FTS5 virtual table messages_fts initialized.")
    except sqlite3.OperationalError as e:
        log.error("Failed to create FTS5 table (does your SQLite support FTS5?): %s", e)

    conn.commit()
    conn.close()


def save_message(msg_id: str, channel: str, date: dt.datetime, text: str):
    if not text:
        return

    text = sanitize_text(text)
    iso = date.isoformat()

    conn = sqlite3.connect(get_config().storage.db_path)
    cur = conn.cursor()
    # main table (id is unique)
    cur.execute(
        """
        INSERT OR IGNORE INTO messages (id, channel, date, text)
        VALUES (?, ?, ?, ?)
        """,
        (msg_id, channel, iso, text),
    )

    # FTS index – no uniqueness, but we insert once per message
    try:
        cur.execute(
            """
            INSERT INTO messages_fts (id, channel, date, text)
            VALUES (?, ?, ?, ?)
            """,
            (msg_id, channel, iso, text),
        )
    except sqlite3.OperationalError as e:
        # Likely FTS5 not available; we just log and continue
        log.warning("Failed to insert into messages_fts (FTS disabled?): %s", e)

    conn.commit()
    conn.close()


def get_messages_for_range(
    start: dt.datetime, end: dt.datetime, limit: int | None = None
):
    """
    Generic helper: get all messages in [start, end] from main table,
    optionally limited.
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    conn = sqlite3.connect(get_config().storage.db_path)
    cur = conn.cursor()

    sql = """
        SELECT channel, text FROM messages
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    cur.execute(sql, (start_iso, end_iso))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_messages_for_day(day: dt.date, limit: int | None = None):
    """
    Backwards-compatible helper: still used if you ever want pure 'calendar day'
    behaviour. Now implemented via get_messages_for_range().
    """
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day, dt.time.max)
    return get_messages_for_range(start, end, limit)


def build_fts_query() -> str:
    """
    Convert a list of keywords into a single FTS5 MATCH query.
    Example: ["war", "offensive", "drone*"] → "war OR offensive OR drone*"
    """
    cfg = get_config()
    kws = cfg.storage.rag_keywords

    # Escape or validate if needed — FTS5 wildcard patterns are OK as-is.
    parts = [kw for kw in kws if kw]

    if not parts:
        raise RuntimeError("No RAG keywords configured")

    # Join with OR operator
    return " OR ".join(parts)


def get_relevant_messages_for_range(
    start: dt.datetime,
    end: dt.datetime,
    max_docs: int = 200,
):
    """
    RAG-style retrieval for an arbitrary time range [start, end].
    Uses FTS index when available, falls back to simple scan.
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    # Query tuned for 'important news'
    query = build_fts_query()

    conn = sqlite3.connect(get_config().storage.db_path)
    cur = conn.cursor()

    try:
        sql = f"""
            SELECT channel, text
            FROM messages_fts
            WHERE messages_fts MATCH ?
              AND date BETWEEN ? AND ?
            ORDER BY date ASC
            LIMIT {int(max_docs)}
        """
        cur.execute(sql, (query, start_iso, end_iso))
        rows = cur.fetchall()
        conn.close()

        if rows:
            log.info(
                "FTS retrieval for %s - %s returned %d messages (max %d).",
                start_iso,
                end_iso,
                len(rows),
                max_docs,
            )
            return rows
        else:
            log.info(
                "FTS retrieval returned 0 rows for %s - %s - falling back to simple range.",
                start_iso,
                end_iso,
            )

    except sqlite3.OperationalError as e:
        # Happens when FTS5 is not available
        log.warning("FTS retrieval failed (%s). Falling back to full range scan.", e)
        conn.close()

    # Fallback: simple scan limited to max_docs
    return get_messages_for_range(start, end, limit=max_docs)


def get_relevant_messages_for_day(day: dt.date, max_docs: int = 200):
    """
    Backwards-compatible wrapper using a calendar day.
    """
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day, dt.time.max)
    return get_relevant_messages_for_range(start, end, max_docs)


def get_messages_last_24h(limit: int | None = None):
    """
    All messages from the last 24 hours (rolling window), in UTC.
    """
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=24)
    return get_messages_for_range(start, now, limit)


def get_relevant_messages_last_24h(max_docs: int = 200):
    """
    RAG-style retrieval for the last 24 hours (rolling window), in UTC.
    """
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=24)
    return get_relevant_messages_for_range(start, now, max_docs)
