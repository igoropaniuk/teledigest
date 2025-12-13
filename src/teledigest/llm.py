from __future__ import annotations

import datetime as dt

from openai import OpenAI

from .config import get_config, log


def build_prompt(day: dt.date, messages):
    if not messages:
        return (
            "You are a helpful assistant.",
            f"No messages to summarize for {day.isoformat()}.",
        )

    lines = []
    max_items = 500
    max_chars_per_msg = 500

    for channel, text in messages[:max_items]:
        t = " ".join(text.split())
        if not t:
            continue
        if len(t) > max_chars_per_msg:
            t = t[:max_chars_per_msg] + " ..."
        lines.append(f"[{channel}] {t}")

    corpus = "\n".join(lines)

    cfg = get_config()

    system = cfg.llm.system_prompt
    user = cfg.llm.user_prompt.format(
        DAY=day.isoformat(),
        MESSAGES=corpus,
        TIMEZONE=cfg.bot.time_zone,
    )

    return system, user


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


def llm_summarize(day: dt.date, messages) -> str:
    client = OpenAI(api_key=get_config().llm.api_key)

    system, user = build_prompt(day, messages)
    log.info("Calling OpenAI for summary (%d messages)...", len(messages))

    try:
        response = client.chat.completions.create(
            model=get_config().llm.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )

        content = response.choices[0].message.content
        assert content is not None
        summary = content.strip()
        summary = strip_markdown_fence(summary)

        log.info("Received summary from OpenAI (%d chars).", len(summary))
        return summary

    except Exception as e:
        log.exception("OpenAI API error: %s", e)
        return f"Failed to generate AI summary for {day.isoformat()}.\n\n" f"Error: {e}"
