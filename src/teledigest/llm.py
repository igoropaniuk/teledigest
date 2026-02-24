from __future__ import annotations

import datetime as dt

from openai import OpenAI

from .config import get_config, log
from .text_sanitize import strip_markdown_fence


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


def llm_summarize(day: dt.date, messages) -> str:
    client = OpenAI(
        api_key=get_config().llm.api_key, base_url=get_config().llm.base_url
    )  # will use standard OpenAI URL if base_url is not provided
    system, user = build_prompt(day, messages)
    log.info("Calling OpenAI for summary (%d messages)...", len(messages))

    try:
        response = client.chat.completions.create(
            model=get_config().llm.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=get_config().llm.temperature,
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
