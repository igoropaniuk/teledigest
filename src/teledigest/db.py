from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from typing import Iterator, NamedTuple

from .config import get_config, log
from .text_sanitize import sanitize_text


class DatabaseError(Exception):
    """Database operation errors."""


class Message(NamedTuple):
    """A single stored message, as returned by all query helpers."""

    channel: str
    text: str


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

            # Only mirror to FTS when the row was actually inserted.
            # INSERT OR IGNORE sets rowcount=0 on a duplicate, so skipping
            # the FTS insert avoids accumulating phantom duplicates there.
            if cur.rowcount:
                try:
                    cur.execute(
                        """
                        INSERT INTO messages_fts (id, channel, date, text)
                        VALUES (?, ?, ?, ?)
                        """,
                        (msg_id, channel, iso, text),
                    )
                except sqlite3.OperationalError as e:
                    log.warning(
                        "Failed to insert into FTS index (FTS disabled?): %s", e
                    )

            conn.commit()

    except sqlite3.Error as e:
        log.error("Failed to save message %s: %s", msg_id, e)
        raise DatabaseError(f"Failed to save message: {e}") from e


def get_messages_for_range(
    start: dt.datetime, end: dt.datetime, limit: int | None = None
) -> list[Message]:
    """
    Get all messages in a date range.

    Args:
        start: Start of date range (inclusive)
        end: End of date range (inclusive)
        limit: Maximum number of messages to return

    Returns:
        List of Message(channel, text) named tuples

    Raises:
        DatabaseError: If query fails
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            sql = """
                SELECT channel, text FROM messages
                WHERE date BETWEEN ? AND ?
                ORDER BY date ASC
            """

            params: list[str | int] = [start_iso, end_iso]

            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)

            cur.execute(sql, params)
            return [Message(*row) for row in cur.fetchall()]

    except sqlite3.Error as e:
        log.error("Failed to query messages: %s", e)
        raise DatabaseError(f"Failed to query messages: {e}") from e


def get_messages_for_day(day: dt.date, limit: int | None = None) -> list[Message]:
    """
    Get all messages for a specific day.

    Args:
        day: Target date
        limit: Maximum number of messages

    Returns:
        List of Message(channel, text) named tuples
    """
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day, dt.time.max)
    return get_messages_for_range(start, end, limit)


def build_fts_query() -> str | None:
    """
    Build an FTS5 MATCH query from the configured keywords.

    Returns:
        A query string such as ``"war OR drone*"``, or ``None`` when no
        keywords are configured.  Callers that receive ``None`` should fall
        back to a full range scan rather than treating the absence of keywords
        as an error.
    """
    kws = get_config().storage.rag_keywords
    parts = [kw.strip() for kw in kws if kw.strip()]
    return " OR ".join(parts) if parts else None


def get_relevant_messages_for_range(
    start: dt.datetime,
    end: dt.datetime,
    max_docs: int = 200,
) -> list[Message]:
    """
    RAG-style retrieval using FTS index.

    Falls back to full scan if FTS is unavailable or no keywords are configured.

    Args:
        start: Start of date range
        end: End of date range
        max_docs: Maximum documents to return

    Returns:
        List of Message(channel, text) named tuples matching keywords
    """
    query = build_fts_query()
    if query is None:
        log.warning("No RAG keywords configured — using full scan.")
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

            cur.execute(sql, (query, start.isoformat(), end.isoformat(), max_docs))
            rows = [Message(*row) for row in cur.fetchall()]

            if rows:
                log.info(
                    "FTS retrieval for %s - %s returned %d messages (max %d)",
                    start.isoformat(),
                    end.isoformat(),
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


def get_relevant_messages_for_day(day: dt.date, max_docs: int = 200) -> list[Message]:
    """
    Backwards-compatible wrapper using a calendar day.
    """
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day, dt.time.max)
    return get_relevant_messages_for_range(start, end, max_docs)


def _rolling_window() -> tuple[dt.datetime, dt.datetime]:
    """Return (start, end) for a rolling 24-hour window ending now (UTC)."""
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(hours=24), now


def get_messages_last_24h(limit: int | None = None) -> list[Message]:
    """
    All messages from the last 24 hours (rolling window), in UTC.
    """
    start, end = _rolling_window()
    return get_messages_for_range(start, end, limit)


def get_relevant_messages_last_24h(max_docs: int = 200) -> list[Message]:
    """
    RAG-style retrieval for the last 24 hours (rolling window), in UTC.
    """
    start, end = _rolling_window()
    return get_relevant_messages_for_range(start, end, max_docs)
