import asyncio
import datetime as dt
from telethon.errors import RPCError

from .config import get_config, log
from .db import get_relevant_messages_last_24h
from .llm import llm_summarize
from .telegram_client import bot_client


async def summary_scheduler():
    cfg = get_config()
    summary_target = cfg.bot.summary_target
    summary_hour = cfg.bot.summary_hour
    log.info("Summary target channel (bot will post here): %s", summary_target)
    log.info("Scheduler started - daily summary at %02d:00", summary_hour)
    last_run_for = None

    while True:
        now = dt.datetime.now()
        today = now.date()

        if now.hour == summary_hour and now.minute == 0:
            if last_run_for == today:
                await asyncio.sleep(60)
                continue

            log.info(
                "Time to generate rolling 24h summary ending %s (labelled as %s)",
                now.isoformat(), today.isoformat()
            )
            messages = get_relevant_messages_last_24h(max_docs=200)

            if messages:
                summary = llm_summarize(today, messages)
            else:
                summary = f"No messages to summarize for the last 24 hours (labelled as {today.isoformat()})."

            try:
                await bot_client.send_message(
                    summary_target,
                    summary,
                    parse_mode="html",
                )
                log.info("Daily summary sent to %s", summary_target)
            except RPCError as e:
                log.exception("Failed to send summary to %s: %s", summary_target, e)

            last_run_for = today
            await asyncio.sleep(65)
        else:
            await asyncio.sleep(30)
