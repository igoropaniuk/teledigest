# isort: skip_file
from __future__ import annotations

import asyncio
import os
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from enum import Enum, auto

from telethon import TelegramClient, events, functions, types
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import JoinChannelRequest

from .config import AppConfig, get_config, log
from .db import get_messages_last_24h, get_relevant_messages_last_24h, save_message, delete_bot_messages, clear_knowledge_for_reextraction
from .knowledge_loader import load_unified_claims
from .llm import build_prompt, llm_summarize, llm_summarize_brief
from .message_utils import reply_long
from .telegraph import post_to_telegraph
from .knowledge_search import is_brain_query, search_and_format
from .sources_db import (
    init_sources_table, migrate_from_config, get_active_sources,
    get_active_countries, get_digest_target,
)
from .bot_menu import (
    handle_callback, handle_text_in_conversation, get_conv,
    main_menu_keyboard,
)

user_client: TelegramClient | None = None
bot_client: TelegramClient | None = None

# We'll store numeric chat IDs of channels we care about
scraped_chat_ids: set[int] = set()
chat_id_to_name: dict[int, str] = {}
# Mapping: chat peer_id -> country code (for МОЗГ and per-country features)
chat_id_to_country: dict[int, str] = {}

ok_mark = "\u2705"
cross_mark = "\u274c"


def _find_peer_id_for_url(url: str) -> int | None:
    """Match a config channel URL to a resolved peer_id."""
    for pid, name in chat_id_to_name.items():
        # Direct match: "@username" == "username" in chat_id_to_name
        if url.lstrip("@") == name:
            return pid
        if url == f"@{name}":
            return pid
        # Full URL match: store the URL itself during resolve
        if url == name:
            return pid
    # Try via the url_to_peer_id mapping
    return _url_to_peer_id.get(url)


# Extra mapping: original config URL -> peer_id (set during resolve)
_url_to_peer_id: dict[str, int] = {}


class UserAuthState(Enum):
    OK = auto()
    REQUIRED = auto()
    IN_PROGRESS = auto()


class AuthStep(Enum):
    WAIT_PHONE = auto()
    WAIT_CODE = auto()


@dataclass
class AuthDialog:
    step: AuthStep
    phone: str | None = None
    phone_code_hash: str | None = None


user_auth_state: UserAuthState = UserAuthState.REQUIRED
auth_dialogs: dict[int, AuthDialog] = {}

SUPPORTED_COMMANDS: dict[str, str] = {
    "/auth": "Start two-factor authentication process for the client instance",
    "/help": "Show this help message",
    "/start": "Alias for /help",
    "/today": "Generate a digest now from the last 24 hours of messages",
    "/digest": "Alias for /today",
    "/status": "Show bot status and configuration summary",
    "bf <country>": "Deep backfill: pull 1 year of history (e.g. bf br)",
    "extract <country>": "Run Q&A extraction for a country (e.g. extract br)",
    "cleanup": "Delete bot messages from DB + clear knowledge for re-extraction",
    "loadkb": "Load unified_claims.jsonl into knowledge table",
    "/menu": "Open interactive menu with buttons",
}

# Track running backfill tasks to prevent duplicates
_backfill_running: set[str] = set()


async def _is_bot_sender(event) -> bool:
    """Check if the message sender is a bot."""
    try:
        sender = await event.get_sender()
        if sender and getattr(sender, "bot", False):
            return True
        # Check against blocked list from config
        cfg = get_config()
        blocked = getattr(cfg.bot, "blocked_senders", set())
        if blocked:
            sender_id = str(event.sender_id or "")
            username = (getattr(sender, "username", None) or "").lower()
            if sender_id in blocked or username in blocked:
                return True
    except Exception:
        pass
    return False


async def channel_message_handler(event):
    """
    Handles all new messages, but only stores those from scraped_chat_ids.
    Skips messages from bots and blocked senders.
    """
    chat_id = event.chat_id

    if chat_id not in scraped_chat_ids:
        return  # not one of our target channels

    # Skip bot messages
    if await _is_bot_sender(event):
        return

    msg = event.message
    text = msg.message or ""
    date = msg.date
    chat_name = chat_id_to_name.get(chat_id, str(chat_id))
    msg_id = f"{chat_name}_{msg.id}"

    # Extract reply_to
    reply_to = None
    if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
        reply_to = f"{chat_name}_{msg.reply_to.reply_to_msg_id}"

    # Get sender info
    sender = await event.get_sender()
    sid = event.sender_id
    s_bot = bool(sender and getattr(sender, "bot", False))

    log.info("Got message from %s (id=%s, reply_to=%s, sender=%s)", chat_name, msg.id, reply_to, sid)
    save_message(msg_id, chat_name, date, text, reply_to_msg_id=reply_to,
                 sender_id=sid, is_bot=s_bot)


