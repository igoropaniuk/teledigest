from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from teledigest import config as cfg
from teledigest import telegram_client as tc
from teledigest.db import Message
from teledigest.telegram_client import AuthDialog, AuthStep, UserAuthState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_config(allowed_users_raw: str = "") -> cfg.AppConfig:
    telegram = cfg.TelegramConfig(
        api_id=1, api_hash="hash", bot_token="token", sessions_dir=Path("testdata")
    )
    bot = cfg.BotConfig(
        channels=["@c1"],
        summary_target="@digest",
        time_zone="UTC",
        allowed_users_raw=allowed_users_raw,
    )
    llm_cfg = cfg.LLMConfig(
        model="gpt-4.1",
        api_key="sk-test",
        system_prompt="You are a helpful assistant.",
        user_prompt="Summarize {DAY}: {MESSAGES}",
        temperature=0.4,
    )
    storage = cfg.StorageConfig(rag_keywords=[], db_path=Path(":memory:"))
    logging_cfg = cfg.LoggingConfig(level="WARNING")
    return cfg.AppConfig(
        telegram=telegram,
        bot=bot,
        llm=llm_cfg,
        storage=storage,
        logging=logging_cfg,
    )


@pytest.fixture
def app_config(monkeypatch) -> cfg.AppConfig:
    app_cfg = _make_app_config()
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg, raising=False)
    return app_cfg


def _make_event(
    sender_id: int = 123,
    username: str | None = "testuser",
    chat_id: int = 456,
    raw_text: str = "",
) -> MagicMock:
    """Return a minimal fake Telethon event."""
    sender = MagicMock()
    sender.username = username

    event = MagicMock()
    event.sender_id = sender_id
    event.chat_id = chat_id
    event.raw_text = raw_text
    event.get_sender = AsyncMock(return_value=sender)
    event.reply = AsyncMock()
    return event


# ---------------------------------------------------------------------------
# is_user_allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_user_allowed_no_restrictions_allows_everyone(app_config):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()
    assert await tc.is_user_allowed(event) is True


@pytest.mark.asyncio
async def test_is_user_allowed_by_numeric_user_id(app_config):
    app_config.bot.allowed_users_raw = "123"
    event = _make_event(sender_id=123)
    assert await tc.is_user_allowed(event) is True


@pytest.mark.asyncio
async def test_is_user_allowed_denied_when_id_not_listed(app_config):
    app_config.bot.allowed_users_raw = "999"
    event = _make_event(sender_id=123)
    assert await tc.is_user_allowed(event) is False


@pytest.mark.asyncio
async def test_is_user_allowed_by_at_username(app_config):
    app_config.bot.allowed_users_raw = "@testuser"
    event = _make_event(sender_id=999, username="testuser")
    assert await tc.is_user_allowed(event) is True


@pytest.mark.asyncio
async def test_is_user_allowed_username_comparison_is_case_insensitive(app_config):
    app_config.bot.allowed_users_raw = "@TESTUSER"
    event = _make_event(sender_id=999, username="testuser")
    assert await tc.is_user_allowed(event) is True


@pytest.mark.asyncio
async def test_is_user_allowed_denied_wrong_username(app_config):
    app_config.bot.allowed_users_raw = "@other"
    event = _make_event(sender_id=999, username="testuser")
    assert await tc.is_user_allowed(event) is False


@pytest.mark.asyncio
async def test_is_user_allowed_no_username_and_id_not_listed_is_denied(app_config):
    app_config.bot.allowed_users_raw = "@someuser"
    event = _make_event(sender_id=999, username=None)
    assert await tc.is_user_allowed(event) is False


@pytest.mark.asyncio
async def test_is_user_allowed_multiple_entries(app_config):
    app_config.bot.allowed_users_raw = "@admin,42,@mod"
    # allowed by ID
    event_id = _make_event(sender_id=42, username=None)
    assert await tc.is_user_allowed(event_id) is True
    # allowed by username
    event_un = _make_event(sender_id=999, username="mod")
    assert await tc.is_user_allowed(event_un) is True
    # not allowed
    event_no = _make_event(sender_id=1, username="stranger")
    assert await tc.is_user_allowed(event_no) is False


# ---------------------------------------------------------------------------
# help_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_command_sends_all_supported_commands(app_config):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()
    await tc.help_command(event)

    event.reply.assert_called_once()
    reply_text = event.reply.call_args[0][0]
    for cmd in ["/today", "/status", "/auth", "/help"]:
        assert cmd in reply_text


@pytest.mark.asyncio
async def test_help_command_denied_for_unauthorized_user(app_config):
    app_config.bot.allowed_users_raw = "999"
    event = _make_event(sender_id=123)
    await tc.help_command(event)

    event.reply.assert_called_once()
    assert "not allowed" in event.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# channel_message_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_message_handler_ignores_non_target_chat(monkeypatch, app_config):
    monkeypatch.setattr(tc, "scraped_chat_ids", set())  # no registered channels
    saved = []
    monkeypatch.setattr(tc, "save_message", lambda *a, **kw: saved.append(a))

    event = MagicMock()
    event.chat_id = 99999
    await tc.channel_message_handler(event)

    assert saved == []


