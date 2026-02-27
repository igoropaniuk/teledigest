from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teledigest import config as cfg
from teledigest import llm
from teledigest.db import Message


def _make_app_config() -> cfg.AppConfig:
    telegram = cfg.TelegramConfig(
        api_id=1, api_hash="hash", bot_token="token", sessions_dir=Path("testdata")
    )
    bot = cfg.BotConfig(
        channels=["@c1"],
        summary_target="@digest",
        time_zone="UTC",
    )
    llm_cfg = cfg.LLMConfig(
        model="gpt-4.1",
        api_key="sk-test",
        system_prompt="You are a helpful assistant.",
        user_prompt="Summarize messages for {DAY}:\n\n{MESSAGES}",
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


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_empty_messages_returns_fallback(app_config):
    system, user = llm.build_prompt(dt.date(2024, 1, 1), [])
    assert "No messages" in user
    assert "2024-01-01" in user


def test_build_prompt_formats_messages_with_channel_prefix(app_config):
    messages = [
        Message(channel="@c1", text="First message"),
        Message(channel="@c2", text="Second message"),
    ]
    day = dt.date(2024, 6, 15)
    system, user = llm.build_prompt(day, messages)

    assert system == "You are a helpful assistant."
    assert "2024-06-15" in user
    assert "[@c1] First message" in user
    assert "[@c2] Second message" in user


def test_build_prompt_truncates_messages_longer_than_500_chars(app_config):
    long_text = "x" * 600
    messages = [Message(channel="@c1", text=long_text)]
    _, user = llm.build_prompt(dt.date(2024, 1, 1), messages)

    # The line should contain a truncation marker
    assert "..." in user
    # Extract the content portion after "[@c1] "
    line = next(ln for ln in user.split("\n") if "[@c1]" in ln)
    content = line[len("[@c1] ") :]
    assert len(content) <= 504  # 500 chars + " ..."


def test_build_prompt_caps_messages_at_500_items(app_config):
    messages = [Message(channel="@c", text=f"msg {i}") for i in range(600)]
    _, user = llm.build_prompt(dt.date(2024, 1, 1), messages)

    assert "msg 499" in user
    assert "msg 500" not in user


def test_build_prompt_skips_blank_text_entries(app_config):
    messages = [
        Message(channel="@c1", text=""),
        Message(channel="@c2", text="   "),
        Message(channel="@c3", text="real content"),
    ]
    _, user = llm.build_prompt(dt.date(2024, 1, 1), messages)

    assert "[@c1]" not in user
    assert "[@c2]" not in user
    assert "[@c3] real content" in user


def test_build_prompt_normalises_internal_whitespace(app_config):
    messages = [Message(channel="@c1", text="hello   world\n\ttab")]
    _, user = llm.build_prompt(dt.date(2024, 1, 1), messages)

    assert "[@c1] hello world tab" in user


def test_build_prompt_injects_timezone_placeholder(app_config):
    # user_prompt must support {TIMEZONE} in addition to {DAY}/{MESSAGES}
    app_config.llm.user_prompt = "Day={DAY} TZ={TIMEZONE} Msgs={MESSAGES}"
    messages = [Message(channel="@c1", text="news")]
    _, user = llm.build_prompt(dt.date(2024, 1, 1), messages)

    assert "TZ=UTC" in user


# ---------------------------------------------------------------------------
# llm_summarize
# ---------------------------------------------------------------------------


def test_llm_summarize_returns_stripped_content(app_config):
    messages = [Message(channel="@c1", text="war update")]
    day = dt.date(2024, 1, 1)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "  Summary content  "

    with patch("teledigest.llm.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = llm.llm_summarize(day, messages)

    assert result == "Summary content"


def test_llm_summarize_strips_markdown_fence(app_config):
    messages = [Message(channel="@c1", text="news")]
    day = dt.date(2024, 1, 1)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "```\nSummary\n```"

    with patch("teledigest.llm.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = llm.llm_summarize(day, messages)

    assert result == "Summary"


def test_llm_summarize_handles_api_error_gracefully(app_config):
    messages = [Message(channel="@c1", text="news")]
    day = dt.date(2024, 1, 1)

    with patch("teledigest.llm.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API timeout")

        result = llm.llm_summarize(day, messages)

    assert "Failed to generate" in result
    assert "2024-01-01" in result
    assert "API timeout" in result


def test_llm_summarize_uses_configured_model_and_temperature(app_config):
    messages = [Message(channel="@c1", text="news")]
    day = dt.date(2024, 1, 1)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "OK"

    with patch("teledigest.llm.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        llm.llm_summarize(day, messages)

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "gpt-4.1"
    assert call_kwargs.kwargs["temperature"] == 0.4


def test_llm_summarize_passes_system_and_user_messages(app_config):
    messages = [Message(channel="@c1", text="news")]
    day = dt.date(2024, 1, 1)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "summary"

    with patch("teledigest.llm.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        llm.llm_summarize(day, messages)

    call_kwargs = mock_client.chat.completions.create.call_args
    msgs_arg = call_kwargs.kwargs["messages"]
    roles = [m["role"] for m in msgs_arg]
    assert roles == ["system", "user"]
