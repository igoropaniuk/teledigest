# isort: skip_file
import asyncio
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest

from .config import AppConfig, get_config, log
from .db import get_messages_last_24h, get_relevant_messages_last_24h, save_message
from .llm import build_prompt, llm_summarize

user_client: TelegramClient | None = None
bot_client: TelegramClient | None = None

# We'll store numeric chat IDs of channels we care about
scraped_chat_ids: set[int] = set()
chat_id_to_name: dict[int, str] = {}

SUPPORTED_COMMANDS: dict[str, str] = {
    "/ping": "Health check (bot replies with 'pong')",
    "/today": "Generate a digest now from the last 24 hours of messages",
    "/status": "Show bot status and configuration summary",
    "/help": "Show this help message",
}


async def channel_message_handler(event):
    """
    Handles all new messages, but only stores those from scraped_chat_ids.
    """
    chat_id = event.chat_id

    if chat_id not in scraped_chat_ids:
        return  # not one of our target channels

    msg = event.message
    text = msg.message or ""
    date = msg.date
    chat_name = chat_id_to_name.get(chat_id, str(chat_id))
    msg_id = f"{chat_name}_{msg.id}"

    log.info("Got message from %s (id=%s)", chat_name, msg.id)
    save_message(msg_id, chat_name, date, text)


async def is_user_allowed(event) -> bool:
    cfg = get_config()
    allowed_user_names = set()
    allowed_user_ids = set()

    for item in [x.strip() for x in cfg.bot.allowed_users_raw.split(",") if x.strip()]:
        if item.startswith("@"):
            allowed_user_names.add(item.lstrip("@").lower())
        else:
            try:
                allowed_user_ids.add(int(item))
            except ValueError:
                log.warning("Invalid TG_ALLOWED_USERS_RAW entry (ignored): %s", item)

    # If no restriction configured, allow everyone
    if not allowed_user_ids and not allowed_user_names:
        return True

    sender = await event.get_sender()
    user_id = event.sender_id
    username = getattr(sender, "username", None)
    username_norm = username.lower() if username else None

    if user_id in allowed_user_ids:
        return True
    if username_norm and username_norm in allowed_user_names:
        return True

    return False


async def ping_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/today denied for user_id=%s", event.sender_id)
        # You can either ignore silently or reply:
        await event.reply("You are not allowed to use this command.")
        return

    await event.reply("pong")


async def help_command(event):
    if not await is_user_allowed(event):
        log.info("/help denied for user_id=%s", event.sender_id)
        await event.reply("You are not allowed to use this command.")
        return

    lines = ["<b>Supported commands</b>", ""]
    for cmd, desc in SUPPORTED_COMMANDS.items():
        lines.append(f"<code>{cmd}</code> — {desc}")

    await event.reply("\n".join(lines), parse_mode="html")


async def today_command(event):
    # permissions check if you added one
    if not await is_user_allowed(event):
        log.info("/today denied for user_id=%s", event.sender_id)
        await event.reply("You are not allowed to use this command.")
        return

    day = dt.date.today()
    log.info(
        "/today requested by %s for rolling last 24h (labelled as %s)",
        event.sender_id,
        day.isoformat(),
    )

    messages = get_relevant_messages_last_24h(max_docs=200)

    if messages:
        summary = llm_summarize(day, messages)
        await event.reply(summary, parse_mode="html")
    else:
        await event.reply("No messages available for the last 24 hours.")


async def status_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/status denied for user_id=%s", event.sender_id)
        await event.reply("You are not allowed to use this command.")
        return

    cfg = get_config()
    tz = ZoneInfo(cfg.bot.time_zone)
    day = dt.datetime.now(tz).date()

    log.info(
        "/status requested by %s (rolling last 24h, labelled as %s in %s)",
        event.sender_id,
        day.isoformat(),
        cfg.bot.time_zone,
    )

    relevant = get_relevant_messages_last_24h(max_docs=200)
    parsed = get_messages_last_24h()

    # A light sanity check for prompt size (useful for troubleshooting)
    prompt_chars = 0
    if relevant:
        _, user_prompt = build_prompt(day, relevant)
        prompt_chars = len(user_prompt)

    digest_time = f"{cfg.bot.summary_hour:02d}:{cfg.bot.summary_minute:02d}"

    channels_list = "\n".join([f"• <code>{c}</code>" for c in cfg.bot.channels])

    text = (
        "<b>Teledigest status</b>\n\n"
        f"<b>Parsed messages (last 24h, UTC):</b> <code>{len(parsed)}</code>\n"
        f"<b>Relevant messages (last 24h, UTC):</b> <code>{len(relevant)}</code>\n"
        f"<b>Planned digest post time:</b> <code>{digest_time}</code> (<code>{cfg.bot.time_zone}</code>)\n"
        f"<b>LLM model:</b> <code>{cfg.llm.model}</code>\n"
        f"<b>Target channel:</b> <code>{cfg.bot.summary_target}</code>\n"
        f"<b>Scrape channels:</b>\n{channels_list}\n"
    )

    if relevant:
        text += f"\n<b>Current prompt size:</b> <code>{prompt_chars}</code> chars"
    else:
        text += "\n\n<i>No relevant messages found in the last 24 hours.</i>"

    await event.reply(text, parse_mode="html")


