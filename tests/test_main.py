from __future__ import annotations

import sys
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from teledigest.main import _run, main, parse_args

# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    with patch.object(sys, "argv", ["teledigest"]):
        args = parse_args()

    assert args.auth is False
    assert args.config is None
    assert args.debug is False


def test_parse_args_auth_flag():
    with patch.object(sys, "argv", ["teledigest", "--auth"]):
        args = parse_args()

    assert args.auth is True


def test_parse_args_config_long_form():
    with patch.object(sys, "argv", ["teledigest", "--config", "/tmp/config.toml"]):
        args = parse_args()

    assert args.config == Path("/tmp/config.toml")


def test_parse_args_debug_long_form():
    with patch.object(sys, "argv", ["teledigest", "--debug"]):
        args = parse_args()

    assert args.debug is True


def test_parse_args_debug_short_form():
    with patch.object(sys, "argv", ["teledigest", "-d"]):
        args = parse_args()

    assert args.debug is True


def test_parse_args_combined_flags():
    with patch.object(
        sys, "argv", ["teledigest", "--auth", "--debug", "--config", "my.toml"]
    ):
        args = parse_args()

    assert args.auth is True
    assert args.debug is True
    assert args.config == Path("my.toml")


# ---------------------------------------------------------------------------
# main() – return codes and error handling
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_success():
    with (
        patch("teledigest.main._run", new_callable=AsyncMock),
        patch.object(sys, "argv", ["teledigest"]),
    ):
        assert main() == 0


def test_main_returns_130_on_keyboard_interrupt():
    with (
        patch(
            "teledigest.main._run",
            new_callable=AsyncMock,
            side_effect=KeyboardInterrupt,
        ),
        patch.object(sys, "argv", ["teledigest"]),
    ):
        assert main() == 130


def test_main_returns_1_on_exception_and_prints_to_stderr(capsys):
    with (
        patch(
            "teledigest.main._run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("disk full"),
        ),
        patch.object(sys, "argv", ["teledigest"]),
    ):
        result = main()

    assert result == 1
    assert "disk full" in capsys.readouterr().err


def test_main_with_debug_flag_calls_traceback_print_exc():
    with (
        patch(
            "teledigest.main._run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("oops"),
        ),
        patch("traceback.print_exc") as mock_tb,
        patch.object(sys, "argv", ["teledigest", "--debug"]),
    ):
        result = main()

    assert result == 1
    mock_tb.assert_called_once()


# ---------------------------------------------------------------------------
# _run() – auth-only and normal execution paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_auth_only_skips_db_init_and_disconnects():
    """`_run(auth_only=True)` must skip init_db and call disconnect_clients."""
    with (
        patch("teledigest.main.init_config"),
        patch("teledigest.main.init_db") as mock_init_db,
        patch("teledigest.main.create_clients", new_callable=AsyncMock),
        patch("teledigest.main.start_clients", new_callable=AsyncMock) as mock_start,
        patch(
            "teledigest.main.disconnect_clients", new_callable=AsyncMock
        ) as mock_disconnect,
    ):
        await _run(None, auth_only=True)

    mock_init_db.assert_not_called()
    mock_start.assert_awaited_once_with(auth_only=True)
    mock_disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_normal_mode_initialises_db_and_gathers_coroutines():
    """`_run(auth_only=False)` must call init_db and pass both long-running
    coroutines to asyncio.gather."""
    gather_calls: list = []

    async def fake_gather(*coros):
        gather_calls.append(coros)
        # Close any coroutines to suppress "never awaited" warnings.
        for coro in coros:
            if hasattr(coro, "close"):
                coro.close()

    with (
        patch("teledigest.main.init_config"),
        patch("teledigest.main.init_db") as mock_init_db,
        patch("teledigest.main.create_clients", new_callable=AsyncMock),
        patch("teledigest.main.start_clients", new_callable=AsyncMock),
        patch("teledigest.main.run_clients", new_callable=AsyncMock),
        patch("teledigest.main.summary_scheduler", new_callable=AsyncMock),
        patch("asyncio.gather", new=fake_gather),
    ):
        await _run(None, auth_only=False)

    mock_init_db.assert_called_once()
    assert len(gather_calls) == 1
