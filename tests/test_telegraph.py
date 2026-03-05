"""Tests for the telegraph module — HTML conversion, token management, and API posting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teledigest import config as cfg
from teledigest import telegraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_config(
    *,
    author_name: str = "TeleDigest",
    author_url: str = "",
    access_token: str | None = None,
    db_path: str = "data/messages.db",
) -> cfg.AppConfig:
    return cfg.AppConfig(
        telegram=cfg.TelegramConfig(
            api_id=1, api_hash="h", bot_token="t", sessions_dir=Path("data")
        ),
        bot=cfg.BotConfig(channels=["@c"], summary_target="@d"),
        llm=cfg.LLMConfig(
            model="gpt-4.1",
            api_key="sk-test",
            system_prompt="sys",
            user_prompt="usr {DAY} {MESSAGES}",
            system_brief_prompt="sys_brief",
            user_brief_prompt="usr_brief {DIGEST}",
        ),
        storage=cfg.StorageConfig(rag_keywords=[], db_path=Path(db_path)),
        logging=cfg.LoggingConfig(level="WARNING"),
        telegraph=cfg.TelegraphConfig(
            author_name=author_name,
            author_url=author_url,
            access_token=access_token,
        ),
    )


def _fake_urlopen(response_dict: dict):
    """Return a context-manager mock whose read() yields the given dict as JSON."""
    body = json.dumps(response_dict).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


# ---------------------------------------------------------------------------
# _html_to_nodes — paragraph splitting
# ---------------------------------------------------------------------------


def test_html_to_nodes_single_plain_paragraph():
    nodes = telegraph._html_to_nodes("Hello world")

    assert len(nodes) == 1
    assert nodes[0]["tag"] == "p"
    assert nodes[0]["children"] == ["Hello world"]


def test_html_to_nodes_multiple_paragraphs_split_on_blank_line():
    nodes = telegraph._html_to_nodes("First\n\nSecond")

    assert len(nodes) == 2
    assert nodes[0]["children"] == ["First"]
    assert nodes[1]["children"] == ["Second"]


def test_html_to_nodes_single_newline_becomes_br():
    nodes = telegraph._html_to_nodes("Line one\nLine two")

    assert len(nodes) == 1
    children = nodes[0]["children"]
    # children should contain text and a <br> node between the lines
    tags = [c["tag"] for c in children if isinstance(c, dict)]
    assert "br" in tags


def test_html_to_nodes_empty_string_returns_fallback():
    nodes = telegraph._html_to_nodes("")

    assert len(nodes) == 1
    assert nodes[0]["tag"] == "p"


def test_html_to_nodes_whitespace_only_returns_fallback():
    nodes = telegraph._html_to_nodes("   \n\n   ")

    assert len(nodes) == 1
    assert nodes[0]["tag"] == "p"


# ---------------------------------------------------------------------------
# _html_to_nodes — inline tag conversion
# ---------------------------------------------------------------------------


def test_html_to_nodes_bold_tag():
    nodes = telegraph._html_to_nodes("<b>Bold text</b>")

    children = nodes[0]["children"]
    assert any(
        isinstance(c, dict) and c["tag"] == "b" and c["children"] == ["Bold text"]
        for c in children
    )


def test_html_to_nodes_italic_tag():
    nodes = telegraph._html_to_nodes("<i>Italic</i>")

    children = nodes[0]["children"]
    assert any(isinstance(c, dict) and c["tag"] == "i" for c in children)


def test_html_to_nodes_code_tag():
    nodes = telegraph._html_to_nodes("<code>snippet</code>")

    children = nodes[0]["children"]
    assert any(isinstance(c, dict) and c["tag"] == "code" for c in children)


def test_html_to_nodes_anchor_preserves_href():
    nodes = telegraph._html_to_nodes('<a href="https://example.com">link</a>')

    children = nodes[0]["children"]
    anchor = next(c for c in children if isinstance(c, dict) and c["tag"] == "a")
    assert anchor["attrs"]["href"] == "https://example.com"
    assert anchor["children"] == ["link"]


def test_html_to_nodes_mixed_inline_content():
    nodes = telegraph._html_to_nodes("Hello <b>world</b> and <i>more</i>")

    children = nodes[0]["children"]
    texts = [c for c in children if isinstance(c, str)]
    tags = [c["tag"] for c in children if isinstance(c, dict)]
    assert "Hello " in texts
    assert "b" in tags
    assert "i" in tags


def test_html_to_nodes_br_is_void_no_children():
    nodes = telegraph._html_to_nodes("before<br>after")

    children = nodes[0]["children"]
    br_nodes = [c for c in children if isinstance(c, dict) and c["tag"] == "br"]
    assert br_nodes
    # br must NOT have a children key (it is a void element)
    assert "children" not in br_nodes[0]


# ---------------------------------------------------------------------------
# _get_or_create_token
# ---------------------------------------------------------------------------


def test_get_or_create_token_reads_existing_file(tmp_path: Path):
    token_file = tmp_path / telegraph._TOKEN_FILENAME
    token_file.write_text(json.dumps({"access_token": "cached_token"}))

    result = telegraph._get_or_create_token(tmp_path, "Bot")

    assert result == "cached_token"


def test_get_or_create_token_creates_new_when_file_missing(tmp_path: Path):
    api_response = {"ok": True, "result": {"access_token": "new_token"}}

    with patch("urllib.request.urlopen", return_value=_fake_urlopen(api_response)):
        result = telegraph._get_or_create_token(tmp_path, "Bot")

    assert result == "new_token"
    # token must be persisted so subsequent calls reuse it
    saved = json.loads((tmp_path / telegraph._TOKEN_FILENAME).read_text())
    assert saved["access_token"] == "new_token"


def test_get_or_create_token_recreates_when_file_is_corrupt(tmp_path: Path):
    (tmp_path / telegraph._TOKEN_FILENAME).write_text("NOT JSON {{}")
    api_response = {"ok": True, "result": {"access_token": "fresh_token"}}

    with patch("urllib.request.urlopen", return_value=_fake_urlopen(api_response)):
        result = telegraph._get_or_create_token(tmp_path, "Bot")

    assert result == "fresh_token"


def test_get_or_create_token_recreates_when_token_key_missing(tmp_path: Path):
    (tmp_path / telegraph._TOKEN_FILENAME).write_text(json.dumps({"other": "data"}))
    api_response = {"ok": True, "result": {"access_token": "new_token2"}}

    with patch("urllib.request.urlopen", return_value=_fake_urlopen(api_response)):
        result = telegraph._get_or_create_token(tmp_path, "Bot")

    assert result == "new_token2"


# ---------------------------------------------------------------------------
# _api_post
# ---------------------------------------------------------------------------


def test_api_post_raises_on_error_response():
    error_resp = {"ok": False, "error": "FLOOD_WAIT"}

    with patch("urllib.request.urlopen", return_value=_fake_urlopen(error_resp)):
        with pytest.raises(RuntimeError, match="FLOOD_WAIT"):
            telegraph._api_post("createPage", {"access_token": "t"})


# ---------------------------------------------------------------------------
# post_to_telegraph
# ---------------------------------------------------------------------------


def test_post_to_telegraph_uses_token_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(access_token="cfg_token", db_path=str(tmp_path / "db"))
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_page_resp = {
        "ok": True,
        "result": {"url": "https://telegra.ph/test-01-01"},
    }

    captured: list[bytes] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.data)
        return _fake_urlopen(create_page_resp)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url = telegraph.post_to_telegraph(title="Test", html="<b>Hello</b>")

    assert url == "https://telegra.ph/test-01-01"
    # The posted payload must include the configured token
    payload = json.loads(captured[0].decode())
    assert payload["access_token"] == "cfg_token"


def test_post_to_telegraph_auto_creates_token_when_not_in_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(access_token=None, db_path=str(tmp_path / "messages.db"))
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_account_resp = {"ok": True, "result": {"access_token": "auto_token"}}
    create_page_resp = {
        "ok": True,
        "result": {"url": "https://telegra.ph/auto-01-01"},
    }
    responses = [create_account_resp, create_page_resp]
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return _fake_urlopen(resp)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url = telegraph.post_to_telegraph(title="Auto", html="text")

    assert url == "https://telegra.ph/auto-01-01"


def test_post_to_telegraph_includes_author_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(
        author_name="My Digest Bot",
        access_token="tok",
        db_path=str(tmp_path / "db"),
    )
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_page_resp = {
        "ok": True,
        "result": {"url": "https://telegra.ph/x"},
    }
    captured: list[bytes] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.data)
        return _fake_urlopen(create_page_resp)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        telegraph.post_to_telegraph(title="T", html="text")

    payload = json.loads(captured[0].decode())
    assert payload["author_name"] == "My Digest Bot"


def test_post_to_telegraph_title_truncated_to_256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(access_token="tok", db_path=str(tmp_path / "db"))
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_page_resp = {"ok": True, "result": {"url": "https://telegra.ph/x"}}
    captured: list[bytes] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.data)
        return _fake_urlopen(create_page_resp)

    long_title = "T" * 300
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        telegraph.post_to_telegraph(title=long_title, html="text")

    payload = json.loads(captured[0].decode())
    assert len(payload["title"]) == 256


def test_post_to_telegraph_omits_author_url_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(
        access_token="tok", author_url="", db_path=str(tmp_path / "db")
    )
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_page_resp = {"ok": True, "result": {"url": "https://telegra.ph/x"}}
    captured: list[bytes] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.data)
        return _fake_urlopen(create_page_resp)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        telegraph.post_to_telegraph(title="T", html="text")

    payload = json.loads(captured[0].decode())
    assert "author_url" not in payload


def test_post_to_telegraph_includes_author_url_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app_cfg = _make_app_config(
        access_token="tok",
        author_url="https://example.com",
        db_path=str(tmp_path / "db"),
    )
    monkeypatch.setattr(cfg, "_CONFIG", app_cfg)

    create_page_resp = {"ok": True, "result": {"url": "https://telegra.ph/x"}}

    def fake_urlopen(req, timeout=None):
        return _fake_urlopen(create_page_resp)

    captured: list[bytes] = []

    def capturing_urlopen(req, timeout=None):
        captured.append(req.data)
        return _fake_urlopen(create_page_resp)

    with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
        telegraph.post_to_telegraph(title="T", html="text")

    payload = json.loads(captured[0].decode())
    assert payload["author_url"] == "https://example.com"