async def is_user_allowed(event) -> bool:
    cfg = get_config()

    # If no restriction configured, allow everyone
    if not cfg.bot.allowed_user_ids and not cfg.bot.allowed_user_names:
        return True

    sender = await event.get_sender()
    username = (getattr(sender, "username", None) or "").lower() if sender else ""
    return event.sender_id in cfg.bot.allowed_user_ids or (
        bool(username) and username in cfg.bot.allowed_user_names
    )


async def help_command(event):
    if not await is_user_allowed(event):
        log.info("/help denied for user_id=%s", event.sender_id)
        await event.reply(f"{cross_mark} You are not allowed to use this command.")
        return

    lines = ["<b>Supported commands</b>", ""]
    for cmd, desc in SUPPORTED_COMMANDS.items():
        lines.append(f"<code>{cmd}</code> — {desc}")

    await event.reply("\n".join(lines), parse_mode="html")


async def today_command(event):
    # permissions check if you added one
    if not await is_user_allowed(event):
        log.info("/today denied for user_id=%s", event.sender_id)
        await event.reply(f"{cross_mark} You are not allowed to use this command.")
        return

    day = dt.date.today()
    log.info(
        "/today requested by %s for rolling last 24h (labelled as %s)",
        event.sender_id,
        day.isoformat(),
    )

    messages = get_relevant_messages_last_24h(max_docs=get_config().llm.max_messages)

    if not messages:
        await event.reply("No messages available for the last 24 hours.")
        return

    summary = llm_summarize(day, messages)

    cfg = get_config()
    if cfg.bot.summary_brief:
        telegraph_url = post_to_telegraph(
            title=f"Digest {day.isoformat()}", html=summary
        )
        brief = llm_summarize_brief(day, summary)
        outgoing = (
            f"{brief}\n\n" f'<a href="{telegraph_url}">Full digest on Telegraph</a>'
        )
    else:
        outgoing = summary

    await reply_long(event, outgoing, parse_mode="html")


async def auth_start_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/auth denied for user_id=%s", event.sender_id)
        await event.reply(f"{cross_mark} You are not allowed to use this command.")
        return

    chat_id = event.chat_id

    if user_auth_state == UserAuthState.OK:
        await event.reply(f"{ok_mark} User client is already authorized.")
        return

    auth_dialogs[chat_id] = AuthDialog(step=AuthStep.WAIT_PHONE)

    await event.reply(
        "Please send your phone number in international format:\n"
        "<code>+123456789</code>",
        parse_mode="html",
    )


async def auth_dialog_handler(event):
    # Ignore commands entirely
    if event.raw_text.startswith("/"):
        return

    chat_id = event.chat_id
    if chat_id not in auth_dialogs:
        return

    # permissions
    if not await is_user_allowed(event):
        log.info("/auth denied for user_id=%s", event.sender_id)
        await event.reply(f"{cross_mark} You are not allowed to use this command.")
        return

    dialog = auth_dialogs[chat_id]
    text = event.raw_text.strip()

    # Phone number step
    if dialog.step == AuthStep.WAIT_PHONE:
        try:
            sent = await user_client.send_code_request(text)
            dialog.phone = text
            dialog.phone_code_hash = sent.phone_code_hash
            dialog.step = AuthStep.WAIT_CODE

            await event.reply(
                "Code sent.\n"
                "Please type the 2FA code you received, but add SPACES between each digit "
                "(for example: 1 2 3 4 5).\n"
                "Do not forward the message; type the code manually."
            )
        except Exception as e:
            del auth_dialogs[chat_id]
            await event.reply(f"{cross_mark} Failed to send code: {e}</b>")

    # Code step
    elif dialog.step == AuthStep.WAIT_CODE:
        try:
            await user_client.sign_in(
                phone=dialog.phone,
                code="".join(ch for ch in text if ch.isalnum() or ch == "-"),
                phone_code_hash=dialog.phone_code_hash,
            )

            del auth_dialogs[chat_id]

            global user_auth_state
            user_auth_state = UserAuthState.OK

            await user_client.get_me()
            await ensure_joined_and_resolve_channels()
            await backfill_history(limit=1000)
            await event.reply(f"{ok_mark} Authorization successful! Backfill started.")

        except SessionPasswordNeededError:
            del auth_dialogs[chat_id]
            await event.reply(
                f"{cross_mark} This account has a password-based 2FA enabled.\n"
                "Password-based login is not yet supported."
            )
        except Exception as e:
            del auth_dialogs[chat_id]
            await event.reply(
                f"{cross_mark} Authorization failed: {e}\n"
                "Send <code>/auth</code> to try again."
            )


