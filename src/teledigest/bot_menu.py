"""
bot_menu.py — Inline keyboard UI and step-by-step dialogs.

Handles: main menu, source management (add country → add channels → set digest target),
status, and other interactive flows.
"""

from __future__ import annotations

from telethon import Button

from .sources_db import (
    COUNTRY_NAMES,
    add_source,
    get_active_countries,
    get_active_sources,
    get_digest_target,
    remove_source,
    resolve_country,
    set_digest_target,
)
from .config import log


# ---------------------------------------------------------------------------
# Conversation state per user (in-memory, resets on restart — fine for admin)
# ---------------------------------------------------------------------------

class ConversationState:
    """Tracks multi-step dialog state for a user."""
    __slots__ = ("step", "country_code", "country_name", "channels_added")

    def __init__(self) -> None:
        self.step: str = ""          # current step name
        self.country_code: str = ""
        self.country_name: str = ""
        self.channels_added: int = 0

    def reset(self) -> None:
        self.step = ""
        self.country_code = ""
        self.country_name = ""
        self.channels_added = 0


# user_id → state
_conversations: dict[int, ConversationState] = {}


def get_conv(user_id: int) -> ConversationState:
    if user_id not in _conversations:
        _conversations[user_id] = ConversationState()
    return _conversations[user_id]


def clear_conv(user_id: int) -> None:
    if user_id in _conversations:
        _conversations[user_id].reset()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu_keyboard():
    """Main menu inline keyboard."""
    return [
        [
            Button.inline("📡 Источники", b"menu:sources"),
            Button.inline("📊 Статус", b"menu:status"),
        ],
        [
            Button.inline("📰 Дайджест", b"menu:digest"),
            Button.inline("🧠 МОЗГ инфо", b"menu:brain"),
        ],
    ]


def sources_menu_keyboard():
    """Sources management keyboard."""
    return [
        [
            Button.inline("➕ Добавить страну", b"src:add"),
            Button.inline("📋 Список", b"src:list"),
        ],
        [Button.inline("◀️ Назад", b"menu:main")],
    ]


def confirm_country_keyboard(code: str):
    """Confirm country selection."""
    return [
        [
            Button.inline("✅ Да", f"src:confirm:{code}".encode()),
            Button.inline("❌ Нет, другая", b"src:add"),
        ],
    ]


def after_channel_keyboard():
    """After adding a channel — add more or finish."""
    return [
        [
            Button.inline("➕ Ещё канал", b"src:more_channel"),
            Button.inline("✅ Готово", b"src:set_digest"),
        ],
    ]


def skip_digest_keyboard():
    """Skip digest target or go back."""
    return [
        [
            Button.inline("⏩ Пропустить", b"src:skip_digest"),
            Button.inline("◀️ Назад", b"menu:sources"),
        ],
    ]


def back_to_main_keyboard():
    return [[Button.inline("◀️ Меню", b"menu:main")]]


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

async def handle_callback(event) -> None:
    """Route inline button callbacks."""
    data = event.data.decode("utf-8")

    if data == "menu:main":
        await event.edit("Главное меню:", buttons=main_menu_keyboard())

    elif data == "menu:sources":
        await event.edit("📡 Управление источниками:", buttons=sources_menu_keyboard())

    elif data == "menu:status":
        await _show_status(event)

    elif data == "menu:digest":
        await _trigger_digest(event)

    elif data == "menu:brain":
        await _show_brain_info(event)

    elif data == "src:add":
        await _start_add_country(event)

    elif data.startswith("src:confirm:"):
        code = data.split(":")[2]
        await _confirm_country(event, code)

    elif data == "src:list":
        await _list_sources(event)

    elif data == "src:more_channel":
        conv = get_conv(event.sender_id)
        conv.step = "await_channel"
        await event.edit(
            f"Отправь ссылку на канал/чат для {conv.country_name}:",
            buttons=[[Button.inline("◀️ Назад", b"menu:sources")]],
        )

    elif data == "src:set_digest":
        await _ask_digest_target(event)

    elif data == "src:skip_digest":
        await _finish_add(event)

    await event.answer()


