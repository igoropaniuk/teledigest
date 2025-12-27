from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import get_config, log
from .text_sanitize import sanitize_text


class DatabaseError(Exception):
    """Database operation errors."""

    pass


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    """
    Context manager for database connections.

    Ensures connections are properly closed even if errors occur.

    Yields:
        sqlite3.Connection: Active database connection
    """
    db_path = get_config().storage.db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable dict-like access
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema."""
    db_path = get_config().storage.db_path
    log.info("Initializing SQLite database at %s", db_path)

    with get_db_connection() as conn:
        cur = conn.cursor()

        # Main table with proper indices
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                date TEXT NOT NULL,
                text TEXT NOT NULL
            )
            """
        )

        # Add indices for common queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)"
        )

        # FTS virtual table for full-text search
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
            log.error("Failed to create FTS5 table: %s", e)
            raise DatabaseError(f"FTS5 initialization failed: {e}") from e

        conn.commit()


def save_message(msg_id: str, channel: str, date: dt.datetime, text: str) -> None:
    """
    Save a message to the database.

    Args:
        msg_id: Unique message identifier
        channel: Channel name
        date: Message timestamp
        text: Message content

    Raises:
        DatabaseError: If save operation fails
    """
    if not text:
        return

    text = sanitize_text(text)
    iso = date.isoformat()

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            # Insert into main table
            cur.execute(
                """
                INSERT OR IGNORE INTO messages (id, channel, date, text)
                VALUES (?, ?, ?, ?)
                """,
                (msg_id, channel, iso, text),
            )

            # Insert into FTS index
            try:
                cur.execute(
                    """
                    INSERT INTO messages_fts (id, channel, date, text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (msg_id, channel, iso, text),
                )
            except sqlite3.OperationalError as e:
                log.warning("Failed to insert into FTS index (FTS disabled?): %s", e)

            conn.commit()

    except sqlite3.Error as e:
        log.error("Failed to save message %s: %s", msg_id, e)
        raise DatabaseError(f"Failed to save message: {e}") from e


def get_messages_for_range(
    start: dt.datetime, end: dt.datetime, limit: int | None = None
) -> list[tuple[str, str]]:
    """
    Get all messages in a date range.

    Args:
        start: Start of date range (inclusive)
        end: End of date range (inclusive)
        limit: Maximum number of messages to return

    Returns:
        List of (channel, text) tuples

    Raises:
        DatabaseError: If query fails
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            # Build query with proper parameterization
            sql = """
                SELECT channel, text FROM messages
                WHERE date BETWEEN ? AND ?
                ORDER BY date ASC
            """

            params = [start_iso, end_iso]

            # Add LIMIT as a parameter to prevent SQL injection
            if limit is not None:
                sql += " LIMIT ?"
                params.append(str(limit))

            cur.execute(sql, params)
            return cur.fetchall()

    except sqlite3.Error as e:
        log.error("Failed to query messages: %s", e)
        raise DatabaseError(f"Failed to query messages: {e}") from e


def get_messages_for_day(
    day: dt.date, limit: int | None = None
) -> list[tuple[str, str]]:
    """
    Get all messages for a specific day.

    Args:
        day: Target date
        limit: Maximum number of messages

    Returns:
        List of (channel, text) tuples
    """
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day, dt.time.max)
    return get_messages_for_range(start, end, limit)


def build_fts_query() -> str:
    """
    Build FTS5 query from configured keywords.

    Returns:
        FTS5 MATCH query string

    Raises:
        DatabaseError: If no keywords configured
    """
    cfg = get_config()
    kws = cfg.storage.rag_keywords

    # Filter empty keywords
    parts = [kw.strip() for kw in kws if kw.strip()]

    if not parts:
        raise DatabaseError("No RAG keywords configured")

    # Join with OR operator for FTS5
    return " OR ".join(parts)


def get_relevant_messages_for_range(
    start: dt.datetime,
    end: dt.datetime,
    max_docs: int = 200,
) -> list[tuple[str, str]]:
    """
    RAG-style retrieval using FTS index.

    Falls back to full scan if FTS is unavailable.

    Args:
        start: Start of date range
        end: End of date range
        max_docs: Maximum documents to return

    Returns:
        List of (channel, text) tuples matching keywords
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    try:
        query = build_fts_query()
    except DatabaseError as e:
        log.warning("Cannot build FTS query: %s. Using full scan.", e)
        return get_messages_for_range(start, end, limit=max_docs)

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            sql = """
                SELECT channel, text
                FROM messages_fts
                WHERE messages_fts MATCH ?
                  AND date BETWEEN ? AND ?
                ORDER BY date ASC
                LIMIT ?
            """

            cur.execute(sql, (query, start_iso, end_iso, max_docs))
            rows = cur.fetchall()

            if rows:
                log.info(
                    "FTS retrieval for %s - %s returned %d messages (max %d)",
                    start_iso,
                    end_iso,
                    len(rows),
                    max_docs,
                )
                return rows
            else:
                log.info("FTS retrieval returned 0 rows - falling back to simple range")

    except sqlite3.OperationalError as e:
        log.warning("FTS retrieval failed (%s). Falling back to full range scan.", e)

    # Fallback to simple scan
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
