#!/usr/bin/env python3
import os
import asyncio
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.functions.channels import JoinChannelRequest
import openai

# ==========================
# Logging
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)
log = logging.getLogger("telegram_digest_bot")

# ==========================
# Load config
# ==========================
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

# ==========================
# OpenAI (global style)
# ==========================
openai.api_key = OPENAI_API_KEY


# ==========================
# DB helpers
# ==========================
# ==========================
# DB helpers (with FTS5 for RAG)
# ==========================
def init_db():
    log.info("Initializing SQLite database at %s", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Main table: one row per message
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            channel TEXT,
            date TEXT,
            text TEXT
        )
        """
    )

    # FTS virtual table for full-text search (RAG retrieval)
    try:
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(
                id,
                channel,
                date,
                text
            )
            """
        )
        log.info("FTS5 virtual table messages_fts initialized.")
    except sqlite3.OperationalError as e:
        log.error("Failed to create FTS5 table (does your SQLite support FTS5?): %s", e)

    conn.commit()
    conn.close()


def save_message(msg_id: str, channel: str, date: dt.datetime, text: str):
    if not text:
        return
    iso = date.isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # main table (id is unique)
    cur.execute(
        """
        INSERT OR IGNORE INTO messages (id, channel, date, text)
        VALUES (?, ?, ?, ?)
        """,
        (msg_id, channel, iso, text),
    )

    # FTS index – no uniqueness, but we insert once per message
    try:
        cur.execute(
            """
            INSERT INTO messages_fts (id, channel, date, text)
            VALUES (?, ?, ?, ?)
            """,
            (msg_id, channel, iso, text),
        )
    except sqlite3.OperationalError as e:
        # Likely FTS5 not available; we just log and continue
        log.warning("Failed to insert into messages_fts (FTS disabled?): %s", e)

    conn.commit()
    conn.close()


