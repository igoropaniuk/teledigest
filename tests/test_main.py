from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from teledigest.main import parse_args

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
