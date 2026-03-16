from __future__ import annotations

import discord


def escape_display_text(value: object, fallback: str = "Unbekannt") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    return discord.utils.escape_markdown(text, as_needed=False)


def safe_display_name(user: object, fallback: str = "Unbekannt") -> str:
    if isinstance(user, str):
        return escape_display_text(user, fallback=fallback)
    display_name = getattr(user, "display_name", None)
    if display_name:
        return escape_display_text(display_name, fallback=fallback)
    username = getattr(user, "name", None)
    if username:
        return escape_display_text(username, fallback=fallback)
    return escape_display_text(fallback, fallback=fallback)


def safe_user_option_label(user: object, *, prefix: str = "", fallback: str = "Unbekannt", max_len: int = 100) -> str:
    base = safe_display_name(user, fallback=fallback)
    label = f"{prefix}{base}" if prefix else base
    return label[:max_len]


def safe_thread_name(*parts: object, fallback: str = "Thread", max_len: int = 100) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        cleaned = [fallback]
    name = " ".join(cleaned)
    return name[:max_len]
