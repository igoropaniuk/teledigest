#!/usr/bin/env python3
import asyncio

from .telegram_client import start_clients, run_clients
from .scheduler import summary_scheduler
from .db import init_db
from .config import log


async def _run():
    init_db()

    await start_clients()

    # Run both clients + scheduler
    await asyncio.gather(
        run_clients(),
        summary_scheduler(),
    )


def main():
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down via KeyboardInterrupt.")
