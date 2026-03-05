"""telegraph.py — Telegraph (telegra.ph) integration for posting full digests.

Converts Telegram-style HTML to the Telegraph Node format and posts pages via
the official Telegraph API using only stdlib HTTP primitives (no extra deps).

Access tokens are auto-created on first use and persisted to a JSON file in
the same directory as the database, so restarts reuse the same account.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import get_config, log

_TELEGRAPH_API = "https://api.telegra.ph"
_TOKEN_FILENAME = "telegraph_token.json"

# Telegraph supports a specific subset of HTML tags.
# Void/self-closing tags that must not push onto the children stack.
_VOID_TAGS = {"br", "img", "hr"}


# ---------------------------------------------------------------------------
# HTML → Telegraph Node conversion
# ---------------------------------------------------------------------------


class _InlineHtmlParser(HTMLParser):
    """Parse Telegram inline HTML into a Telegraph Node list.

    Telegraph Nodes are either plain strings or dicts of the form::

        {"tag": "b", "attrs": {"href": "..."}, "children": [...]}
    """

    def __init__(self) -> None:
        super().__init__()
        # _stack[0] is the root list; each open tag pushes its children list.
        self._stack: list[list[Any]] = [[]]
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node: dict[str, Any] = {"tag": tag}
        attrs_dict = {k: v for k, v in attrs if v is not None}
        if attrs_dict:
            node["attrs"] = attrs_dict
        if tag not in _VOID_TAGS:
            node["children"] = []
            self._stack[-1].append(node)
            self._stack.append(node["children"])
            self._tag_stack.append(tag)
        else:
            self._stack[-1].append(node)

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._stack.pop()
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].append(data)

    def get_nodes(self) -> list[Any]:
        return self._stack[0]


def _parse_inline(html: str) -> list[Any]:
    """Parse an HTML fragment (one paragraph) into Telegraph nodes."""
    parser = _InlineHtmlParser()
    parser.feed(html)
    return parser.get_nodes()


def _html_to_nodes(html: str) -> list[Any]:
    """Convert Telegram-style HTML to a Telegraph Node array.

    Blank lines become paragraph breaks.  Single newlines within a paragraph
    are converted to ``<br>`` nodes so line structure is preserved.
    """
    nodes: list[Any] = []
    for para in re.split(r"\n{2,}", html.strip()):
        para = para.strip()
        if not para:
            continue
        # Single newlines → <br> so the inline parser sees them as elements.
        para_html = para.replace("\n", "<br>")
        children = _parse_inline(para_html)
        if children:
            nodes.append({"tag": "p", "children": children})

    return nodes or [{"tag": "p", "children": ["(empty)"]}]


# ---------------------------------------------------------------------------
# Telegraph API helpers
# ---------------------------------------------------------------------------


def _api_post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST *payload* as JSON to a Telegraph API *method* and return ``result``."""
    url = f"{_TELEGRAPH_API}/{method}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        data: dict[str, Any] = json.loads(resp.read().decode())

    if not data.get("ok"):
        raise RuntimeError(f"Telegraph API error: {data.get('error', 'unknown')}")

    return data["result"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


def _get_or_create_token(data_dir: Path, author_name: str) -> str:
    """Return a cached Telegraph access token, creating one if needed.

    The token is persisted to *data_dir/telegraph_token.json* so it survives
    restarts and the same Telegraph account is reused.
    """
    token_file = data_dir / _TOKEN_FILENAME

    if token_file.is_file():
        try:
            saved: dict[str, Any] = json.loads(token_file.read_text())
            token = saved.get("access_token", "")
            if token:
                return str(token)
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to read Telegraph token from %s; recreating.", token_file
            )

    log.info("Creating new Telegraph account (author_name=%r)…", author_name)
    result = _api_post(
        "createAccount",
        {"short_name": author_name[:32], "author_name": author_name},
    )
    token = str(result["access_token"])

    data_dir.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps({"access_token": token}))
    log.info("Telegraph access token saved to %s", token_file)
    return token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def post_to_telegraph(title: str, html: str) -> str:
    """Post *html* content to Telegraph and return the public page URL.

    Configuration is read from the global ``AppConfig``:

    * ``cfg.telegraph.access_token`` — use this token if set; otherwise
      auto-create one and persist it next to the database file.
    * ``cfg.telegraph.author_name`` — byline shown on the Telegraph page.
    * ``cfg.telegraph.author_url``  — optional link on the byline.
    """
    cfg = get_config()
    tph = cfg.telegraph

    if tph.access_token:
        token = tph.access_token
    else:
        data_dir = cfg.storage.db_path.parent
        token = _get_or_create_token(data_dir, tph.author_name)

    nodes = _html_to_nodes(html)

    payload: dict[str, Any] = {
        "access_token": token,
        "title": title[:256],  # Telegraph max title length
        "content": nodes,
        "author_name": tph.author_name,
    }
    if tph.author_url:
        payload["author_url"] = tph.author_url

    result = _api_post("createPage", payload)
    url: str = result["url"]
    log.info("Posted digest to Telegraph: %s", url)
    return url
