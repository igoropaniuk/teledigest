#!/usr/bin/env python3
# isort: skip_file
import argparse
import asyncio
import sys
import traceback
from pathlib import Path

from .config import init_config, get_config, log
from .db import init_db
from .sources_db import init_sources_table, migrate_from_config
from .scheduler import summary_scheduler
from .telegram_client import (
    create_clients,
    disconnect_clients,
    run_clients,
    start_clients,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="teledigest",
        description="LLM-driven Telegram digest bot that summarizes channels",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help=(
            "Authenticate Telegram user account, create user session, then exit. "
            "Useful for one-time setup in containers."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.toml (overrides default location)",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Show full traceback on errors",
    )
    return parser.parse_args()


async def _run(config_path: Path | None, auth_only: bool) -> None:
    init_config(config_path)

    if not auth_only:
        # The message handler writes to SQLite; initialize DB before clients can receive updates.
        init_db()
        init_sources_table()
        # Migrate channels from config to sources table (one-time, skips existing)
        cfg = get_config()
        if hasattr(cfg, "sources") and cfg.sources and cfg.sources.channels:
            channels = [
                {"url": ch.url, "country": ch.country, "name": ch.name, "language": ch.language}
                for ch in cfg.sources.channels
            ]
            digest_targets = cfg.sources.digest_targets if cfg.sources.digest_targets else {}
            migrate_from_config(channels, digest_targets)


    await create_clients()
    await start_clients(auth_only=auth_only)

    if auth_only:
        # Disconnect cleanly and exit.

        await disconnect_clients()
        log.info(
            "Authentication completed; session files should now be present. Exiting."
        )
        return

    # Run both clients + scheduler
    await asyncio.gather(
        run_clients(),
        summary_scheduler(),
    )


def main() -> int:
    args = parse_args()

    try:
        asyncio.run(_run(args.config, args.auth))
        return 0
    except KeyboardInterrupt:
        log.info("Shutting down via KeyboardInterrupt")
        return 130
    except Exception as e:
        if args.debug:
            traceback.print_exc()
        else:
            # Keep stderr clean by default (no full backtrace)
            # and provide user-friendly output
            print(f"Error: {str(e)}", file=sys.stderr)
        return 1
