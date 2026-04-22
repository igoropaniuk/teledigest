"""
sources_db.py — Dynamic channel/country management in SQLite.

Replaces static config-based channel lists with DB-driven sources
that can be added/removed via bot commands at runtime.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from .config import log
from .db import get_db_connection


# ---------------------------------------------------------------------------
# Country name → code mapping
# ---------------------------------------------------------------------------

COUNTRY_MAP: dict[str, str] = {
    # Russian names
    "бразилия": "br",
    "турция": "tr",
    "таиланд": "th",
    "португалия": "pt",
    "испания": "es",
    "италия": "it",
    "грузия": "ge",
    "армения": "am",
    "сербия": "rs",
    "черногория": "me",
    "аргентина": "ar",
    "мексика": "mx",
    "индонезия": "id",
    "вьетнам": "vn",
    "оаэ": "ae",
    "эмираты": "ae",
    "кипр": "cy",
    "германия": "de",
    "франция": "fr",
    "чехия": "cz",
    "польша": "pl",
    "израиль": "il",
    "египет": "eg",
    "марокко": "ma",
    "шри-ланка": "lk",
    "шриланка": "lk",
    "малайзия": "my",
    "парагвай": "py",
    "уругвай": "uy",
    "чили": "cl",
    "колумбия": "co",
    "перу": "pe",
    "боливия": "bo",
    "эквадор": "ec",
    "доминикана": "do",
    "куба": "cu",
    "коста-рика": "cr",
    "панама": "pa",
    # English / short codes
    "brazil": "br",
    "turkey": "tr",
    "thailand": "th",
    "portugal": "pt",
    "spain": "es",
    "italy": "it",
    "georgia": "ge",
    "argentina": "ar",
    "mexico": "mx",
    "indonesia": "id",
    "vietnam": "vn",
    "uae": "ae",
}

# Reverse: code → display name (Russian)
COUNTRY_NAMES: dict[str, str] = {
    "br": "🇧🇷 Бразилия",
    "tr": "🇹🇷 Турция",
    "th": "🇹🇭 Таиланд",
    "pt": "🇵🇹 Португалия",
    "es": "🇪🇸 Испания",
    "it": "🇮🇹 Италия",
    "ge": "🇬🇪 Грузия",
    "am": "🇦🇲 Армения",
    "rs": "🇷🇸 Сербия",
    "me": "🇲🇪 Черногория",
    "ar": "🇦🇷 Аргентина",
    "mx": "🇲🇽 Мексика",
    "id": "🇮🇩 Индонезия",
    "vn": "🇻🇳 Вьетнам",
    "ae": "🇦🇪 ОАЭ",
    "cy": "🇨🇾 Кипр",
    "de": "🇩🇪 Германия",
    "fr": "🇫🇷 Франция",
    "cz": "🇨🇿 Чехия",
    "pl": "🇵🇱 Польша",
    "il": "🇮🇱 Израиль",
    "eg": "🇪🇬 Египет",
    "ma": "🇲🇦 Марокко",
    "lk": "🇱🇰 Шри-Ланка",
    "my": "🇲🇾 Малайзия",
    "py": "🇵🇾 Парагвай",
    "uy": "🇺🇾 Уругвай",
    "cl": "🇨🇱 Чили",
    "co": "🇨🇴 Колумбия",
    "pe": "🇵🇪 Перу",
    "bo": "🇧🇴 Боливия",
    "ec": "🇪🇨 Эквадор",
    "do": "🇩🇴 Доминикана",
    "cu": "🇨🇺 Куба",
    "cr": "🇨🇷 Коста-Рика",
    "pa": "🇵🇦 Панама",
}


def resolve_country(text: str) -> tuple[str, str] | None:
    """
    Resolve user input to (country_code, display_name).

    Accepts: full name in Russian/English, ISO code, partial match.
    Returns None if not recognized.
    """
    text = text.strip().lower()

    # Direct code match
    if text in COUNTRY_NAMES:
        return text, COUNTRY_NAMES[text]

    # Full name match
    if text in COUNTRY_MAP:
        code = COUNTRY_MAP[text]
        return code, COUNTRY_NAMES.get(code, code.upper())

    # Prefix match (e.g. "тур" → "турция")
    candidates = [(k, v) for k, v in COUNTRY_MAP.items() if k.startswith(text)]
    if len(candidates) == 1:
        code = candidates[0][1]
        return code, COUNTRY_NAMES.get(code, code.upper())

    return None


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

def init_sources_table() -> None:
    """Create sources table if not exists."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT 'ru',
                digest_target TEXT DEFAULT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL,
                UNIQUE(country, url)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sources_country
            ON sources(country)
        """)
        log.info("Sources table initialized.")


def migrate_from_config(channels: list[dict[str, str]],
                        digest_targets: dict[str, str]) -> int:
    """
    One-time migration: copy channels from config into sources table.

    Skips channels that already exist (by url).
    Returns number of new channels added.
    """
    added = 0
    now = dt.datetime.utcnow().isoformat()

    with get_db_connection() as conn:
        cur = conn.cursor()
        for ch in channels:
            url = ch.get("url", "").strip()
            if not url:
                continue
            country = ch.get("country", "br")
            name = ch.get("name", "")
            language = ch.get("language", "ru")

            try:
                cur.execute(
                    "INSERT OR IGNORE INTO sources (country, url, name, language, added_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (country, url, name, language, now),
                )
                if cur.rowcount > 0:
                    added += 1
            except sqlite3.IntegrityError:
                pass

        # Migrate digest targets
        for country_code, target in digest_targets.items():
            cur.execute(
                "UPDATE sources SET digest_target = ? WHERE country = ? AND digest_target IS NULL",
                (target, country_code),
            )

    if added:
        log.info("Migrated %d channels from config to sources table.", added)
    return added


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_source(country: str, url: str, name: str = "",
               language: str = "ru") -> int:
    """Add a new source channel. Returns row id or 0 if duplicate."""
    now = dt.datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO sources (country, url, name, language, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (country, url, name, language, now),
            )
            return cur.lastrowid or 0
        except sqlite3.IntegrityError:
            return 0


def remove_source(country: str, url: str) -> bool:
    """Deactivate a source. Returns True if found."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sources SET active = 0 WHERE country = ? AND url = ?",
            (country, url),
        )
        return cur.rowcount > 0


def set_digest_target(country: str, target: str) -> None:
    """Set digest target channel for a country."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sources SET digest_target = ? WHERE country = ?",
            (target, country),
        )


def get_active_sources(country: str | None = None) -> list[dict[str, Any]]:
    """Get active sources, optionally filtered by country."""
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if country:
            cur.execute(
                "SELECT * FROM sources WHERE active = 1 AND country = ? ORDER BY added_at",
                (country,),
            )
        else:
            cur.execute("SELECT * FROM sources WHERE active = 1 ORDER BY country, added_at")
        return [dict(row) for row in cur.fetchall()]


def get_digest_target(country: str) -> str | None:
    """Get digest target channel for a country."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT digest_target FROM sources WHERE country = ? AND digest_target IS NOT NULL LIMIT 1",
            (country,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_active_countries() -> list[str]:
    """Get list of countries that have active sources."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT country FROM sources WHERE active = 1 ORDER BY country")
        return [row[0] for row in cur.fetchall()]
