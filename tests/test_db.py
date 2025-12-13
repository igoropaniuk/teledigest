from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import List

import pytest

from teledigest import config as cfg
from teledigest import db


def _make_app_config(
    db_path: Path, rag_keywords: list[str] | None = None
) -> cfg.AppConfig:
    """
    Helper to build a minimal AppConfig instance that is sufficient for db.py.
    """
    telegram = cfg.TelegramConfig(
        api_id=1, api_hash="hash", bot_token="token", sessions_dir=Path("testdata")
    )
    bot = cfg.BotConfig(channels=["@c1"], summary_target="@digest")
    llm = cfg.LLMConfig(
        model="gpt-4.1",
        api_key="sk-test",
        system_prompt="",
        user_prompt="",
    )
    storage = cfg.StorageConfig(
        rag_keywords=rag_keywords or [],
        db_path=db_path,
    )
    logging_cfg = cfg.LoggingConfig(level="INFO")

    return cfg.AppConfig(
        telegram=telegram,
        bot=bot,
        llm=llm,
        storage=storage,
        logging=logging_cfg,
    )


@pytest.fixture
def app_config(tmp_path, monkeypatch) -> cfg.AppConfig:
    """
    Provide a fresh AppConfig for each test and wire it into config.get_config().
    """
    db_path = tmp_path / "messages_fts.db"
    app_cfg = _make_app_config(db_path=db_path, rag_keywords=["war", "drone*"])

    # Install as global config so db.get_config() can see it
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg, raising=False)
    return app_cfg


def supports_fts5() -> bool:
    """
    Detect whether the sqlite3 library has FTS5 support.
    We use this to conditionally assert on FTS-specific behaviour.
    """
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
        conn.close()
        return True
    except sqlite3.OperationalError:
        return False


def _fetch_table_names(db_path: Path) -> List[str]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def _fetch_all_messages(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, channel, date, text FROM messages ORDER BY date ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_messages_table(app_config: cfg.AppConfig) -> None:
    """
    init_db() should create the main messages table and, when available, the FTS table.
    """
    db.init_db()

    table_names = _fetch_table_names(app_config.storage.db_path)
    assert "messages" in table_names

    if supports_fts5():
        # In an environment with FTS5, we expect the index table as well.
        assert "messages_fts" in table_names


# ---------------------------------------------------------------------------
# save_message + get_messages_for_range
# ---------------------------------------------------------------------------


def test_save_message_inserts_into_messages_and_respects_range(
    app_config: cfg.AppConfig,
) -> None:
    db.init_db()

    base = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    db.save_message("1", "@c1", base, "first message")
    db.save_message("2", "@c2", base + dt.timedelta(minutes=1), "second message")
    db.save_message("3", "@c3", base + dt.timedelta(minutes=2), "third message")

    # Raw check via sqlite
    rows = _fetch_all_messages(app_config.storage.db_path)
    assert [r[0] for r in rows] == ["1", "2", "3"]

    # get_messages_for_range should return the same 3 rows
    start = base - dt.timedelta(minutes=1)
    end = base + dt.timedelta(minutes=10)
    messages = db.get_messages_for_range(start, end, limit=None)
    assert len(messages) == 3
    # format is (channel, text), order by date asc
    assert messages[0][0] == "@c1"
    assert messages[0][1] == "first message"
    assert messages[-1][0] == "@c3"


def test_save_message_ignores_empty_text(app_config: cfg.AppConfig) -> None:
    db.init_db()

    base = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    db.save_message("1", "@c1", base, "")
    rows = _fetch_all_messages(app_config.storage.db_path)

    assert rows == []


def test_get_messages_for_range_honours_limit(app_config: cfg.AppConfig) -> None:
    db.init_db()

    base = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    for i in range(5):
        db.save_message(str(i), "@c", base + dt.timedelta(minutes=i), f"m{i}")

    start = base - dt.timedelta(minutes=1)
    end = base + dt.timedelta(minutes=10)

    limited = db.get_messages_for_range(start, end, limit=2)
    assert len(limited) == 2


# ---------------------------------------------------------------------------
# build_fts_query
# ---------------------------------------------------------------------------


def test_build_fts_query_joins_keywords_with_or(
    app_config: cfg.AppConfig, monkeypatch
) -> None:
    # Ensure we have a specific list of keywords
    app_config.storage.rag_keywords = ["war", "offensive", "drone*"]
    query = db.build_fts_query()
    assert query == "war OR offensive OR drone*"


def test_build_fts_query_raises_when_no_keywords(app_config: cfg.AppConfig) -> None:
    app_config.storage.rag_keywords = []
    with pytest.raises(RuntimeError):
        db.build_fts_query()


# ---------------------------------------------------------------------------
# get_relevant_messages_for_range – FTS path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not supports_fts5(), reason="sqlite3 FTS5 is not available")
def test_get_relevant_messages_for_range_uses_fts_when_available(
    app_config: cfg.AppConfig,
) -> None:
    """
    When FTS5 is available, get_relevant_messages_for_range should return
    only the messages matching the configured keywords.
    """
    db.init_db()

    base = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)

    # This message should match the keyword 'war'
    db.save_message("1", "@c1", base, "breaking war news here")

    # This one should not match
    db.save_message("2", "@c1", base, "completely unrelated chit chat")

    start = base - dt.timedelta(minutes=1)
    end = base + dt.timedelta(minutes=1)

    app_config.storage.rag_keywords = ["war"]

    rows = db.get_relevant_messages_for_range(start, end, max_docs=10)
    # In FTS mode we expect only the matching row
    assert len(rows) == 1
    channel, text = rows[0]
    assert channel == "@c1"
    assert "war" in text


# ---------------------------------------------------------------------------
# get_relevant_messages_for_range – fallback path when FTS fails
# ---------------------------------------------------------------------------


def test_get_relevant_messages_for_range_falls_back_on_fts_error(
    app_config: cfg.AppConfig, monkeypatch
) -> None:
    """
    If the FTS query fails with sqlite3.OperationalError, the function should
    fall back to get_messages_for_range and still return results.
    """
    db.init_db()

    base = dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    db.save_message("1", "@c1", base, "some war related content")
    db.save_message("2", "@c1", base, "other content with no keywords")

    start = base - dt.timedelta(minutes=1)
    end = base + dt.timedelta(minutes=1)

    # We configure some keyword but will force the FTS query to fail
    app_config.storage.rag_keywords = ["war"]

    # Keep a reference to the real connect
    real_connect = db.sqlite3.connect

    def fake_connect(path):
        # Wrap the real connection so we can selectively break FTS queries
        conn = real_connect(path)

        class FakeCursor:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *args, **kwargs):
                # Any query against messages_fts will fail with OperationalError
                if "FROM messages_fts" in sql:
                    raise sqlite3.OperationalError("FTS disabled for test")
                return self._inner.execute(sql, *args, **kwargs)

            def fetchall(self):
                return self._inner.fetchall()

        class FakeConn:
            def __init__(self, inner):
                self._inner = inner

            def cursor(self):
                return FakeCursor(self._inner.cursor())

            def close(self):
                self._inner.close()

        return FakeConn(conn)

    # Patch only within db module – other modules keep the real sqlite3.connect
    monkeypatch.setattr(db.sqlite3, "connect", fake_connect, raising=True)

    rows = db.get_relevant_messages_for_range(start, end, max_docs=10)

    # Fallback should behave like a simple range query capped by max_docs
    assert len(rows) == 2
    channels = {ch for ch, _ in rows}
    assert channels == {"@c1"}