# ---------------------------------------------------------------------------
# Dialog steps
# ---------------------------------------------------------------------------

async def _start_add_country(event) -> None:
    """Step 1: Ask for country name."""
    conv = get_conv(event.sender_id)
    conv.reset()
    conv.step = "await_country"
    await event.edit(
        "🌍 Напиши название страны\n"
        "(например: Турция, Таиланд, Португалия, Сербия...)\n\n"
        "Можно писать как угодно — пойму.",
        buttons=[[Button.inline("◀️ Отмена", b"menu:sources")]],
    )


async def _confirm_country(event, code: str) -> None:
    """Step 2: Country confirmed, ask for channel."""
    conv = get_conv(event.sender_id)
    name = COUNTRY_NAMES.get(code, code.upper())
    conv.step = "await_channel"
    conv.country_code = code
    conv.country_name = name
    conv.channels_added = 0
    await event.edit(
        f"{name} — отправь ссылку на канал или чат\n"
        f"(например: @turkey_chat или https://t.me/+AbCdEf123)",
        buttons=[[Button.inline("◀️ Отмена", b"menu:sources")]],
    )


async def _ask_digest_target(event) -> None:
    """Step 4: Ask where to post digests."""
    conv = get_conv(event.sender_id)
    existing = get_digest_target(conv.country_code)
    if existing:
        await _finish_add(event)
        return

    conv.step = "await_digest_target"
    await event.edit(
        f"📰 Куда постить дайджест для {conv.country_name}?\n"
        "Отправь @username канала или ссылку.\n\n"
        "Бот должен быть админом этого канала.",
        buttons=skip_digest_keyboard(),
    )


async def _finish_add(event) -> None:
    """Finish: show summary."""
    conv = get_conv(event.sender_id)
    sources = get_active_sources(conv.country_code)
    digest = get_digest_target(conv.country_code)

    lines = [f"✅ {conv.country_name} настроена!\n"]
    lines.append(f"Каналов: {len(sources)}")
    for s in sources:
        lines.append(f"  • {s['name'] or s['url']}")
    if digest:
        lines.append(f"\nДайджест → {digest}")
    else:
        lines.append("\nДайджест: не настроен")
    lines.append("\n⚡ Парсинг начнётся автоматически при следующем запуске.")

    clear_conv(event.sender_id)
    await event.edit("\n".join(lines), buttons=back_to_main_keyboard())


async def _list_sources(event) -> None:
    """Show all active sources grouped by country."""
    countries = get_active_countries()
    if not countries:
        await event.edit("Нет активных источников.", buttons=sources_menu_keyboard())
        return

    lines = ["📡 <b>Активные источники:</b>\n"]
    for code in countries:
        name = COUNTRY_NAMES.get(code, code.upper())
        sources = get_active_sources(code)
        digest = get_digest_target(code)
        lines.append(f"<b>{name}</b>")
        for s in sources:
            lines.append(f"  • {s['name'] or s['url']}")
        if digest:
            lines.append(f"  📰 → {digest}")
        lines.append("")

    await event.edit("\n".join(lines), buttons=sources_menu_keyboard(), parse_mode="html")


async def _show_status(event) -> None:
    """Show bot status."""
    from .db import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        msg_count = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        kb_count = cur.execute("SELECT COUNT(*) FROM knowledge WHERE is_outdated=0").fetchone()[0]

    countries = get_active_countries()
    total_sources = len(get_active_sources())

    lines = [
        "📊 <b>Статус бота:</b>\n",
        f"💬 Сообщений: {msg_count:,}",
        f"🧠 Записей в базе: {kb_count:,}",
        f"🌍 Стран: {len(countries)} ({', '.join(countries)})",
        f"📡 Источников: {total_sources}",
    ]
    await event.edit("\n".join(lines), buttons=back_to_main_keyboard(), parse_mode="html")