def get_messages_for_day(day: dt.date, limit: int | None = None):
    """
    Fallback: simple 'all messages for the day' from main table,
    optionally limited.
    """
    start = dt.datetime.combine(day, dt.time.min).isoformat()
    end = dt.datetime.combine(day, dt.time.max).isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    sql = """
        SELECT channel, text FROM messages
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    cur.execute(sql, (start, end))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_relevant_messages_for_day(day: dt.date, max_docs: int = 200):
    """
    RAG-style retrieval:
    Use the FTS index to get the most relevant messages for today's
    'important news' queries instead of sending everything to the LLM.

    If FTS5 is not available or returns nothing, falls back to
    get_messages_for_day(day, limit=max_docs).
    """
    start = dt.datetime.combine(day, dt.time.min).isoformat()
    end = dt.datetime.combine(day, dt.time.max).isoformat()

    # Query tuned for 'important news' (Ukr/Eng mix; tweak as you like)
    query = (
        # ===============================
        # 🇺🇦 Ukrainian — war, politics
        # ===============================
        "війна OR наступ* OR контрнаступ* OR фронт OR лінія OR оборон* "
        "OR штурм* OR артилер* OR обстріл* OR удар* OR ракета* OR безпілотн* "
        "OR дрон* OR ППО OR мобілізац* OR призов* OR резерв* OR втрат* "
        "OR збройн* OR ЗСУ OR Сили OR Оборони OR Генштаб OR Міноборони "
        "OR санкц* OR економік* OR енергетик* OR ринок* OR бюджет* "
        "OR НАТО OR ЄС OR Європейськ* OR допомог* OR підтримк* "
        "OR переговор* OR дипломат* "
        # Key persons UA
        "OR Зеленськ* OR Умеров OR Умєров "
        # ===============================
        # 🇷🇺 Russian — war, politics
        # ===============================
        "OR войн* OR наступлен* OR контрнаступ* OR фронт OR линия "
        "OR оборон* OR штурм* OR артилл* OR обстрел* OR удар* OR ракет* "
        "OR беспилотн* OR дрон* OR ПВО OR мобилизац* OR призыв OR резерв* "
        "OR потерь OR армия OR ВСУ OR Минобороны "
        "OR санкц* OR экономик* OR энергетик* OR бюджет* OR рынок* "
        "OR НАТО OR ЕС OR Европейск* OR помощ* OR поддержк* "
        "OR переговор* OR дипломат* "
        # Key persons RU
        "OR Зеленск* OR Умеров "
        # ===============================
        # 🇬🇧 English — war, geopolitics
        # ===============================
        "OR war OR offensive OR counteroffensive OR front OR frontline "
        "OR defense OR assault OR artillery OR shell* OR strike* OR attack* "
        "OR missile* OR drone* OR UAV OR air OR defense OR mobilization "
        "OR draft OR reserve OR casualties OR military OR armed OR forces "
        "OR sanctions OR economy OR energy OR market OR budget "
        "OR NATO OR EU OR European OR aid OR support "
        "OR negotiations OR diplomacy "
        # Key persons EN
        "OR Zelensky OR Zelenskiy OR Zelenskyy OR Umerov"
    )

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        sql = f"""
            SELECT channel, text
            FROM messages_fts
            WHERE messages_fts MATCH ?
              AND date BETWEEN ? AND ?
            ORDER BY date ASC
            LIMIT {int(max_docs)}
        """
        cur.execute(sql, (query, start, end))
        rows = cur.fetchall()
        conn.close()

        if rows:
            log.info(
                "FTS retrieval for %s returned %d messages (max %d).",
                day.isoformat(),
                len(rows),
                max_docs,
            )
            return rows
        else:
            log.info(
                "FTS retrieval returned 0 rows for %s – falling back to simple day range.",
                day.isoformat(),
            )

    except sqlite3.OperationalError as e:
        # Happens when FTS5 is not available
        log.warning("FTS retrieval failed (%s). Falling back to full day scan.", e)
        conn.close()

    # Fallback: simple scan limited to max_docs
    return get_messages_for_day(day, limit=max_docs)


# ==========================
# LLM summarization
# ==========================
def build_prompt(day: dt.date, messages):
    if not messages:
        return (
            "You are a helpful assistant.",
            f"No messages to summarize for {day.isoformat()}.",
        )

    lines = []
    max_items = 500
    max_chars_per_msg = 500

    for channel, text in messages[:max_items]:
        t = " ".join(text.split())
        if not t:
            continue
        if len(t) > max_chars_per_msg:
            t = t[:max_chars_per_msg] + " ..."
        lines.append(f"[{channel}] {t}")

    corpus = "\n".join(lines)

    system = (
        "Ти — асистент, який аналізує новини та класифікує їх у форматі «зрада / перемога / не все так однозначно». "
        "Ти отримуєш багато твітів з різних джерел. "
        "Твоє завдання — структурувати їх у три категорії:\n\n"
        "1. ЗРАДА — погані новини, негативні наслідки, невдачі, програші, втрати, корупція, скандали, загрози.\n"
        "2. ПЕРЕМОГА — хороші новини, успіхи, прогрес, досягнення, здобутки, позитивні зрушення.\n"
        "3. НЕ ВСЕ ТАК ОДНОЗНАЧНО — складні, неоднозначні або змішані події; інформація, яку важко віднести однозначно до позитиву чи негативу; "
        "суперечливі оцінки або ситуації з потенційно різними трактуваннями.\n\n"
        "Головне: Чітко структуруй інформацію, не вигадуй фактів, не перекручуй зміст. "
        "Об’єднуй схожі твіти в один пункт."
    )

    user = f"""
        Сьогодні {day.isoformat()} у часовому поясі {TIMEZONE}.

        Нижче наведено твіти з різних Telegram-акаунтів:

        {corpus}

        Завдання:

        1. Проаналізуй всі твіти й розподіли їх на три секції:
        - 🟥 **ЗРАДА**
        - 🟩 **ПЕРЕМОГА**
        - 🟨 **НЕ ВСЕ ТАК ОДНОЗНАЧНО**

        2. У кожній секції створи список маркованих пунктів:
        - Кожен пункт повинен об’єднувати кілька схожих твітів (якщо вони про одне й те саме), по можливості використовуй емодзі.
        - Вказуй факти коротко, чітко, без оціночних суджень.

        3. Наприкінці додай короткий (2–3 речення) загальний підсумок дня.
        4. Зосередься виключно на важливих новинах, що стосуються України та геополітики й можуть вплинути на війну проти України;
        ігноруй усі інші новини. Також ігноруй меми, дрібну балаканину, рекламу та теми, пов’язані з особистим здоров’ям.

        Не використовуй синтаксис Markdown (жодних #, *, ``` тощо).
        Формат виводу: тільки Telegram HTML.
        Використовуй теги <b>, <i>, <u>, <code>, <a href='…'> та символ • для списків.
        Уся відповідь повинна бути українською мовою і обов'язково не більше 2000 символів.
        Reply should be only in Ukrainian and less than 2000 symbols.
        Формат суворо з розділами:

        Сьогодні {day.isoformat()}.

        <b> 🟥 ЗРАДА </b>
        - пункт
        - пункт

        <b> 🟩 ПЕРЕМОГА </b>
        - пункт
        - пункт

        <b> 🟨 НЕ ВСЕ ТАК ОДНОЗНАЧНО </b>
        - пункт
        - пункт

        <b> ✅ Підсумок дня </b>
        2–3 речення

        <b> Рівень потужності - Тут надай число від 0 до 100% який відповідає рівню позитиву (позитивних нових по відношенню до негативних) і емодзі (😀 - нижче 100%, 🙂 - нижче 80%, 😐 - нижче 50%, 😧 - нижче 30%) </b>

        """

    return system, user


def strip_markdown_fence(text: str) -> str:
    """
    If the text is wrapped in ```...``` or ```markdown ... ```,
    remove those outer fences so Telegram can render it as Markdown.
    """
    if not text:
        return text

    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    lines = stripped.splitlines()

    # drop first line if it's ``` or ```markdown
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]

    # drop last line if it's ```
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def llm_summarize(day: dt.date, messages):
    system, user = build_prompt(day, messages)
    log.info("Calling OpenAI for summary (%d messages)...", len(messages))

    try:
        response = openai.ChatCompletion.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )
        summary = response.choices[0].message["content"].strip()
        summary = strip_markdown_fence(summary)
        log.info("Received summary from OpenAI (%d chars).", len(summary))
        return summary
    except Exception as e:
        log.exception("OpenAI API error: %s", e)
        return f"Failed to generate AI summary for {day.isoformat()}.\n\n" f"Error: {e}"


# ==========================
# Telegram clients
# ==========================
user_client = TelegramClient("user_session", TG_API_ID, TG_API_HASH)
bot_client = TelegramClient("bot_session", TG_API_ID, TG_API_HASH)

# We'll store numeric chat IDs of channels we care about
SCRAPED_CHAT_IDS = set()
CHAT_ID_TO_NAME = {}


async def ensure_joined_and_resolve_channels():
    """
    Using the user account:
    - join channels from CHANNELS
    - resolve their peer chat_ids (same format as event.chat_id)
    """
    global SCRAPED_CHAT_IDS, CHAT_ID_TO_NAME
    SCRAPED_CHAT_IDS = set()
    CHAT_ID_TO_NAME = {}

    for ch in CHANNELS:
        try:
            # Resolve entity
            ent = await user_client.get_entity(ch)

            # IMPORTANT: use peer id, not ent.id
            peer_id = await user_client.get_peer_id(ent)

            username = getattr(ent, "username", None)
            name = username if username else str(peer_id)
            CHAT_ID_TO_NAME[peer_id] = name

            # Try to join (if already joined, Telegram will just ignore)
            try:
                await user_client(JoinChannelRequest(ent))
                log.info("User account joined channel: %s", ch)
            except Exception as e:
                log.warning(
                    "User account could not join %s (maybe already joined): %s", ch, e
                )

            SCRAPED_CHAT_IDS.add(peer_id)
            log.info("Will scrape chat %s (peer_id=%s)", name, peer_id)

        except Exception as e:
            log.warning("User account cannot resolve %s: %s", ch, e)


# ---------- User client: catch-all, manual filter ----------
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


@user_client.on(events.NewMessage)
async def channel_message_handler(event):
    """
    Handles all new messages, but only stores those from SCRAPED_CHAT_IDS.
    """
    chat_id = event.chat_id

    if chat_id not in SCRAPED_CHAT_IDS:
        return  # not one of our target channels

    msg = event.message
    text = msg.message or ""
    date = msg.date
    chat_name = CHAT_ID_TO_NAME.get(chat_id, str(chat_id))
    msg_id = f"{chat_name}_{msg.id}"

    log.info("Got message from %s (id=%s)", chat_name, msg.id)
    save_message(msg_id, chat_name, date, text)


# ---------- Bot client: commands ----------
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


# ==========================
# Scheduler
# ==========================
async def summary_scheduler():
    log.info("Scheduler started - daily summary at %02d:00", SUMMARY_HOUR)
    last_run_for = None

    while True:
        now = dt.datetime.now()
        today = now.date()

        if now.hour == SUMMARY_HOUR and now.minute == 0:
            if last_run_for == today:
                await asyncio.sleep(60)
                continue

            log.info("Time to generate daily summary for %s", today.isoformat())
            messages = get_relevant_messages_for_day(today, max_docs=200)

            if messages:
                summary = llm_summarize(today, messages)
            else:
                summary = f"No messages to summarize for {today.isoformat()}."

            try:
                await bot_client.send_message(
                    SUMMARY_TARGET,
                    summary,
                    parse_mode="html",  # or 'markdown'
                )
                log.info("Daily summary sent to %s", SUMMARY_TARGET)
            except RPCError as e:
                log.exception("Failed to send summary to %s: %s", SUMMARY_TARGET, e)

            last_run_for = today
            await asyncio.sleep(65)
        else:
            await asyncio.sleep(30)


# ==========================
# Main
# ==========================
async def _run():
    init_db()
    log.info("Starting user & bot clients...")
    log.info("Channels to scrape (user account): %s", ", ".join(CHANNELS))
    log.info("Summary target (bot will post here): %s", SUMMARY_TARGET)

    # 1. Start user client (you will log in with your phone on first run)
    await user_client.start()
    log.info("User client started (logged in as your account).")
    await ensure_joined_and_resolve_channels()

    # 2. Start bot client
    await bot_client.start(bot_token=TG_BOT_TOKEN)
    log.info("Bot client started (logged in as bot).")

    # 3. Run both clients + scheduler
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected(),
        summary_scheduler(),
    )


def main():
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down via KeyboardInterrupt.")
