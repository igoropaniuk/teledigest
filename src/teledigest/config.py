from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from platformdirs import user_config_dir

APP_NAME = "teledigest"
APP_AUTHOR = "Igor Opaniuk"  # optional, used on Windows


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    bot_token: str
    sessions_dir: Path = Path("data")


_DEFAULT_SYSTEM_PROMPT = """You are a helpful assistant that summarizes Telegram messages into a concise digest."""
_DEFAULT_USER_PROMPT = (
    """Summarize the following Telegram messages for {DAY}:\n\n{MESSAGES}"""
)


@dataclass
class LLMConfig:
    model: str
    api_key: str
    system_prompt: str
    user_prompt: str
    base_url: Optional[str] = None
    temperature: float = 0.4


@dataclass
class BotConfig:
    channels: List[str]
    summary_target: str
    time_zone: str = "Europe/Warsaw"
    summary_hour: int = 21
    summary_minute: int = 0
    allowed_users_raw: str = ""  # e.g. "@user1,12345678"

    def _raw_parts(self) -> List[str]:
        return [x.strip() for x in self.allowed_users_raw.split(",") if x.strip()]

    @cached_property
    def allowed_user_ids(self) -> frozenset:
        result: set[int] = set()
        for x in self._raw_parts():
            if not x.startswith("@"):
                try:
                    result.add(int(x))
                except ValueError:
                    log.warning("Invalid user ID in allowed_users: %r", x)
        return frozenset(result)

    @cached_property
    def allowed_user_names(self) -> frozenset:
        return frozenset(
            x.lstrip("@").lower() for x in self._raw_parts() if x.startswith("@")
        )


@dataclass
class StorageConfig:
    rag_keywords: list[str]
    db_path: Path = Path("messages_fts.db")


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class AppConfig:
    telegram: TelegramConfig
    bot: BotConfig
    llm: LLMConfig
    storage: StorageConfig = field(default_factory=lambda: StorageConfig([]))
    logging: LoggingConfig = field(default_factory=lambda: LoggingConfig())


_CONFIG: Optional[AppConfig] = None

log = logging.getLogger(APP_NAME)


def _default_config_path() -> Path:
    """
    Determine the default config file path in a cross-platform way.
    Example:
      - Linux:  ~/.config/teledigest/config.toml
      - macOS:  ~/Library/Application Support/teledigest/config.toml
      - Win:    %APPDATA%\\teledigest\\config.toml
    """
    config_dir = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    return config_dir / "config.toml"


def _load_toml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)
    return data


def _locate_config_path(
    explicit_path: Optional[Path] = None,
    create_parent: bool = False,
) -> Path:
    """
    Decide which config path to use.

    Precedence:
      1. explicit_path (e.g. from CLI)
      2. TELEGRAM_DIGEST_CONFIG env var
      3. OS-specific default user config path
    """
    if explicit_path is not None:
        config_path = explicit_path.expanduser()
    else:
        config_path = _default_config_path()

    if create_parent:
        config_path.parent.mkdir(parents=True, exist_ok=True)

    return config_path


def _parse_telegram(raw: Dict[str, Any]) -> TelegramConfig:
    tg_raw = raw.get("telegram") or {}
    try:
        return TelegramConfig(
            api_id=int(tg_raw["api_id"]),
            api_hash=str(tg_raw["api_hash"]),
            bot_token=str(tg_raw["bot_token"]),
            sessions_dir=Path(tg_raw.get("sessions_dir", "data")),
        )
    except KeyError as e:
        raise KeyError(f"Missing required [telegram] field in config: {e!s}") from e


