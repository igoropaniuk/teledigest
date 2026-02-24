"""
message_utils.py — helpers for sending long Telegram messages.

Telegram enforces a hard limit of 4096 UTF-16 code units per message.
The utilities here split arbitrary text on line boundaries (with a
hard-split fallback for very long lines), annotate each part with an
italic footer when more than one message is needed.
"""

from __future__ import annotations

# Telegram's hard limit for a single message is 4096 UTF-16 code units.
# We stay a bit below to have a safe margin.
TG_MAX_LEN = 4000

# Characters reserved for the longest possible footer, e.g.:
#   "\n<i>— message 99 of 99 —</i>"
_FOOTER_RESERVE = 32

# Effective chunk budget passed to split_chunks by default.
_EFFECTIVE_MAX_LEN = TG_MAX_LEN - _FOOTER_RESERVE


def utf16_len(text: str) -> int:
    """
    Return the number of UTF-16 code units in *text*.

    Python's len() counts Unicode code points, but Telegram measures
    message length in UTF-16 code units.  Characters outside the Basic
    Multilingual Plane (e.g. most emoji) encode as *two* UTF-16 code
    units (a surrogate pair) while counting as only one code point.
    Using len() would therefore undercount the real size and allow
    messages that Telegram still rejects.
    """
    return len(text.encode("utf-16-le")) // 2


def split_chunks(text: str, max_len: int = _EFFECTIVE_MAX_LEN) -> list[str]:
    """
    Split *text* into chunks whose UTF-16 length is at most *max_len*,
    preferring line boundaries so that HTML tags are never split.

    When a single line is itself longer than *max_len* (e.g. a very
    long URL or a line without any newlines) it is hard-split by
    character position as a last resort.

    Returns a list with at least one element (empty string for empty input).
    """

    def hard_split(line: str) -> list[str]:
        """Split a single overlong line into sub-chunks by character position."""
        parts: list[str] = []
        while utf16_len(line) > max_len:
            # Binary-search for the largest prefix that fits.
            lo, hi = 0, len(line)
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if utf16_len(line[:mid]) <= max_len:
                    lo = mid
                else:
                    hi = mid
            parts.append(line[:lo])
            line = line[lo:]
        parts.append(line)
        return parts

    lines = text.split("\n")
    chunks: list[str] = []
    chunk_lines: list[str] = []
    # Track UTF-16 length of the accumulated chunk, including newlines.
    chunk_utf16 = 0

    for line in lines:
        # If this single line exceeds the budget on its own, hard-split it
        # before trying to merge it into the current chunk.
        sub_lines = hard_split(line) if utf16_len(line) > max_len else [line]

        for sub in sub_lines:
            # +1 for the newline character that join() will re-add.
            # '\n' is in the BMP so it always costs exactly 1 UTF-16 unit.
            newline_cost = 1 if chunk_lines else 0
            needed = utf16_len(sub) + newline_cost

            if chunk_lines and chunk_utf16 + needed > max_len:
                chunks.append("\n".join(chunk_lines))
                chunk_lines = [sub]
                chunk_utf16 = utf16_len(sub)
            else:
                chunk_lines.append(sub)
                chunk_utf16 += needed

    if chunk_lines:
        chunks.append("\n".join(chunk_lines))

    # Always return at least one element so callers can unconditionally
    # index chunks[0] without a length check.
    return chunks or [""]


async def _send_long(text: str, sender, parse_mode: str = "html") -> None:
    """
    Core implementation: split *text* and dispatch each part via *sender*.

    *sender* must be an async callable with signature ``(text, parse_mode=...)``.

    * If the text fits in a single message it is sent as-is, with no footer.
    * If it requires multiple messages each part gets an italic footer::

          — message 1 of 3 —

    Line boundaries are preferred split points; individual lines that
    exceed the limit are hard-split by character position as a fallback.
    """
    chunks = split_chunks(text)

    if len(chunks) == 1:
        await sender(text, parse_mode=parse_mode)
        return

    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        footer = f"\n<i>— message {i} of {total} —</i>"
        await sender(chunk + footer, parse_mode=parse_mode)


async def reply_long(event, text: str, parse_mode: str = "html") -> None:
    """
    Send *text* as one or more Telegram replies.

    * If the text fits in a single message it is sent as-is, with no footer.
    * If it requires multiple messages each part gets an italic footer::

          — message 1 of 3 —

    Line boundaries are preferred split points; individual lines that
    exceed the limit are hard-split by character position as a fallback.
    """
    await _send_long(text, event.reply, parse_mode=parse_mode)


async def send_message_long(
    bot_client, target: str, text: str, parse_mode: str = "html"
) -> None:
    """
    Send *text* as one or more Telegram messages to *target*.

    * If the text fits in a single message it is sent as-is, with no footer.
    * If it requires multiple messages each part gets an italic footer::

          — message 1 of 3 —

    Line boundaries are preferred split points; individual lines that
    exceed the limit are hard-split by character position as a fallback.
    """
    await _send_long(
        text,
        lambda msg, **kw: bot_client.send_message(target, msg, **kw),
        parse_mode=parse_mode,
    )