async def status_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/status denied for user_id=%s", event.sender_id)
        await event.reply(f"{cross_mark} You are not allowed to use this command.")
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

    relevant = get_relevant_messages_last_24h(max_docs=get_config().llm.max_messages)
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

    if user_auth_state != UserAuthState.OK:
        text += (
            f"\n\n<b>User client:</b> {cross_mark} <b>Authorization required</b>\n"
            "Use <code>/auth</code> to authorize the scraping account."
        )
    else:
        text += f"\n\n<b>User client:</b> {ok_mark} Authorized"

    if relevant:
        text += f"\n<b>Current prompt size:</b> <code>{prompt_chars}</code> chars"
    else:
        text += "\n\n<i>No relevant messages found in the last 24 hours.</i>"

    await reply_long(event, text, parse_mode="html")


async def brain_message_handler(event):
    """
    Handle МОЗГ queries.
    Works in group chats (country from chat mapping) and in DMs (searches all countries
    or uses the first configured country as default).
    """
    text = event.raw_text or ""
    query = is_brain_query(text)
    if not query:
        return

    chat_id = event.chat_id
    country = chat_id_to_country.get(chat_id)

    if not country:
        # DM with bot or unmapped chat — use first configured country as default
        cfg = get_config()
        countries = cfg.sources.countries()
        if countries:
            country = countries[0]
        else:
            country = "default"

    log.info("МОЗГ query in %s (country=%s): %s", chat_id_to_name.get(chat_id, chat_id), country, query[:80])

    response = search_and_format(country, query)
    await event.reply(response, parse_mode="html")


async def backfill_command(event):
    """Handle 'bf <country>' admin command for deep backfill."""
    if not await is_user_allowed(event):
        await event.reply(f"{cross_mark} Not allowed.")
        return

    text = event.raw_text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await event.reply("Usage: <code>bf &lt;country_code&gt;</code>\nExample: <code>bf br</code>", parse_mode="html")
        return

    country = parts[1].strip().lower()
    cfg = get_config()
    country_channels = cfg.sources.channels_for_country(country)

    if not country_channels:
        available = ", ".join(cfg.sources.countries()) or "none"
        await event.reply(
            f"{cross_mark} No channels configured for country '{country}'.\n"
            f"Available: {available}",
        )
        return

    if country in _backfill_running:
        await event.reply(f"Backfill for '{country}' is already running.")
        return

    _backfill_running.add(country)
    await event.reply(
        f"Starting deep backfill for <b>{country}</b> "
        f"({len(country_channels)} channels, up to 1 year)...",
        parse_mode="html",
    )

    from .deep_backfill import deep_backfill

    total = 0
    for ch in country_channels:
        peer_id = _find_peer_id_for_url(ch.url)
        if peer_id is None:
            await event.reply(f"Channel {ch.url} not resolved, skipping.")
            continue

        try:
            count = await deep_backfill(
                user_client, peer_id, chat_id_to_name[peer_id],
                country, ch.language,
            )
            total += count
            await event.reply(f"{ok_mark} {ch.name}: {count} messages")
        except Exception as e:
            await event.reply(f"{cross_mark} {ch.name}: {e}")

    _backfill_running.discard(country)
    await event.reply(f"Backfill for <b>{country}</b> complete: {total} messages total.", parse_mode="html")