@pytest.mark.asyncio
async def test_channel_message_handler_saves_message_from_target_chat(
    monkeypatch, app_config
):
    chat_id = 12345
    monkeypatch.setattr(tc, "scraped_chat_ids", {chat_id})
    monkeypatch.setattr(tc, "chat_id_to_name", {chat_id: "testchannel"})

    saved = []
    monkeypatch.setattr(tc, "save_message", lambda *a, **kw: saved.append(a))

    msg = MagicMock()
    msg.message = "Hello from channel"
    msg.date = dt.datetime(2024, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    msg.id = 42

    event = MagicMock()
    event.chat_id = chat_id
    event.message = msg

    await tc.channel_message_handler(event)

    assert len(saved) == 1
    msg_id, channel, date, text = saved[0]
    assert channel == "testchannel"
    assert text == "Hello from channel"
    assert msg_id == "testchannel_42"


@pytest.mark.asyncio
async def test_channel_message_handler_uses_chat_id_as_fallback_name(
    monkeypatch, app_config
):
    chat_id = 12345
    monkeypatch.setattr(tc, "scraped_chat_ids", {chat_id})
    monkeypatch.setattr(tc, "chat_id_to_name", {})  # no name mapping

    saved = []
    monkeypatch.setattr(tc, "save_message", lambda *a, **kw: saved.append(a))

    msg = MagicMock()
    msg.message = "text"
    msg.date = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    msg.id = 1

    event = MagicMock()
    event.chat_id = chat_id
    event.message = msg

    await tc.channel_message_handler(event)

    assert saved[0][1] == str(chat_id)


# ---------------------------------------------------------------------------
# today_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_today_command_denied_for_unauthorized_user(app_config):
    app_config.bot.allowed_users_raw = "999"
    event = _make_event(sender_id=123)
    await tc.today_command(event)

    event.reply.assert_called_once()
    assert "not allowed" in event.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_today_command_no_messages_replies_with_empty_notice(
    monkeypatch, app_config
):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()
    monkeypatch.setattr(tc, "get_relevant_messages_last_24h", lambda max_docs=200: [])
    await tc.today_command(event)

    event.reply.assert_called_once()
    assert "No messages" in event.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_today_command_with_messages_sends_llm_summary(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()

    messages = [Message(channel="@c1", text="war update")]
    monkeypatch.setattr(
        tc, "get_relevant_messages_last_24h", lambda max_docs=200: messages
    )
    monkeypatch.setattr(tc, "llm_summarize", lambda day, msgs: "AI summary")

    reply_long_mock = AsyncMock()
    monkeypatch.setattr(tc, "reply_long", reply_long_mock)

    await tc.today_command(event)

    reply_long_mock.assert_called_once()
    assert "AI summary" in reply_long_mock.call_args[0][1]


# ---------------------------------------------------------------------------
# status_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_command_denied_for_unauthorized_user(app_config):
    app_config.bot.allowed_users_raw = "999"
    event = _make_event(sender_id=123)
    await tc.status_command(event)

    event.reply.assert_called_once()
    assert "not allowed" in event.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_status_command_returns_teledigest_status(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()

    monkeypatch.setattr(tc, "get_relevant_messages_last_24h", lambda max_docs=200: [])
    monkeypatch.setattr(tc, "get_messages_last_24h", lambda: [])
    reply_long_mock = AsyncMock()
    monkeypatch.setattr(tc, "reply_long", reply_long_mock)

    await tc.status_command(event)

    reply_long_mock.assert_called_once()
    reply_text = reply_long_mock.call_args[0][1]
    assert "Teledigest status" in reply_text


@pytest.mark.asyncio
async def test_status_command_shows_auth_warning_when_not_authorized(
    monkeypatch, app_config
):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()

    monkeypatch.setattr(tc, "get_relevant_messages_last_24h", lambda max_docs=200: [])
    monkeypatch.setattr(tc, "get_messages_last_24h", lambda: [])
    monkeypatch.setattr(tc, "user_auth_state", UserAuthState.REQUIRED)

    reply_long_mock = AsyncMock()
    monkeypatch.setattr(tc, "reply_long", reply_long_mock)

    await tc.status_command(event)

    reply_text = reply_long_mock.call_args[0][1]
    assert "Authorization required" in reply_text


@pytest.mark.asyncio
async def test_status_command_shows_message_counts(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = ""
    event = _make_event()

    relevant = [Message(channel="@c1", text="war news")]
    all_msgs = [
        Message(channel="@c1", text="war news"),
        Message(channel="@c1", text="other"),
    ]
    monkeypatch.setattr(
        tc, "get_relevant_messages_last_24h", lambda max_docs=200: relevant
    )
    monkeypatch.setattr(tc, "get_messages_last_24h", lambda: all_msgs)
    monkeypatch.setattr(tc, "build_prompt", lambda day, msgs: ("sys", "user"))

    reply_long_mock = AsyncMock()
    monkeypatch.setattr(tc, "reply_long", reply_long_mock)

    await tc.status_command(event)

    reply_text = reply_long_mock.call_args[0][1]
    assert "1" in reply_text  # relevant count
    assert "2" in reply_text  # parsed count


# ---------------------------------------------------------------------------
# auth_start_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_start_command_denied_for_unauthorized_user(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = "999"
    event = _make_event(sender_id=123, chat_id=456)
    await tc.auth_start_command(event)

    event.reply.assert_called_once()
    assert "not allowed" in event.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_auth_start_command_replies_already_authorized(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = ""
    monkeypatch.setattr(tc, "user_auth_state", UserAuthState.OK)
    event = _make_event(chat_id=456)

    await tc.auth_start_command(event)

    event.reply.assert_called_once()
    assert "already authorized" in event.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_auth_start_command_starts_dialog_when_not_authorized(
    monkeypatch, app_config
):
    app_config.bot.allowed_users_raw = ""
    monkeypatch.setattr(tc, "user_auth_state", UserAuthState.REQUIRED)
    monkeypatch.setattr(tc, "auth_dialogs", {})
    event = _make_event(chat_id=456)

    await tc.auth_start_command(event)

    assert 456 in tc.auth_dialogs
    assert tc.auth_dialogs[456].step == AuthStep.WAIT_PHONE
    event.reply.assert_called_once()
    assert "phone" in event.reply.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# auth_dialog_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_dialog_handler_ignores_commands(monkeypatch, app_config):
    """Messages starting with '/' should be ignored by the dialog handler."""
    monkeypatch.setattr(tc, "auth_dialogs", {})
    event = _make_event(raw_text="/auth", chat_id=456)
    replied = []
    event.reply = AsyncMock(side_effect=lambda t, **kw: replied.append(t))

    await tc.auth_dialog_handler(event)

    assert replied == []


@pytest.mark.asyncio
async def test_auth_dialog_handler_ignores_chats_without_active_dialog(
    monkeypatch, app_config
):
    """If the chat has no pending dialog, the handler should return silently."""
    monkeypatch.setattr(tc, "auth_dialogs", {})
    event = _make_event(raw_text="+1234567890", chat_id=456)

    await tc.auth_dialog_handler(event)

    event.reply.assert_not_called()


@pytest.mark.asyncio
async def test_auth_dialog_handler_phone_step_sends_code(monkeypatch, app_config):
    app_config.bot.allowed_users_raw = ""
    dialogs: dict = {456: AuthDialog(step=AuthStep.WAIT_PHONE)}
    monkeypatch.setattr(tc, "auth_dialogs", dialogs)

    sent_code_result = MagicMock()
    sent_code_result.phone_code_hash = "hash123"

    fake_user_client = AsyncMock()
    fake_user_client.send_code_request = AsyncMock(return_value=sent_code_result)
    monkeypatch.setattr(tc, "user_client", fake_user_client)

    event = _make_event(raw_text="+1234567890", chat_id=456)

    await tc.auth_dialog_handler(event)

    # Dialog should advance to code step
    assert dialogs[456].step == AuthStep.WAIT_CODE
    assert dialogs[456].phone == "+1234567890"
    assert dialogs[456].phone_code_hash == "hash123"
    event.reply.assert_called_once()


@pytest.mark.asyncio
async def test_auth_dialog_handler_phone_step_handles_send_code_error(
    monkeypatch, app_config
):
    app_config.bot.allowed_users_raw = ""
    dialogs: dict = {456: AuthDialog(step=AuthStep.WAIT_PHONE)}
    monkeypatch.setattr(tc, "auth_dialogs", dialogs)

    fake_user_client = AsyncMock()
    fake_user_client.send_code_request = AsyncMock(
        side_effect=Exception("network error")
    )
    monkeypatch.setattr(tc, "user_client", fake_user_client)

    event = _make_event(raw_text="+1234567890", chat_id=456)

    await tc.auth_dialog_handler(event)

    # Dialog should be cleaned up on error
    assert 456 not in dialogs
    event.reply.assert_called_once()
    assert "Failed" in event.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# _session_paths
# ---------------------------------------------------------------------------


def test_session_paths_returns_correct_paths(app_config, tmp_path):
    app_config.telegram.sessions_dir = tmp_path / "sessions"
    user_path, bot_path = tc._session_paths(app_config)

    assert user_path == tmp_path / "sessions" / "user.session"
    assert bot_path == tmp_path / "sessions" / "bot.session"
    assert (tmp_path / "sessions").is_dir()


# ---------------------------------------------------------------------------
# get_bot_client
# ---------------------------------------------------------------------------


def test_get_bot_client_raises_when_not_initialized(monkeypatch):
    monkeypatch.setattr(tc, "bot_client", None)
    with pytest.raises(RuntimeError) as exc:
        tc.get_bot_client()
    assert "Bot client not initialized" in str(exc.value)


def test_get_bot_client_returns_initialized_client(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(tc, "bot_client", fake_client)
    assert tc.get_bot_client() is fake_client
