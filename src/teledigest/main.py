#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path

from .telegram_client import create_clients, start_clients, run_clients
from .scheduler import summary_scheduler
from .db import init_db
from .config import log, init_config

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="teledigest",
        description="LLM-driven Telegram digest bot that summarizes channels",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.toml (overrides default location)",
    )
    return parser.parse_args()

async def _run(config_path: Path | None) -> None:
    init_config(config_path)

    init_db()

    await create_clients()
    await start_clients()

    # Run both clients + scheduler
    await asyncio.gather(
        run_clients(),
        summary_scheduler(),
    )


def main():
    try:
        args = parse_args()
        asyncio.run(_run(args.config))
    except KeyboardInterrupt:
        log.info("Shutting down via KeyboardInterrupt.")