async def extract_command(event):
    """Handle 'extract <country>' admin command for Q&A extraction."""
    if not await is_user_allowed(event):
        await event.reply(f"{cross_mark} Not allowed.")
        return

    text = event.raw_text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await event.reply("Usage: <code>extract &lt;country_code&gt;</code>\nExample: <code>extract br</code>", parse_mode="html")
        return

    country = parts[1].strip().lower()
    cfg = get_config()
    country_channels = cfg.sources.channels_for_country(country)

    if not country_channels:
        await event.reply(f"{cross_mark} No channels for country '{country}'.")
        return

    await event.reply(f"Starting Q&A extraction for <b>{country}</b>...", parse_mode="html")

    from .qa_extractor import extract_from_chat

    total = 0
    for ch in country_channels:
        peer_id = _find_peer_id_for_url(ch.url)
        if peer_id is None:
            continue

        try:
            count = await extract_from_chat(
                user_client, peer_id, chat_id_to_name[peer_id], country,
            )
            total += count
            await event.reply(f"{ok_mark} {ch.name}: {count} Q&A pairs extracted")
        except Exception as e:
            await event.reply(f"{cross_mark} {ch.name}: {e}")

    await event.reply(f"Extraction for <b>{country}</b> complete: {total} Q&A pairs total.", parse_mode="html")


async def relink_command(event):
    """Handle 'relink <country>' — update reply_to links for existing messages."""
    if not await is_user_allowed(event):
        await event.reply(f"{cross_mark} Not allowed.")
        return

    text = event.raw_text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await event.reply("Usage: <code>relink &lt;country_code&gt;</code>", parse_mode="html")
        return

    country = parts[1].strip().lower()
    cfg = get_config()
    country_channels = cfg.sources.channels_for_country(country)

    if not country_channels:
        await event.reply(f"{cross_mark} No channels for country '{country}'.")
        return

    await event.reply(f"Relinking reply chains for <b>{country}</b>...", parse_mode="html")

    from .deep_backfill import relink_replies

    total = 0
    for ch in country_channels:
        peer_id = _find_peer_id_for_url(ch.url)
        if peer_id is None:
            await event.reply(f"Channel {ch.url} not resolved, skipping.")
            continue

        try:
            updated = await relink_replies(
                user_client, peer_id, chat_id_to_name[peer_id],
            )
            total += updated
            await event.reply(f"{ok_mark} {ch.name}: {updated} reply links updated")
        except Exception as e:
            await event.reply(f"{cross_mark} {ch.name}: {e}")

    await event.reply(f"Relink for <b>{country}</b> complete: {total} links total.", parse_mode="html")


async def loadkb_command(event):
    """Handle 'loadkb [filename]' — load claims JSONL into knowledge table.

    Without argument: loads unified_claims.jsonl + overview_claims.jsonl.
    With argument: loads only the specified file (e.g. 'loadkb overview_claims.jsonl').
    """
    if not await is_user_allowed(event):
        await event.reply(f"{cross_mark} Not allowed.")
        return

    from pathlib import Path
    base_dir = Path(r"D:\temp1\_Grab\codex_teledigest-my")

    # Parse optional filename argument
    text = event.raw_text.strip()
    arg = text[len("loadkb"):].strip() if len(text) > len("loadkb") else ""

    if arg:
        files = [arg]
    else:
        files = ["unified_claims.jsonl", "overview_claims.jsonl"]

    total_stats = {"loaded": 0, "skipped": 0, "errors": 0}
    loaded_files = []

    for fname in files:
        fpath = base_dir / fname
        if not fpath.exists():
            await event.reply(f"⚠️ {fname} not found, skipping.")
            continue

        await event.reply(f"Loading {fname}...")
        stats = load_unified_claims(fpath, country="br")
        total_stats["loaded"] += stats["loaded"]
        total_stats["skipped"] += stats["skipped"]
        total_stats["errors"] += stats["errors"]
        loaded_files.append(f"{fname}: {stats['loaded']}")

    await event.reply(
        f"{ok_mark} Knowledge base loaded:\n"
        + "\n".join(f"• {f}" for f in loaded_files)
        + f"\n\nTotal: {total_stats['loaded']} loaded, "
        f"{total_stats['skipped']} skipped, {total_stats['errors']} errors",
    )


