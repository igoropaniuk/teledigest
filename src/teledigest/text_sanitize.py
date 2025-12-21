from __future__ import annotations

import re
import unicodedata

# Matches common URL patterns.
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# @mentions and #hashtags (kept simple on purpose).
_MENTION_HASHTAG_RE = re.compile(r"[@#]\w+", re.UNICODE)

# Collapse all whitespace to a single space.
_WS_RE = re.compile(r"\s+", re.UNICODE)


def sanitize_text(text: str) -> str:
    """Return a DB-safe version of Telegram message text.

    Removes:
      - URLs
      - @mentions and #hashtags
      - emojis and most symbols
      - control/invisible characters

    Keeps:
      - Unicode letters and numbers (all scripts)
      - punctuation
      - regular whitespace
    """

    if not text:
        return ""

    # Normalize to reduce odd unicode variants (e.g. full-width forms)
    text = unicodedata.normalize("NFKC", text)

    # Strip URLs early (they often contain lots of punctuation/symbols)
    text = _URL_RE.sub("", text)

    # Strip mentions / hashtags
    text = _MENTION_HASHTAG_RE.sub("", text)

    cleaned_chars: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)

        # Drop control characters, surrogate codepoints, etc.
        if cat.startswith("C"):
            continue

        # Keep spaces.
        if cat == "Zs":
            cleaned_chars.append(ch)
            continue

        # Keep letters, numbers, and punctuation.
        if cat.startswith(("L", "N", "P")):
            cleaned_chars.append(ch)
            continue

        # Everything else is "symbol" territory (emoji, pictographs, math symbols, etc.)
        # and is removed by design.

    out = "".join(cleaned_chars)
    out = _WS_RE.sub(" ", out).strip()
    return out


def strip_markdown_fence(text: str) -> str:
    """
    If the text is wrapped in ```...``` or ```markdown ... ```,
    remove those outer fences so Telegram can render it as Markdown.
    """
    if not text:
        return text

    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    lines = stripped.splitlines()

    # drop first line if it's ``` or ```markdown
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]

    # drop last line if it's ```
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()
