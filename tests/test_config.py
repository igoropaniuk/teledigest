from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from teledigest import config


def _make_minimal_raw() -> Dict[str, Any]:
    """Helper: minimal valid raw config dict (as if loaded from TOML)."""
    return {
        "telegram": {
            "api_id": 123456,
            "api_hash": "hash",
            "bot_token": "token",
        },
        "bot": {
            "channels": ["@ch1", "@ch2"],
            "summary_target": "@digest",
            "summary_hour": 8,
            "allowed_users": "@admin,123",
            "time_zone": "Europe/Warsaw",
        },
        "llm": {
            "api_key": "sk-test",
            # intentionally omit "model" to test default
            # prompts will be omitted in some tests to use defaults
        },
    }


# ---------------------------------------------------------------------------
# _load_toml
# ---------------------------------------------------------------------------


def test_load_toml_success(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
        [telegram]
        api_id = 1
        api_hash = "hash"
        bot_token = "token"

        [bot]
        channels = ["@ch"]
        summary_target = "@digest"

        [llm]
        api_key = "sk-test"
        """
    )

    data = config._load_toml(cfg_path)
    assert data["telegram"]["api_id"] == 1
    assert data["bot"]["channels"] == ["@ch"]
    assert data["llm"]["api_key"] == "sk-test"


def test_load_toml_missing_file_raises() -> None:
    missing = Path("this/does/not/exist.toml")
    with pytest.raises(FileNotFoundError):
        config._load_toml(missing)


# ---------------------------------------------------------------------------
# _parse_app_config – happy path
# ---------------------------------------------------------------------------


def test_parse_app_config_valid_minimal_defaults() -> None:
    raw = _make_minimal_raw()

    app_cfg = config._parse_app_config(raw)

    # telegram
    assert app_cfg.telegram.api_id == 123456
    assert app_cfg.telegram.api_hash == "hash"
    assert app_cfg.telegram.bot_token == "token"

    # bot
    assert app_cfg.bot.channels == ["@ch1", "@ch2"]
    assert app_cfg.bot.summary_target == "@digest"
    assert app_cfg.bot.summary_hour == 8
    assert app_cfg.bot.allowed_users_raw == "@admin,123"
    assert app_cfg.bot.time_zone == "Europe/Warsaw"

    # storage defaults
    # default db path and empty keyword list when [storage] is missing
    assert app_cfg.storage.db_path == Path("data/messages_fts.db")
    assert app_cfg.storage.rag_keywords == []

    # llm
    assert app_cfg.llm.api_key == "sk-test"
    # model default from config._DEFAULT_* if not provided
    assert app_cfg.llm.model == "gpt-5.1"
    # prompts default to builtin prompts if [llm.prompts] missing
    assert app_cfg.llm.system_prompt == config._DEFAULT_SYSTEM_PROMPT
    assert app_cfg.llm.user_prompt == config._DEFAULT_USER_PROMPT

    # logging default
    assert app_cfg.logging.level == "INFO"


def test_parse_app_config_with_storage_and_rag() -> None:
    raw = _make_minimal_raw()
    raw["storage"] = {
        "db_path": "/tmp/messages.db",
        "rag": {
            "keywords": ["foo", "bar"],
        },
    }

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.storage.db_path == Path("/tmp/messages.db")
    assert app_cfg.storage.rag_keywords == ["foo", "bar"]


def test_parse_app_config_with_custom_llm_prompts_and_model() -> None:
    raw = _make_minimal_raw()
    raw["llm"]["model"] = "gpt-4.1"
    raw["llm"]["prompts"] = {
        "system": "SYSTEM PROMPT",
        "user": "USER PROMPT",
    }

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.model == "gpt-4.1"
    assert app_cfg.llm.system_prompt == "SYSTEM PROMPT"
    assert app_cfg.llm.user_prompt == "USER PROMPT"


# ---------------------------------------------------------------------------
# _parse_app_config – validation / error cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_key", ["api_id", "api_hash", "bot_token"])
def test_parse_app_config_telegram_missing_required_field(missing_key: str) -> None:
    raw = _make_minimal_raw()
    del raw["telegram"][missing_key]

    with pytest.raises(KeyError) as exc:
        config._parse_app_config(raw)

    # message should mention [telegram]
    assert "[telegram]" in str(exc.value)


@pytest.mark.parametrize(
    "channels_value",
    [
        [],  # empty list
        None,  # None
        "@not-a-list",  # wrong type
    ],
)
def test_parse_app_config_bot_channels_must_be_non_empty_list(channels_value: Any) -> None:  # type: ignore[override]
    raw = _make_minimal_raw()
    raw["bot"]["channels"] = channels_value

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "Config [bot].channels must be a non-empty list." in str(exc.value)


def test_parse_app_config_bot_summary_target_required() -> None:
    raw = _make_minimal_raw()
    raw["bot"]["summary_target"] = "   "  # only spaces

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "Config [bot].summary_target is required." in str(exc.value)


def test_parse_app_config_llm_api_key_required() -> None:
    raw = _make_minimal_raw()
    raw["llm"]["api_key"] = ""

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "Config [llm].api_key is required." in str(exc.value)


def test_parse_app_config_llm_base_url_provided() -> None:
    raw = _make_minimal_raw()
    raw["llm"]["base_url"] = "http://localhost"

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.base_url == "http://localhost"


def test_parse_app_config_llm_base_url_not_provided() -> None:
    raw = _make_minimal_raw()

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.base_url is None


def test_parse_app_config_llm_base_url_empty_str() -> None:
    raw = _make_minimal_raw()
    raw["llm"]["base_url"] = ""

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.base_url is None


# ---------------------------------------------------------------------------
# _parse_app_config – logging.level validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_level", ["TRACE", "VERBOSE", "ALL", "WARN1", ""])
def test_parse_app_config_invalid_logging_level_raises(bad_level: str) -> None:
    raw = _make_minimal_raw()
    raw["logging"] = {"level": bad_level}

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "[logging].level" in str(exc.value)


@pytest.mark.parametrize(
    "good_level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
)
def test_parse_app_config_valid_logging_levels_accepted(good_level: str) -> None:
    raw = _make_minimal_raw()
    raw["logging"] = {"level": good_level}

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.logging.level == good_level


def test_parse_app_config_logging_level_is_case_insensitive() -> None:
    raw = _make_minimal_raw()
    raw["logging"] = {"level": "debug"}

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.logging.level == "debug"


# ---------------------------------------------------------------------------
# _locate_config_path / _default_config_path
# ---------------------------------------------------------------------------


def test_locate_config_path_uses_explicit_path(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.toml"

    result = config._locate_config_path(explicit_path=explicit)

    # Should be exactly the expanded path
    assert result == explicit.expanduser()


def test_locate_config_path_falls_back_to_default_when_no_explicit() -> None:
    default = config._default_config_path()
    result = config._locate_config_path()

    assert result == default


def test_locate_config_path_create_parent(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "cfg.toml"
    parent_dir = nested.parent

    assert not parent_dir.exists()

    result = config._locate_config_path(explicit_path=nested, create_parent=True)

    assert result == nested
    assert parent_dir.is_dir()


# ---------------------------------------------------------------------------
# init_config / get_config integration
# ---------------------------------------------------------------------------


def test_init_config_and_get_config_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reset global state for this test
    monkeypatch.setattr(config, "_CONFIG", None, raising=False)

    cfg_path = tmp_path / "teledigest.toml"
    cfg_path.write_text(
        """
        [telegram]
        api_id = 42
        api_hash = "hash42"
        bot_token = "token42"

        [bot]
        channels = ["@c1", "@c2"]
        summary_target = "@digest42"
        summary_hour = 10
        allowed_users = "@admin42"
        time_zone = "Europe/Warsaw"

        [llm]
        api_key = "sk-42"

        [storage]
        db_path = "messages_fts.db"

        [storage.rag]
        keywords = ["k1", "k2"]

        [logging]
        level = "DEBUG"
        """
    )

    app_cfg = config.init_config(explicit_path=cfg_path)

    # get_config should now return the same instance
    same_cfg = config.get_config()
    assert same_cfg is app_cfg

    # basic sanity checks
    assert app_cfg.telegram.api_id == 42
    assert app_cfg.bot.channels == ["@c1", "@c2"]
    assert app_cfg.llm.api_key == "sk-42"
    assert app_cfg.storage.rag_keywords == ["k1", "k2"]
    assert app_cfg.logging.level == "DEBUG"


def test_init_config_second_call_with_explicit_path_emits_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A second init_config() with an explicit path must not silently discard it.

    Callers that pass an explicit path expect it to take effect.  Silently
    returning the cached config makes misconfigured test setups and multi-
    process deployments very hard to debug.  The warning makes the "first
    caller wins" contract visible in logs.
    """
    # Provide an already-loaded config instance
    dummy_cfg = config._parse_app_config(
        {
            "telegram": {"api_id": 1, "api_hash": "h", "bot_token": "t"},
            "bot": {"channels": ["@c"], "summary_target": "@d"},
            "llm": {"api_key": "sk-x"},
        }
    )
    monkeypatch.setattr(config, "_CONFIG", dummy_cfg, raising=False)

    second_path = tmp_path / "other.toml"

    with caplog.at_level("WARNING", logger="teledigest"):
        result = config.init_config(explicit_path=second_path)

    # The cached config is still returned
    assert result is dummy_cfg
    # The caller is warned that their path was ignored
    assert any("ignored" in r.message for r in caplog.records)
    assert any(str(second_path) in r.message for r in caplog.records)


def test_init_config_second_call_without_explicit_path_is_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Re-calling init_config() with no path is the normal 'get current config'
    pattern and must not produce any warnings."""
    dummy_cfg = config._parse_app_config(
        {
            "telegram": {"api_id": 1, "api_hash": "h", "bot_token": "t"},
            "bot": {"channels": ["@c"], "summary_target": "@d"},
            "llm": {"api_key": "sk-x"},
        }
    )
    monkeypatch.setattr(config, "_CONFIG", dummy_cfg, raising=False)

    with caplog.at_level("WARNING", logger="teledigest"):
        result = config.init_config()

    assert result is dummy_cfg
    assert caplog.records == []


def test_get_config_raises_if_not_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_CONFIG", None, raising=False)

    with pytest.raises(RuntimeError) as exc:
        config.get_config()

    assert "Config not initialized" in str(exc.value)


# ---------------------------------------------------------------------------
# summary_brief
# ---------------------------------------------------------------------------


def test_parse_app_config_summary_brief_defaults_to_false() -> None:
    raw = _make_minimal_raw()

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.bot.summary_brief is False


def test_parse_app_config_summary_brief_true() -> None:
    raw = _make_minimal_raw()
    raw["bot"]["summary_brief"] = True

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.bot.summary_brief is True


# ---------------------------------------------------------------------------
# brief prompts
# ---------------------------------------------------------------------------


def test_parse_app_config_brief_prompts_default_to_builtins() -> None:
    raw = _make_minimal_raw()

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.system_brief_prompt == config._DEFAULT_SYSTEM_BRIEF_PROMPT
    assert app_cfg.llm.user_brief_prompt == config._DEFAULT_USER_BRIEF_PROMPT


def test_parse_app_config_brief_prompts_custom() -> None:
    raw = _make_minimal_raw()
    raw["llm"]["prompts"] = {
        "system": "SYS",
        "user": "USR {DAY} {MESSAGES}",
        "system_brief": "SYS_BRIEF",
        "user_brief": "USR_BRIEF {DIGEST}",
    }

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.system_brief_prompt == "SYS_BRIEF"
    assert app_cfg.llm.user_brief_prompt == "USR_BRIEF {DIGEST}"


def test_parse_app_config_brief_prompts_partial_override() -> None:
    """Only system_brief is overridden; user_brief should keep the default."""
    raw = _make_minimal_raw()
    raw["llm"]["prompts"] = {"system_brief": "CUSTOM_SYSTEM_BRIEF"}

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.llm.system_brief_prompt == "CUSTOM_SYSTEM_BRIEF"
    assert app_cfg.llm.user_brief_prompt == config._DEFAULT_USER_BRIEF_PROMPT


# ---------------------------------------------------------------------------
# TelegraphConfig / _parse_telegraph
# ---------------------------------------------------------------------------


def test_parse_telegraph_defaults_when_section_absent() -> None:
    raw = _make_minimal_raw()

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.telegraph.author_name == "TeleDigest"
    assert app_cfg.telegraph.author_url == ""
    assert app_cfg.telegraph.access_token is None


def test_parse_telegraph_custom_author_name() -> None:
    raw = _make_minimal_raw()
    raw["telegraph"] = {"author_name": "My Bot"}

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.telegraph.author_name == "My Bot"


def test_parse_telegraph_explicit_access_token() -> None:
    raw = _make_minimal_raw()
    raw["telegraph"] = {"access_token": "tok123"}

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.telegraph.access_token == "tok123"


def test_parse_telegraph_access_token_absent_is_none() -> None:
    raw = _make_minimal_raw()
    raw["telegraph"] = {"author_name": "Bot"}  # no access_token key

    app_cfg = config._parse_app_config(raw)

    assert app_cfg.telegraph.access_token is None


# ---------------------------------------------------------------------------
# _parse_bot – summary_hour / summary_minute range validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_hour", [-1, 24, 100])
def test_parse_app_config_summary_hour_out_of_range_raises(bad_hour: int) -> None:
    raw = _make_minimal_raw()
    raw["bot"]["summary_hour"] = bad_hour

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "summary_hour" in str(exc.value)


@pytest.mark.parametrize("bad_minute", [-1, 60, 100])
def test_parse_app_config_summary_minute_out_of_range_raises(bad_minute: int) -> None:
    raw = _make_minimal_raw()
    raw["bot"]["summary_minute"] = bad_minute

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "summary_minute" in str(exc.value)


# ---------------------------------------------------------------------------
# _parse_llm – temperature range validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_temp", [-0.1, 2.1, 5.0])
def test_parse_app_config_temperature_out_of_range_raises(bad_temp: float) -> None:
    raw = _make_minimal_raw()
    raw["llm"]["temperature"] = bad_temp

    with pytest.raises(ValueError) as exc:
        config._parse_app_config(raw)

    assert "temperature" in str(exc.value)


# ---------------------------------------------------------------------------
# BotConfig.allowed_user_ids – invalid entry warning
# ---------------------------------------------------------------------------


def test_bot_config_allowed_user_ids_skips_invalid_entry_and_emits_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-integer, non-@ values in allowed_users must be skipped with a WARNING.

    The cached_property computes lazily on first access; the warning should
    appear at that point and the invalid token must not appear in the result.
    """
    raw = _make_minimal_raw()
    raw["bot"]["allowed_users"] = "123,notanid,@admin"

    app_cfg = config._parse_app_config(raw)

    with caplog.at_level("WARNING", logger="teledigest"):
        ids = app_cfg.bot.allowed_user_ids

    assert ids == {123}
    assert any("notanid" in r.message for r in caplog.records)