async def cleanup_command(event):
    """Handle 'cleanup' — delete bot messages from DB and clear knowledge for re-extraction."""
    if not await is_user_allowed(event):
        await event.reply(f"{cross_mark} Not allowed.")
        return

    await event.reply("Cleaning up bot messages and knowledge tables...")

    bot_count = delete_bot_messages()
    clear_knowledge_for_reextraction()

    await event.reply(
        f"{ok_mark} Cleanup done:\n"
        f"• Deleted {bot_count} bot messages from DB\n"
        f"• Cleared knowledge & extraction_log tables\n\n"
        f"Now run <code>extract &lt;country&gt;</code> to re-extract Q&A pairs.",
        parse_mode="html",
    )


async def ensure_joined_and_resolve_channels():
    """
    Using the user account:
    - join source channels for scraping
    - resolve their peer chat_ids

    Using the bot account:
    - resolve digest_target chats
    - build chat_id_to_country mapping (МОЗГ works in OUR chats, not source chats)
    """
    global scraped_chat_ids, chat_id_to_name, chat_id_to_country, _url_to_peer_id
    scraped_chat_ids = set()
    chat_id_to_name = {}
    chat_id_to_country = {}
    _url_to_peer_id = {}

    cfg = get_config()

    # --- 1. Resolve SOURCE channels via user_client (for scraping) ---
    # Merge channels from config + sources DB (dynamic)
    all_channels: list[str] = list(cfg.bot.channels)
    for src_ch in cfg.sources.channels:
        if src_ch.url not in all_channels:
            all_channels.append(src_ch.url)
    # Add channels from DB (added via /menu → add country)
    for src in get_active_sources():
        if src["url"] not in all_channels:
            all_channels.append(src["url"])

    for ch in all_channels:
        try:
            ent = await user_client.get_entity(ch)
            peer_id = await user_client.get_peer_id(ent)

            username = getattr(ent, "username", None)
            name = username if username else str(peer_id)
            chat_id_to_name[peer_id] = name

            try:
                await user_client(JoinChannelRequest(ent))
                log.info("User account joined channel: %s", ch)
            except Exception as e:
                log.warning(
                    "User account could not join %s (maybe already joined): %s", ch, e
                )

            scraped_chat_ids.add(peer_id)
            _url_to_peer_id[ch] = peer_id
            log.info("Will scrape chat %s (peer_id=%s)", name, peer_id)

        except Exception as e:
            log.warning("User account cannot resolve %s: %s", ch, e)

    # --- 2. Resolve DIGEST TARGET channels + their linked chats (for МОЗГ) ---
    # Digests go to the channel; МОЗГ lives in the linked discussion chat.
    # Bot auto-discovers the linked chat — no extra config needed.
    from telethon.tl.functions.channels import GetFullChannelRequest

    targets_to_resolve = dict(cfg.sources.digest_targets)
    # Add digest targets from DB
    for code in get_active_countries():
        dt_target = get_digest_target(code)
        if dt_target and code not in targets_to_resolve:
            targets_to_resolve[code] = dt_target
    if not targets_to_resolve and cfg.bot.summary_target:
        targets_to_resolve["default"] = cfg.bot.summary_target

    for country, target in targets_to_resolve.items():
        try:
            ent = await bot_client.get_entity(target)
            peer_id = await bot_client.get_peer_id(ent)

            # Try to find the linked discussion chat
            try:
                full = await bot_client(GetFullChannelRequest(ent))
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
                if linked_chat_id:
                    # Linked chat uses negative peer_id format for supergroups
                    linked_peer_id = -1000000000000 - linked_chat_id
                    chat_id_to_country[linked_peer_id] = country
                    log.info(
                        "МОЗГ mapped linked chat of %s (chat_id=%s) -> country=%s",
                        target, linked_peer_id, country,
                    )
                else:
                    # No linked chat — МОЗГ works directly in the channel/chat
                    chat_id_to_country[peer_id] = country
                    log.info(
                        "No linked chat for %s, МОЗГ mapped to channel itself -> country=%s",
                        target, country,
                    )
            except Exception as e:
                # Might not be a channel (could be a group chat already)
                chat_id_to_country[peer_id] = country
                log.info(
                    "Target %s is not a channel (or can't get full info: %s), "
                    "МОЗГ mapped directly -> country=%s",
                    target, e, country,
                )
        except Exception as e:
            log.warning("Bot cannot resolve digest target %s: %s", target, e)


