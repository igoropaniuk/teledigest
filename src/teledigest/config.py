from __future__ import annotations

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def env_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


TG_API_ID = int(env_required("TG_API_ID"))
TG_API_HASH = env_required("TG_API_HASH")
TG_BOT_TOKEN = env_required("TG_BOT_TOKEN")

TG_ALLOWED_USERS_RAW = os.getenv("TG_ALLOWED_USERS_RAW", "")
TG_ALLOWED_USER_IDS = set()
TG_ALLOWED_USERNAMES = set()

TIMEZONE = env_required("TIMEZONE")

for item in [x.strip() for x in TG_ALLOWED_USERS_RAW.split(",") if x.strip()]:
    if item.startswith("@"):
        TG_ALLOWED_USERNAMES.add(item.lstrip("@").lower())
    else:
        try:
            TG_ALLOWED_USER_IDS.add(int(item))
        except ValueError:
            log.warning("Invalid TG_ALLOWED_USERS_RAW entry (ignored): %s", item)

CHANNELS_RAW = os.getenv("CHANNELS", "")
CHANNELS = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]
if not CHANNELS:
    raise RuntimeError("CHANNELS in .env is empty - add at least one channel.")

SUMMARY_TARGET = env_required("SUMMARY_TARGET")
OPENAI_API_KEY = env_required("OPENAI_API_KEY")
SUMMARY_HOUR = int(os.getenv("SUMMARY_HOUR", 21))

DB_PATH = Path("messages_fts.db")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)
log = logging.getLogger("teledigest")