async def _trigger_digest(event) -> None:
    """Quick digest trigger placeholder."""
    await event.edit(
        "📰 Используй /today для генерации дайджеста.",
        buttons=back_to_main_keyboard(),
    )


async def _show_brain_info(event) -> None:
    """МОЗГ info."""
    from .db import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        kb_count = cur.execute("SELECT COUNT(*) FROM knowledge WHERE is_outdated=0").fetchone()[0]
        cats = cur.execute(
            "SELECT category, COUNT(*) FROM knowledge WHERE is_outdated=0 GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall()

    lines = [
        "🧠 <b>МОЗГ — база знаний:</b>\n",
        f"Всего записей: {kb_count:,}\n",
        "<b>По категориям:</b>",
    ]
    for cat, cnt in cats:
        lines.append(f"  • {cat}: {cnt}")
    lines.append("\nЧтобы спросить, напиши в чат упоминая бота.")

    await event.edit("\n".join(lines), buttons=back_to_main_keyboard(), parse_mode="html")


# ---------------------------------------------------------------------------
# Text message handler for multi-step dialogs
# ---------------------------------------------------------------------------

async def handle_text_in_conversation(event) -> bool:
    """
    Handle text input during a multi-step dialog.

    Returns True if message was consumed by dialog, False if not in dialog.
    """
    user_id = event.sender_id
    conv = get_conv(user_id)

    if not conv.step:
        return False

    text = (event.raw_text or "").strip()
    if not text:
        return False

    # Step: waiting for country name
    if conv.step == "await_country":
        result = resolve_country(text)
        if result:
            code, name = result
            await event.reply(
                f"{name} — верно?",
                buttons=confirm_country_keyboard(code),
            )
        else:
            await event.reply(
                f"❓ Не знаю страну \"{text}\".\n"
                "Попробуй полное название на русском (Турция, Таиланд, Сербия...)",
                buttons=[[Button.inline("◀️ Отмена", b"menu:sources")]],
            )
        return True

    # Step: waiting for channel URL
    if conv.step == "await_channel":
        url = text.strip()
        if not (url.startswith("@") or url.startswith("https://t.me/") or url.startswith("http://t.me/")):
            await event.reply(
                "❌ Не похоже на ссылку. Отправь @username или https://t.me/...",
            )
            return True

        # Try to add
        sid = add_source(conv.country_code, url, name=url)
        if sid:
            conv.channels_added += 1
            await event.reply(
                f"✅ Канал добавлен: {url}\n"
                f"(всего для {conv.country_name}: {conv.channels_added})",
                buttons=after_channel_keyboard(),
            )
        else:
            await event.reply(
                f"⚠️ Этот канал уже добавлен для {conv.country_name}.",
                buttons=after_channel_keyboard(),
            )
        return True

    # Step: waiting for digest target
    if conv.step == "await_digest_target":
        target = text.strip()
        if not target.startswith("@"):
            target = f"@{target}"
        set_digest_target(conv.country_code, target)
        await _finish_add_from_reply(event)
        return True

    return False


async def _finish_add_from_reply(event) -> None:
    """Finish flow from a reply (not callback edit)."""
    conv = get_conv(event.sender_id)
    sources = get_active_sources(conv.country_code)
    digest = get_digest_target(conv.country_code)

    lines = [f"✅ {conv.country_name} настроена!\n"]
    lines.append(f"Каналов: {len(sources)}")
    for s in sources:
        lines.append(f"  • {s['name'] or s['url']}")
    if digest:
        lines.append(f"\nДайджест → {digest}")
    lines.append("\n⚡ Парсинг начнётся автоматически при следующем запуске.")

    clear_conv(event.sender_id)
    await event.reply("\n".join(lines), buttons=back_to_main_keyboard())