async def backfill_history(limit: int = 1000):
    """
    Fetch the last `limit` messages from each scraped channel
    and store them in the database. Runs only if the database is empty.
    """
    from .db import get_messages_last_24h

    existing = get_messages_last_24h()
    if existing:
        log.info("Backfill: skipped — database already has %d messages.", len(existing))
        return

    if not scraped_chat_ids:
        log.info("Backfill: no channels to backfill.")
        return

    log.info("Backfill: database is empty, fetching history...")
    total = 0
    for chat_id in scraped_chat_ids:
        chat_name = chat_id_to_name.get(chat_id, str(chat_id))
        count = 0
        try:
            async for msg in user_client.iter_messages(chat_id, limit=limit):
                # Skip bot messages
                sender = getattr(msg, "sender", None)
                if sender and getattr(sender, "bot", False):
                    continue
                text = msg.message or ""
                if not text.strip():
                    continue
                msg_id = f"{chat_name}_{msg.id}"
                reply_to = None
                if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
                    reply_to = f"{chat_name}_{msg.reply_to.reply_to_msg_id}"
                sid = getattr(msg, "sender_id", None)
                s_bot = bool(sender and getattr(sender, "bot", False))
                save_message(msg_id, chat_name, msg.date, text, reply_to_msg_id=reply_to,
                             sender_id=sid, is_bot=s_bot)
                count += 1
                if count % 100 == 0:
                    await asyncio.sleep(2)
            log.info("Backfill: fetched %d messages from %s", count, chat_name)
            total += count
            await asyncio.sleep(5)
        except Exception as e:
            log.warning("Backfill failed for %s: %s", chat_name, e)

    log.info("Backfill complete: %d messages total.", total)


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


# ---------------------------------------------------------------------------
# Menu & conversation handlers
# ---------------------------------------------------------------------------

async def menu_command(event):
    """Handle /menu — show interactive keyboard."""
    if not await is_user_allowed(event):
        return
    await event.reply("Главное меню:", buttons=main_menu_keyboard())


async def menu_callback_handler(event):
    """Handle inline button presses."""
    await handle_callback(event)


async def conversation_text_handler(event):
    """Intercept text during multi-step dialogs (add country/channel flow)."""
    if not await is_user_allowed(event):
        return
    # Only intercept if user is in an active conversation
    consumed = await handle_text_in_conversation(event)
    if consumed:
        raise events.StopPropagation


async def create_clients():
    global user_client, bot_client

    if user_client is not None and bot_client is not None:
        return

    cfg = get_config()

    user_session_path, bot_session_path = _session_paths(cfg)

    log.info(f"Using session paths: user={user_session_path}, bot={bot_session_path}")

    # Optional SOCKS5/HTTP proxy via env (e.g. v2ray: socks5://127.0.0.1:10808)
    proxy = None
    proxy_url = os.environ.get("TG_PROXY")
    if proxy_url:
        from urllib.parse import urlparse
        p = urlparse(proxy_url)
        scheme = (p.scheme or "socks5").lower()
        proto = {"socks5": "socks5", "socks4": "socks4", "http": "http"}.get(scheme, "socks5")
        proxy = (proto, p.hostname, p.port)
        log.info("Using Telegram proxy: %s://%s:%s", proto, p.hostname, p.port)

    user_client = TelegramClient(
        str(user_session_path), cfg.telegram.api_id, cfg.telegram.api_hash,
        proxy=proxy,
    )
    bot_client = TelegramClient(
        str(bot_session_path), cfg.telegram.api_id, cfg.telegram.api_hash,
        proxy=proxy,
    )

    bot_client.add_event_handler(
        status_command, events.NewMessage(pattern=r"^/status$")
    )
    bot_client.add_event_handler(
        help_command, events.NewMessage(pattern=r"^/(help|start)$")
    )
    bot_client.add_event_handler(
        today_command, events.NewMessage(pattern=r"^/(today|digest)$")
    )
    bot_client.add_event_handler(
        auth_start_command, events.NewMessage(pattern=r"^/auth$")
    )
    bot_client.add_event_handler(
        backfill_command, events.NewMessage(pattern=r"^bf\s")
    )
    bot_client.add_event_handler(
        extract_command, events.NewMessage(pattern=r"^extract\s")
    )
    bot_client.add_event_handler(
        relink_command, events.NewMessage(pattern=r"^relink\s")
    )
    bot_client.add_event_handler(
        cleanup_command, events.NewMessage(pattern=r"^cleanup$")
    )
    bot_client.add_event_handler(
        loadkb_command, events.NewMessage(pattern=r"^loadkb")
    )
    bot_client.add_event_handler(
        menu_command, events.NewMessage(pattern=r"^/menu$")
    )
    bot_client.add_event_handler(
        menu_callback_handler, events.CallbackQuery()
    )
    bot_client.add_event_handler(auth_dialog_handler, events.NewMessage)
    # Conversation handler must be before МОЗГ to intercept dialog steps
    bot_client.add_event_handler(conversation_text_handler, events.NewMessage)
    # МОЗГ handler on bot_client — reacts in group chats
    bot_client.add_event_handler(brain_message_handler, events.NewMessage)

    user_client.add_event_handler(channel_message_handler, events.NewMessage)


