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
