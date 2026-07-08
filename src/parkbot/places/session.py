"""ServiceNow session management for support-places.accenture.com.

ServiceNow auth model:
  - SAML2 login via Microsoft Entra sets browser session cookies.
  - Every API call needs `X-UserToken` = the g_ck value embedded in page HTML.
  - g_ck is per-session and changes at each login — no OAuth refresh_token here.

Strategy:
  1. bootstrap.py does the one-time Playwright SAML login and saves cookies to disk.
  2. At runtime, load the cookies into an httpx session, fetch any light SN page,
     extract g_ck from the returned HTML via regex.
  3. If the page redirects to login → session expired → raise SessionExpiredError
     so the bot can prompt the user to re-bootstrap.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config

GCK_RE = re.compile(r"var\s+g_ck\s*=\s*'([a-f0-9]+)'")


class SessionExpiredError(Exception):
    """SN session cookies are stale — user must re-run places bootstrap."""


def _load_cookies() -> dict[str, str]:
    """Load cookies from disk. Keys starting with '_' are metadata, not cookies."""
    if not config.PLACES_COOKIES_FILE.exists():
        raise SessionExpiredError(
            f"Nessuna sessione SN salvata ({config.PLACES_COOKIES_FILE}). "
            "Esegui `parkbot places-bootstrap`."
        )
    with config.PLACES_COOKIES_FILE.open() as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _load_meta() -> dict:
    """Load metadata fields (keys starting with '_') from the cookies file."""
    if not config.PLACES_COOKIES_FILE.exists():
        return {}
    with config.PLACES_COOKIES_FILE.open() as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if k.startswith("_")}


def _save_cookies(cookies: dict[str, str]) -> None:
    """Save cookies to disk, preserving existing metadata or adding _bootstrapped_at."""
    meta = _load_meta() if config.PLACES_COOKIES_FILE.exists() else {}
    meta["_bootstrapped_at"] = datetime.now(timezone.utc).isoformat()
    config.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.PLACES_COOKIES_FILE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump({**meta, **cookies}, f)
    tmp.chmod(0o600)
    tmp.replace(config.PLACES_COOKIES_FILE)


def session_age_hours() -> float | None:
    """Return how many hours ago the SN session was bootstrapped, or None if unknown."""
    meta = _load_meta()
    ts = meta.get("_bootstrapped_at")
    if not ts:
        return None
    try:
        bootstrapped = datetime.fromisoformat(ts)
        return (datetime.now(timezone.utc) - bootstrapped).total_seconds() / 3600
    except Exception:
        return None


def _extract_gck(html: str) -> str | None:
    m = GCK_RE.search(html)
    return m.group(1) if m else None


class PlacesSession:
    """An authenticated httpx client with a live cookie jar and fresh g_ck.

    The underlying httpx.Client is kept alive so cookies set during the
    initial page load (SN refreshes glide_session_store on every request)
    are automatically carried into subsequent API calls.
    """

    def __init__(self, gck: str, http_client: httpx.Client) -> None:
        self._gck = gck
        self._http = http_client

    def client(self) -> httpx.Client:
        """Return the live client — caller must NOT close it between calls."""
        self._http.headers.update({
            "X-UserToken": self._gck,
            "X-Transaction-Source": config.TRANSACTION_SOURCE,
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": f"{config.PLACES_BASE}/places",
        })
        return self._http

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_session() -> PlacesSession:
    """Load saved cookies, warm up the SN session, return a ready PlacesSession.

    Uses a single persistent httpx.Client so SN's cookie refreshes (e.g.
    glide_session_store rotation) are carried forward into every API call.

    Raises SessionExpiredError if cookies are missing or the SN session has
    expired (redirect to Microsoft login detected).
    """
    cookies = _load_cookies()

    # Keep the client alive — SN sets fresh cookies on the page GET and we
    # need those same updated cookies for subsequent API requests.
    http = httpx.Client(
        cookies=cookies,
        timeout=20.0,
        follow_redirects=True,
    )

    r = http.get(f"{config.PLACES_BASE}/places/")

    # After following redirects, a live session lands on the SN portal page.
    # A dead session ends up on the Microsoft login page.
    if "microsoftonline.com" in str(r.url) or "login" in str(r.url).lower():
        http.close()
        raise SessionExpiredError(
            "Sessione SN scaduta. Esegui `parkbot places-bootstrap` "
            "(serve un tap MFA)."
        )

    gck = _extract_gck(r.text)
    if not gck:
        http.close()
        raise SessionExpiredError(
            "Impossibile estrarre g_ck dalla pagina SN. "
            "Prova a ri-eseguire `parkbot places-bootstrap`."
        )

    # Verify auth with a real API call — SN returns 200+g_ck even for anonymous
    # page loads, so the URL redirect check above is not enough. A 401 here means
    # the cookies are stale (JS would redirect in a browser, but httpx won't).
    http.headers.update({
        "X-UserToken": gck,
        "Accept": "application/json",
    })
    check = http.get(
        f"{config.PLACES_API}/reservation/list",
        params={"endIndex": 1, "startIndex": 0},
    )
    if check.status_code == 401:
        http.close()
        raise SessionExpiredError(
            "Sessione SN scaduta (API 401). Esegui `parkbot places-bootstrap` "
            "(serve un tap MFA)."
        )

    # Persist the updated cookies to disk so future runs start fresh
    updated = {c.name: c.value for c in http.cookies.jar}
    if updated:
        _save_cookies(updated)

    return PlacesSession(gck=gck, http_client=http)