def _parse_bot(raw: Dict[str, Any]) -> BotConfig:
    bot_raw = raw.get("bot") or {}
    channels = bot_raw.get("channels") or []
    if not isinstance(channels, list) or not channels:
        raise ValueError("Config [bot].channels must be a non-empty list.")

    bot = BotConfig(
        channels=[str(c).strip() for c in channels],
        summary_target=str(bot_raw.get("summary_target", "")).strip(),
        summary_hour=int(bot_raw.get("summary_hour", 21)),
        summary_minute=int(bot_raw.get("summary_minute", 0)),
        allowed_users_raw=str(bot_raw.get("allowed_users", "")),
        time_zone=str(bot_raw.get("time_zone", "Europe/Warsaw")),
    )

    if not bot.summary_target:
        raise ValueError("Config [bot].summary_target is required.")
    if not (0 <= bot.summary_hour <= 23):
        raise ValueError("Config [bot].summary_hour must be between 0 and 23.")
    if not (0 <= bot.summary_minute <= 59):
        raise ValueError("Config [bot].summary_minute must be between 0 and 59.")

    return bot


def _parse_storage(raw: Dict[str, Any]) -> StorageConfig:
    storage_raw = raw.get("storage") or {}
    rag_raw = storage_raw.get("rag") or {}
    db_path_str = storage_raw.get("db_path", "data/messages_fts.db")
    keywords = rag_raw.get("keywords") or []
    return StorageConfig(db_path=Path(db_path_str), rag_keywords=keywords)


def _parse_llm(raw: Dict[str, Any]) -> LLMConfig:
    llm_raw = raw.get("llm") or {}
    prompts_raw = llm_raw.get("prompts") or {}
    llm = LLMConfig(
        api_key=str(llm_raw.get("api_key", "")),
        model=str(llm_raw.get("model", "gpt-5.1")),
        system_prompt=str(prompts_raw.get("system", _DEFAULT_SYSTEM_PROMPT)),
        user_prompt=str(prompts_raw.get("user", _DEFAULT_USER_PROMPT)),
        base_url=str(llm_raw.get("base_url", "")) or None,
        temperature=float(llm_raw.get("temperature", 0.4)),
    )

    if not (0.0 <= llm.temperature <= 2.0):
        raise ValueError("Config [llm].temperature must be between 0.0 and 2.0.")
    if not llm.api_key:
        raise ValueError("Config [llm].api_key is required.")

    return llm


def _parse_logging(raw: Dict[str, Any]) -> LoggingConfig:
    logging_raw = raw.get("logging") or {}
    level = str(logging_raw.get("level", "INFO"))
    if not isinstance(getattr(logging, level.upper(), None), int):
        raise ValueError(
            f"Config [logging].level is invalid: {level!r}. "
            "Valid values are: DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )
    return LoggingConfig(level=level)


def _parse_app_config(raw: Dict[str, Any]) -> AppConfig:
    """
    Convert the raw TOML dict into typed AppConfig.
    Raises KeyError/ValueError if required sections/fields are missing or invalid.
    """
    return AppConfig(
        telegram=_parse_telegram(raw),
        bot=_parse_bot(raw),
        llm=_parse_llm(raw),
        storage=_parse_storage(raw),
        logging=_parse_logging(raw),
    )


def init_config(
    explicit_path: Optional[Path] = None,
) -> AppConfig:

    global _CONFIG
    if _CONFIG is not None:
        if explicit_path is not None:
            log.warning(
                "init_config() called again with explicit_path=%s, "
                "but config is already loaded; the new path will be ignored.",
                explicit_path,
            )
        return _CONFIG

    config_path = _locate_config_path(explicit_path)

    log.debug("Loading config from %s", config_path)
    raw = _load_toml(config_path)
    _CONFIG = _parse_app_config(raw)
    _configure_logging(_CONFIG.logging)

    return _CONFIG


def get_config() -> AppConfig:
    """
    Get the global AppConfig.
    Raises if init_config() hasn't been called yet.
    """
    if _CONFIG is None:
        raise RuntimeError("Config not initialized. Call init_config() first.")
    return _CONFIG


def _configure_logging(logging_cfg: LoggingConfig) -> None:
    """
    Basic logging setup based on config.
    """
    level = getattr(logging, logging_cfg.level.upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    log.info("Logging configured at %s level", logging_cfg.level.upper())
