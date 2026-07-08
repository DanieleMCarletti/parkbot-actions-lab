"""One-time interactive login that captures Cognito tokens via Playwright.

Run via:   parkbot bootstrap

What it does:
  1. Launches Chromium (headed, via WSLg).
  2. Navigates to the parking portal login page.
  3. You log in via Accenture SSO + MFA in the browser.
  4. While you log in, the script intercepts the /oauth2/token response on the
     Cognito hosted-UI domain and grabs the refresh_token.
  5. Saves refresh_token to ~/.local/share/parkbot/secrets/tokens.json (chmod 600).
  6. Verifies by immediately minting a fresh access_token via the refresh grant.
  7. Optional sanity check: GET /api/profilo/me.

Re-run when the refresh_token expires (~30 days, Cognito default).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from playwright.async_api import Response, async_playwright

from . import config, tokens, booking


_LOGIN_URL = f"{config.PORTAL_ORIGIN}/app/login"
_COGNITO_TOKEN_PATH = "/oauth2/token"


async def _run() -> int:
    config.ensure_dirs()
    captured: dict | None = None
    done_event = asyncio.Event()

    async def on_response(response: Response) -> None:
        nonlocal captured
        if _COGNITO_TOKEN_PATH not in response.url:
            return
        if config.COGNITO_DOMAIN not in response.url:
            return
        if response.request.method != "POST":
            return
        try:
            body = await response.json()
        except Exception as e:
            print(f"  ! /oauth2/token response not JSON: {e}", file=sys.stderr)
            return
        # Only treat as success if it has a refresh_token (authorization_code grant)
        if "refresh_token" not in body:
            return
        captured = body
        print(f"  ✓ captured token bundle (expires_in={body.get('expires_in')}s)")
        done_event.set()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(config.BOOTSTRAP_PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.on("response", lambda r: asyncio.create_task(on_response(r)))

        page = await context.new_page()
        await page.goto(_LOGIN_URL)

        print()
        print("=" * 70)
        print(" Chromium is open. Log in with Accenture SSO (+ MFA tap).")
        print(" The script will close itself once it captures the token bundle.")
        print(" If it stalls after a successful login, press Ctrl+C.")
        print("=" * 70)

        try:
            await asyncio.wait_for(done_event.wait(), timeout=300)  # 5 min
        except asyncio.TimeoutError:
            print("\n[timeout] Did not see a token exchange in 5 minutes.",
                  file=sys.stderr)
            await context.close()
            return 2

        # Give the page a moment to finish other init requests so we don't kill
        # mid-stream (no functional reason — just for cleanliness)
        await asyncio.sleep(1.0)
        await context.close()

    if not captured:
        print("[error] No token captured.", file=sys.stderr)
        return 2

    bundle = tokens.save_initial_tokens(captured)
    print(f"  ✓ saved refresh_token to {config.TOKEN_FILE}")

    # Verify the refresh works end-to-end
    print("  · verifying refresh grant…")
    fresh = tokens.get_access_token()
    if fresh.access_token == bundle.access_token:
        # Same access_token returned — Cognito reused it? Unusual but harmless.
        print("  ✓ refresh returned (same) access_token — OK")
    else:
        print("  ✓ refresh minted a NEW access_token — OK")

    # Sanity check: hit /profilo/me with the fresh token
    print("  · sanity check: GET /api/profilo/me …")
    try:
        result = booking.list_future_bookings(token=fresh)
        print(f"  ✓ API call OK. You currently have {len(result)} future booking(s).")
    except Exception as e:
        print(f"  ! API check failed: {e}", file=sys.stderr)
        print("    Bootstrap saved, but the API didn't accept the token. Investigate.",
              file=sys.stderr)
        return 1

    print()
    print("Done. You can now run e.g.:")
    print("  parkbot book 2026-06-04")
    print("  parkbot queue 2026-06-04   # for midnight cron to pick up")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
