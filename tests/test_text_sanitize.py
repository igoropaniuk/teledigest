from __future__ import annotations

from teledigest import text_sanitize as ts


def test_sanitize_text_removes_urls_mentions_hashtags_and_emojis() -> None:
    raw = (
        "🔥 Breaking! 🚨 New release v1.2.3 → https://github.com/foo/bar\n"
        "Follow @teledigest #python 🤖"
    )

    out = ts.sanitize_text(raw)

    # No URLs
    assert "http" not in out
    assert "github.com" not in out

    # No mentions / hashtags
    assert "@" not in out
    assert "#" not in out

    # Still keeps meaningful text
    assert "Breaking" in out
    assert "New release v1.2.3" in out


def test_strip_markdown_fence_no_fence_returns_original() -> None:
    raw = "Hello **world**"
    assert ts.strip_markdown_fence(raw) == raw


def test_strip_markdown_fence_triple_backticks() -> None:
    raw = "```\nHello **world**\n```"
    assert ts.strip_markdown_fence(raw) == "Hello **world**"


def test_strip_markdown_fence_markdown_language_hint() -> None:
    raw = "```markdown\n# Title\n\nSome *text*\n```"
    assert ts.strip_markdown_fence(raw) == "# Title\n\nSome *text*"


def test_strip_markdown_fence_trims_outer_whitespace() -> None:
    raw = " \n  ```\nHello\n```\n  "
    assert ts.strip_markdown_fence(raw) == "Hello"


def test_strip_markdown_fence_missing_closing_fence() -> None:
    # Current behavior: if it starts with ``` it strips the first fence line,
    # and only strips the last line if it is exactly ```
    raw = "```\nHello\nWorld\n"
    assert ts.strip_markdown_fence(raw) == "Hello\nWorld"
