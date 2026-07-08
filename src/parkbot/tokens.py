"""Cognito token lifecycle: load refresh_token from disk, mint access_token on demand.

The portal's Cognito User Pool is a public SPA client (PKCE), so refresh exchanges
don't require a client_secret — just `grant_type=refresh_token` + the stored
refresh_token + client_id.

We persist refresh_token to disk (chmod 600). Access tokens are kept in memory only —
each process invocation mints a fresh one, which takes ~300 ms over the network.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from . import config


class TokenError(Exception):
    """Raised when token operations fail. Common causes: refresh_token revoked or
    expired (need to re-run bootstrap), Cognito reachability issues."""


@dataclass
class TokenBundle:
    access_token: str
    expires_at: float  # unix epoch
    refresh_token: str
    id_token: Optional[str] = None
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        # 60s safety margin
        return time.time() > self.expires_at - 60

    @property
    def authorization_header(self) -> str:
        return f"{self.token_type} {self.access_token}"


def _read_disk_tokens() -> dict:
    if not config.TOKEN_FILE.exists():
        raise TokenError(
            f"No saved tokens at {config.TOKEN_FILE}. Run `parkbot bootstrap` first."
        )
    with config.TOKEN_FILE.open() as f:
        return json.load(f)


def _write_disk_tokens(data: dict) -> None:
    config.ensure_dirs()
    # Write to temp + rename for atomicity
    tmp = config.TOKEN_FILE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(config.TOKEN_FILE)


def save_initial_tokens(token_response: dict) -> TokenBundle:
    """Persist the token bundle returned by Cognito's authorization_code exchange.

    `token_response` is the parsed JSON body of the /oauth2/token response — must
    contain refresh_token, access_token, expires_in.
    """
    required = {"refresh_token", "access_token", "expires_in"}
    missing = required - token_response.keys()
    if missing:
        raise TokenError(f"Cognito token response missing fields: {missing}")

    bundle = TokenBundle(
        access_token=token_response["access_token"],
        expires_at=time.time() + int(token_response["expires_in"]),
        refresh_token=token_response["refresh_token"],
        id_token=token_response.get("id_token"),
        token_type=token_response.get("token_type", "Bearer"),
    )
    _write_disk_tokens({
        "refresh_token": bundle.refresh_token,
        "saved_at": time.time(),
        # NOTE: we intentionally do NOT persist access_token — minted fresh each run.
    })
    return bundle


def get_access_token(*, http_timeout: float = 10.0) -> TokenBundle:
    """Mint a fresh access_token by exchanging the stored refresh_token.

    Cognito's User Pool /oauth2/token endpoint takes form-encoded body:
        grant_type=refresh_token
        refresh_token=<jwt>
        client_id=<public client id>
    """
    disk = _read_disk_tokens()
    refresh_token = disk.get("refresh_token")
    if not refresh_token:
        raise TokenError("Stored token file has no refresh_token. Re-bootstrap.")

    try:
        r = httpx.post(
            config.COGNITO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config.COGNITO_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=http_timeout,
        )
    except httpx.HTTPError as e:
        raise TokenError(f"Cognito token endpoint unreachable: {e}") from e

    if r.status_code != 200:
        # Cognito returns { "error": "invalid_grant" } when refresh_token is bad
        raise TokenError(
            f"Refresh failed: HTTP {r.status_code} body={r.text[:300]!r}. "
            f"If this is invalid_grant, re-run `parkbot bootstrap`."
        )

    body = r.json()
    bundle = TokenBundle(
        access_token=body["access_token"],
        expires_at=time.time() + int(body["expires_in"]),
        # Cognito User Pools without token rotation return the SAME refresh_token;
        # if rotation is enabled, body contains a new one. Prefer the response value
        # if present, fall back to the existing one.
        refresh_token=body.get("refresh_token", refresh_token),
        id_token=body.get("id_token"),
        token_type=body.get("token_type", "Bearer"),
    )

    # Persist refresh_token if it rotated
    if bundle.refresh_token != refresh_token:
        disk["refresh_token"] = bundle.refresh_token
        disk["rotated_at"] = time.time()
        _write_disk_tokens(disk)

    return bundle