async def set_bot_menu_commands(client: TelegramClient) -> None:
    """
    Set the bot's menu commands for easy access in the Telegram UI.
    """
    await client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code="en",
            commands=[
                types.BotCommand(command="status", description="Check system status"),
                types.BotCommand(
                    command="today", description="Request today's summary"
                ),
                types.BotCommand(command="help", description="Get help info"),
                types.BotCommand(command="auth", description="Set authentication"),
            ],
        )
    )
    log.info("Bot name for knowledge queries: %s", get_config().bot.bot_name)


async def start_clients(auth_only: bool = False) -> None:
    """
    Start Telegram clients.
    If auth_only=True: authenticate client (create client session file) and return
    without joining channels / registering the bot and its handlers.
    """
    global user_client, bot_client
    if user_client is None or bot_client is None:
        raise RuntimeError("Clients not initialized — call create_clients() first.")

    cfg = get_config()
    log.info("Starting user & bot clients...")
    log.info("Channels to scrape (user account): %s", ", ".join(cfg.bot.channels))

    # Log in with your phone on first run in CLI mode
    if auth_only:
        await user_client.start()
        log.info("Auth-only mode: skipping channel joins and handler registration.")
        return

    # Non-interactive startup
    await user_client.connect()
    await bot_client.start(bot_token=cfg.telegram.bot_token)
    await set_bot_menu_commands(bot_client)
    log.info("Bot client started (logged in as bot).")

    global user_auth_state
    if not await user_client.is_user_authorized():
        log.warning("User client not authorized. Use /auth command in the bot")
        user_auth_state = UserAuthState.REQUIRED
    else:
        user_auth_state = UserAuthState.OK
        await user_client.get_me()
        await ensure_joined_and_resolve_channels()
        await backfill_history(limit=1000)


async def _reconnect_loop(client, name: str, max_retries: int = 0):
    """
    Keep a Telethon client alive with auto-reconnect on connection loss.
    max_retries=0 means infinite retries.
    """
    attempt = 0
    while True:
        try:
            if not client.is_connected():
                log.warning("%s disconnected, reconnecting...", name)
                await client.connect()
                log.info("%s reconnected successfully.", name)
                attempt = 0
            await client.run_until_disconnected()
        except ConnectionError as e:
            attempt += 1
            if max_retries and attempt >= max_retries:
                log.error("%s: max reconnect attempts reached, giving up.", name)
                raise
            wait = min(30, 5 * attempt)  # 5s, 10s, 15s... max 30s
            log.warning(
                "%s connection error (attempt %d): %s. Retrying in %ds...",
                name, attempt, e, wait,
            )
            await asyncio.sleep(wait)
        except Exception as e:
            log.error("%s unexpected error: %s", name, e)
            await asyncio.sleep(10)


async def run_clients():
    global user_client, bot_client

    if await user_client.is_user_authorized():
        await asyncio.gather(
            _reconnect_loop(user_client, "UserClient"),
            _reconnect_loop(bot_client, "BotClient"),
        )
    else:
        await _reconnect_loop(bot_client, "BotClient")


async def disconnect_clients(auth_only: bool = False) -> None:
    """Disconnect both Telegram clients if they were initialized."""
    global user_client, bot_client

    # Telethon's disconnect() is async.
    tasks = []
    if user_client:
        tasks.append(user_client.disconnect())
    if bot_client and not auth_only:
        tasks.append(bot_client.disconnect())

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def get_bot_client() -> TelegramClient:
    if bot_client is None:
        raise RuntimeError("Bot client not initialized — call create_clients() first.")
    return bot_client
