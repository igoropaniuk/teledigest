from __future__ import annotations

import pytest

from teledigest import message_utils as mu

# ---------------------------------------------------------------------------
# utf16_len
# ---------------------------------------------------------------------------


def test_utf16_len_ascii():
    assert mu.utf16_len("hello") == 5


def test_utf16_len_empty():
    assert mu.utf16_len("") == 0


def test_utf16_len_emoji_counts_as_two_units():
    # 😀 is U+1F600 (outside BMP), encodes as a surrogate pair → 2 UTF-16 units
    assert mu.utf16_len("😀") == 2


def test_utf16_len_bmp_char_counts_as_one():
    # © is U+00A9, in the BMP → 1 UTF-16 unit
    assert mu.utf16_len("©") == 1


def test_utf16_len_mixed_ascii_and_emoji():
    # "hi😀" → 2 (ascii) + 2 (emoji) = 4
    assert mu.utf16_len("hi😀") == 4


def test_utf16_len_newline_is_one_unit():
    # '\n' is U+000A, in the BMP → 1 UTF-16 unit
    assert mu.utf16_len("\n") == 1


# ---------------------------------------------------------------------------
# split_chunks
# ---------------------------------------------------------------------------


def test_split_chunks_short_text_returns_single_chunk():
    text = "Hello world"
    chunks = mu.split_chunks(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_chunks_empty_string_returns_single_empty_element():
    chunks = mu.split_chunks("")
    assert chunks == [""]


def test_split_chunks_splits_on_line_boundaries():
    # Each line is exactly max_len characters; together they exceed max_len.
    line_a = "a" * 10
    line_b = "b" * 10
    text = f"{line_a}\n{line_b}"
    chunks = mu.split_chunks(text, max_len=10)
    assert len(chunks) == 2
    assert chunks[0] == line_a
    assert chunks[1] == line_b


def test_split_chunks_hard_splits_overlong_single_line():
    # A single line longer than max_len must be split by character position.
    long_line = "x" * 25
    chunks = mu.split_chunks(long_line, max_len=10)
    assert len(chunks) == 3
    for chunk in chunks:
        assert mu.utf16_len(chunk) <= 10
    assert "".join(chunks) == long_line


def test_split_chunks_every_chunk_respects_max_len():
    # Build a multi-line text and ensure no chunk exceeds max_len.
    lines = ["wordword"] * 20  # 8 chars each
    text = "\n".join(lines)
    chunks = mu.split_chunks(text, max_len=30)
    for chunk in chunks:
        assert mu.utf16_len(chunk) <= 30


def test_split_chunks_all_content_preserved():
    # Reconstructing the text from chunks must yield the original.
    lines = [f"message {i}" for i in range(50)]
    text = "\n".join(lines)
    chunks = mu.split_chunks(text, max_len=20)
    reconstructed = "\n".join(chunks)
    assert reconstructed == text


def test_split_chunks_single_line_exactly_max_len():
    line = "a" * 10
    chunks = mu.split_chunks(line, max_len=10)
    assert len(chunks) == 1
    assert chunks[0] == line


# ---------------------------------------------------------------------------
# _send_long
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_long_single_chunk_no_footer():
    sent = []

    async def sender(text, *, parse_mode="html"):
        sent.append(text)

    await mu._send_long("hello", sender)
    assert len(sent) == 1
    assert sent[0] == "hello"
    assert "<i>" not in sent[0]


@pytest.mark.asyncio
async def test_send_long_multiple_chunks_adds_footer():
    sent = []

    async def sender(text, *, parse_mode="html"):
        sent.append(text)

    # Two lines, each slightly under the default effective limit but together
    # exceeding it, so we get 2 chunks.
    line_a = "a" * 3968
    line_b = "b" * 3968
    long_text = f"{line_a}\n{line_b}"

    await mu._send_long(long_text, sender)

    assert len(sent) == 2
    assert "— message 1 of 2 —" in sent[0]
    assert "— message 2 of 2 —" in sent[1]


@pytest.mark.asyncio
async def test_send_long_passes_parse_mode():
    received = {}

    async def sender(text, *, parse_mode="html"):
        received["parse_mode"] = parse_mode

    await mu._send_long("hello", sender, parse_mode="markdown")
    assert received["parse_mode"] == "markdown"


@pytest.mark.asyncio
async def test_send_long_original_text_sent_for_single_chunk():
    """When there is only one chunk the original text is sent as-is."""
    sent = []

    async def sender(text, *, parse_mode="html"):
        sent.append(text)

    original = "Short text"
    await mu._send_long(original, sender)
    assert sent[0] is original


# ---------------------------------------------------------------------------
# reply_long
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_long_single_message():
    replied = []

    class FakeEvent:
        async def reply(self, text, *, parse_mode="html"):
            replied.append(text)

    await mu.reply_long(FakeEvent(), "short message")
    assert len(replied) == 1
    assert replied[0] == "short message"


@pytest.mark.asyncio
async def test_reply_long_multi_message():
    replied = []

    class FakeEvent:
        async def reply(self, text, *, parse_mode="html"):
            replied.append(text)

    long_text = ("a" * 3968 + "\n") + ("b" * 3968)
    await mu.reply_long(FakeEvent(), long_text)
    assert len(replied) == 2


@pytest.mark.asyncio
async def test_reply_long_passes_parse_mode():
    received_kwargs = {}

    class FakeEvent:
        async def reply(self, text, *, parse_mode="html"):
            received_kwargs["parse_mode"] = parse_mode

    await mu.reply_long(FakeEvent(), "text", parse_mode="markdown")
    assert received_kwargs["parse_mode"] == "markdown"


# ---------------------------------------------------------------------------
# send_message_long
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_long_single_message():
    calls = []

    class FakeClient:
        async def send_message(self, target, text, **kw):
            calls.append((target, text))

    await mu.send_message_long(FakeClient(), "@target", "hello")
    assert len(calls) == 1
    assert calls[0] == ("@target", "hello")


@pytest.mark.asyncio
async def test_send_message_long_multi_message():
    calls = []

    class FakeClient:
        async def send_message(self, target, text, **kw):
            calls.append((target, text))

    long_text = ("a" * 3968 + "\n") + ("b" * 3968)
    await mu.send_message_long(FakeClient(), "@chan", long_text)
    assert len(calls) == 2
    for target, _text in calls:
        assert target == "@chan"


@pytest.mark.asyncio
async def test_send_message_long_passes_parse_mode():
    received = {}

    class FakeClient:
        async def send_message(self, target, text, *, parse_mode="html"):
            received["parse_mode"] = parse_mode

    await mu.send_message_long(FakeClient(), "@chan", "hello", parse_mode="markdown")
    assert received["parse_mode"] == "markdown"
