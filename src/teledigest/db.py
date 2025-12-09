from __future__ import annotations

import datetime as dt
import sqlite3
from .config import DB_PATH, log


def init_db():
    log.info("Initializing SQLite database at %s", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
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
    iso = date.isoformat()

    conn = sqlite3.connect(DB_PATH)
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


def get_messages_for_day(day: dt.date, limit: int | None = None):
    """
    Fallback: simple 'all messages for the day' from main table,
    optionally limited.
    """
    start = dt.datetime.combine(day, dt.time.min).isoformat()
    end = dt.datetime.combine(day, dt.time.max).isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    sql = """
        SELECT channel, text FROM messages
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    cur.execute(sql, (start, end))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_relevant_messages_for_day(day: dt.date, max_docs: int = 200):
    """
    RAG-style retrieval:
    Use the FTS index to get the most relevant messages for today's
    'important news' queries instead of sending everything to the LLM.

    If FTS5 is not available or returns nothing, falls back to
    get_messages_for_day(day, limit=max_docs).
    """
    start = dt.datetime.combine(day, dt.time.min).isoformat()
    end = dt.datetime.combine(day, dt.time.max).isoformat()

    # Query tuned for 'important news' (Ukr/Eng mix; tweak as you like)
    query = (
        # ===============================
        # 🇺🇦 Ukrainian — war, politics
        # ===============================
        "війна OR наступ* OR контрнаступ* OR фронт OR лінія OR оборон* "
        "OR штурм* OR артилер* OR обстріл* OR удар* OR ракета* OR безпілотн* "
        "OR дрон* OR ППО OR мобілізац* OR призов* OR резерв* OR втрат* "
        "OR збройн* OR ЗСУ OR Сили OR Оборони OR Генштаб OR Міноборони "
        "OR санкц* OR економік* OR енергетик* OR ринок* OR бюджет* "
        "OR НАТО OR ЄС OR Європейськ* OR допомог* OR підтримк* "
        "OR переговор* OR дипломат* "
        # Key persons UA
        "OR Зеленськ* OR Умеров OR Умєров "
        # ===============================
        # 🇷🇺 Russian — war, politics
        # ===============================
        "OR войн* OR наступлен* OR контрнаступ* OR фронт OR линия "
        "OR оборон* OR штурм* OR артилл* OR обстрел* OR удар* OR ракет* "
        "OR беспилотн* OR дрон* OR ПВО OR мобилизац* OR призыв OR резерв* "
        "OR потерь OR армия OR ВСУ OR Минобороны "
        "OR санкц* OR экономик* OR энергетик* OR бюджет* OR рынок* "
        "OR НАТО OR ЕС OR Европейск* OR помощ* OR поддержк* "
        "OR переговор* OR дипломат* "
        # Key persons RU
        "OR Зеленск* OR Умеров "
        # ===============================
        # 🇬🇧 English — war, geopolitics
        # ===============================
        "OR war OR offensive OR counteroffensive OR front OR frontline "
        "OR defense OR assault OR artillery OR shell* OR strike* OR attack* "
        "OR missile* OR drone* OR UAV OR air OR defense OR mobilization "
        "OR draft OR reserve OR casualties OR military OR armed OR forces "
        "OR sanctions OR economy OR energy OR market OR budget "
        "OR NATO OR EU OR European OR aid OR support "
        "OR negotiations OR diplomacy "
        # Key persons EN
        "OR Zelensky OR Zelenskiy OR Zelenskyy OR Umerov"
    )

    conn = sqlite3.connect(DB_PATH)
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
        cur.execute(sql, (query, start, end))
        rows = cur.fetchall()
        conn.close()

        if rows:
            log.info(
                "FTS retrieval for %s returned %d messages (max %d).",
                day.isoformat(),
                len(rows),
                max_docs,
            )
            return rows
        else:
            log.info(
                "FTS retrieval returned 0 rows for %s – falling back to simple day range.",
                day.isoformat(),
            )

    except sqlite3.OperationalError as e:
        # Happens when FTS5 is not available
        log.warning("FTS retrieval failed (%s). Falling back to full day scan.", e)
        conn.close()

    # Fallback: simple scan limited to max_docs
    return get_messages_for_day(day, limit=max_docs)
