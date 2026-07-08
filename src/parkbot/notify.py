"""Telegram notifications — plain synchronous sendMessage via httpx.

Used by both the `fire` cron job (to report booking results) and anywhere else
that wants to ping the owner. Kept dependency-light (no async framework) so it's
safe to call from a oneshot systemd service.

Config lives in ~/.local/share/parkbot/secrets/telegram.json:
    {"bot_token": "123:abc", "allowed_chat_id": 12345678}
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from . import config


def _load_cfg() -> Optional[dict]:
    if not config.TELEGRAM_CONFIG_FILE.exists():
        return None
    try:
        with config.TELEGRAM_CONFIG_FILE.open() as f:
            return json.load(f)
    except Exception:
        return None


def is_configured() -> bool:
    cfg = _load_cfg()
    return bool(cfg and cfg.get("bot_token") and cfg.get("allowed_chat_id"))


def notify(text: str, *, timeout: float = 10.0) -> bool:
    """Send a Telegram message to the registered owner chat.

    Returns True on success, False if not configured or on any error. Never
    raises — notification failure must not break a booking.
    """
    cfg = _load_cfg()
    if not cfg or not cfg.get("bot_token") or not cfg.get("allowed_chat_id"):
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
            json={
                "chat_id": cfg["allowed_chat_id"],
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False
