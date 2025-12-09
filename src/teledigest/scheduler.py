import asyncio
import datetime as dt
from telethon.errors import RPCError

from .config import SUMMARY_TARGET, SUMMARY_HOUR, log
from .db import get_relevant_messages_for_day
from .llm import llm_summarize
from .telegram_client import bot_client


async def summary_scheduler():
    log.info("Summary target channel (bot will post here): %s", SUMMARY_TARGET)
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