async def ensure_joined_and_resolve_channels():
    """
    Using the user account:
    - join channels from CHANNELS
    - resolve their peer chat_ids (same format as event.chat_id)
    """
    global scraped_chat_ids, chat_id_to_name
    scraped_chat_ids = set()
    chat_id_to_name = {}

    cfg = get_config()

    for ch in cfg.bot.channels:
        try:
            # Resolve entity
            ent = await user_client.get_entity(ch)

            # IMPORTANT: use peer id, not ent.id
            peer_id = await user_client.get_peer_id(ent)

            username = getattr(ent, "username", None)
            name = username if username else str(peer_id)
            chat_id_to_name[peer_id] = name

            # Try to join (if already joined, Telegram will just ignore)
            try:
                await user_client(JoinChannelRequest(ent))
                log.info("User account joined channel: %s", ch)
            except Exception as e:
                log.warning(
                    "User account could not join %s (maybe already joined): %s", ch, e
                )

            scraped_chat_ids.add(peer_id)
            log.info("Will scrape chat %s (peer_id=%s)", name, peer_id)

        except Exception as e:
            log.warning("User account cannot resolve %s: %s", ch, e)


def _session_paths(cfg: AppConfig) -> tuple[Path, Path]:
    """
    Return filesystem paths for user & bot session files,
    retrieved from the config file
    """
    sessions_dir = cfg.telegram.sessions_dir

    sessions_dir.mkdir(parents=True, exist_ok=True)

    user_session = sessions_dir / "user.session"
    bot_session = sessions_dir / "bot.session"
    return user_session, bot_session


async def create_clients():
    global user_client, bot_client

    if user_client is not None and bot_client is not None:
        return

    cfg = get_config()

    user_session_path, bot_session_path = _session_paths(cfg)

    log.info(f"Using session paths: user={user_session_path}, bot={bot_session_path}")

    user_client = TelegramClient(
        str(user_session_path), cfg.telegram.api_id, cfg.telegram.api_hash
    )
    bot_client = TelegramClient(
        str(bot_session_path), cfg.telegram.api_id, cfg.telegram.api_hash
    )

    bot_client.add_event_handler(
        status_command, events.NewMessage(pattern=r"^/status$")
    )
    bot_client.add_event_handler(help_command, events.NewMessage(pattern=r"^/help$"))
    bot_client.add_event_handler(today_command, events.NewMessage(pattern=r"^/today$"))
    bot_client.add_event_handler(ping_command, events.NewMessage(pattern=r"^/ping$"))

    user_client.add_event_handler(channel_message_handler, events.NewMessage)


async def start_clients():
    global user_client, bot_client

    cfg = get_config()
    log.info("Starting user & bot clients...")
    log.info("Channels to scrape (user account): %s", ", ".join(cfg.bot.channels))

    bot_client.on(events.NewMessage(pattern=r"^/ping$"))
    # 1. Start user client (you will log in with your phone on first run)
    await user_client.start()
    log.info("User client started (logged in as your account).")
    await ensure_joined_and_resolve_channels()

    # 2. Start bot client
    await bot_client.start(bot_token=cfg.telegram.bot_token)
    log.info("Bot client started (logged in as bot).")


async def run_clients():
    global user_client, bot_client

    # Keep the clients running
    await asyncio.gather(
        user_client.run_until_disconnected(), bot_client.run_until_disconnected()
    )


def get_bot_client() -> TelegramClient:
    if bot_client is None:
        raise RuntimeError("Bot client not initialized — call create_clients() first.")
    return bot_client
