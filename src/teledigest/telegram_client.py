import asyncio
import datetime as dt

from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.functions.channels import JoinChannelRequest

from .config import (
    TG_API_ID,
    TG_API_HASH,
    TG_BOT_TOKEN,
    CHANNELS,
    log,
    TG_ALLOWED_USER_IDS,
    TG_ALLOWED_USERNAMES,
)
from .db import save_message, get_relevant_messages_for_day, get_messages_for_day
from .llm import llm_summarize, build_prompt

user_client = TelegramClient("user_session", TG_API_ID, TG_API_HASH)
bot_client = TelegramClient("bot_session", TG_API_ID, TG_API_HASH)

# We'll store numeric chat IDs of channels we care about
scraped_chat_ids = set()
chat_id_to_name = {}


@user_client.on(events.NewMessage)
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
    # If no restriction configured, allow everyone
    if not TG_ALLOWED_USER_IDS and not TG_ALLOWED_USERNAMES:
        return True

    sender = await event.get_sender()
    user_id = event.sender_id
    username = getattr(sender, "username", None)
    username_norm = username.lower() if username else None

    if user_id in TG_ALLOWED_USER_IDS:
        return True
    if username_norm and username_norm in TG_ALLOWED_USERNAMES:
        return True

    return False


@bot_client.on(events.NewMessage(pattern=r"^/ping$"))
async def ping_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/today denied for user_id=%s", event.sender_id)
        # You can either ignore silently or reply:
        await event.reply("You are not allowed to use this command.")
        return

    await event.reply("pong")


@bot_client.on(events.NewMessage(pattern=r"^/today$"))
async def today_command(event):
    # permissions check if you added one
    if not await is_user_allowed(event):
        log.info("/today denied for user_id=%s", event.sender_id)
        await event.reply("You are not allowed to use this command.")
        return

    day = dt.date.today()
    log.info("/today requested by %s for %s", event.sender_id, day.isoformat())

    messages = get_relevant_messages_for_day(day, max_docs=200)

    if messages:
        summary = llm_summarize(day, messages)
        await event.reply(summary, parse_mode="html")  # or 'markdown'
    else:
        await event.reply("No messages available for today's summary.")


@bot_client.on(events.NewMessage(pattern=r"^/status$"))
async def check_command(event):
    # permissions
    if not await is_user_allowed(event):
        log.info("/status denied for user_id=%s", event.sender_id)
        # You can either ignore silently or reply:
        await event.reply("You are not allowed to use this command.")
        return

    day = dt.date.today()
    log.info("/status requested by %s for %s", event.sender_id, day.isoformat())
    messages = get_relevant_messages_for_day(day, max_docs=200)
    all_parsed = get_messages_for_day(day)

    if messages:
        system, user = build_prompt(day, messages)
        await event.reply(
            f"""Relevant messages: {len(messages)}, parsed: {len((all_parsed))}, prompt: {len(user)} symbols"""
        )
    else:
        await event.reply("No messages available for today's summary.")


async def ensure_joined_and_resolve_channels():
    """
    Using the user account:
    - join channels from CHANNELS
    - resolve their peer chat_ids (same format as event.chat_id)
    """
    global scraped_chat_ids, chat_id_to_name
    scraped_chat_ids = set()
    chat_id_to_name = {}

    for ch in CHANNELS:
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


async def start_clients():
    log.info("Starting user & bot clients...")
    log.info("Channels to scrape (user account): %s", ", ".join(CHANNELS))

    bot_client.on(events.NewMessage(pattern=r"^/ping$"))
    # 1. Start user client (you will log in with your phone on first run)
    await user_client.start()
    log.info("User client started (logged in as your account).")
    await ensure_joined_and_resolve_channels()

    # 2. Start bot client
    await bot_client.start(bot_token=TG_BOT_TOKEN)
    log.info("Bot client started (logged in as bot).")


async def run_clients():
    # Keep the clients running
    await asyncio.gather(
        user_client.run_until_disconnected(), bot_client.run_until_disconnected()
    )
