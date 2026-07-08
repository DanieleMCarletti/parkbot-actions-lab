"""One-time interactive login for support-places.accenture.com.

Opens Firefox via Playwright (using the recon profile that has SAML tokens cached),
waits until SN signals a fully-authenticated user session, then saves cookies to disk.

Run interactively:  parkbot places-bootstrap
Silent auto-refresh: called automatically by keep-alive when SN session expires.
"""
from __future__ import annotations

import time

from . import config

TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes
# Microsoft Conditional Access blocks headless browsers for SAML SSO.
# Silent refresh uses headed Firefox on a private Xvfb virtual display.
SILENT_TIMEOUT_MS = 90 * 1000  # 90 s


def _save_cookies(cookies: dict) -> None:
    from .session import _save_cookies as _sc
    _sc(cookies)


def _run_browser(*, headless: bool, timeout_ms: int) -> dict | None:
    """Launch Firefox, wait for SN auth, return saved cookies dict or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    # Firefox profile — separate from any Chromium profile (incompatible format)
    profile = config.STATE_DIR / "places-recon-profile-firefox"
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.firefox.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
        )
        page = context.new_page()
        try:
            page.goto(f"{config.PLACES_BASE}/places/", timeout=timeout_ms)

            # Poll for glide_session_store — only set after a fully authenticated
            # SN session. We use a single long loop that covers both the fast
            # (silent SSO) and slow (user must complete MFA) paths.
            # Do NOT use wait_for_url — the initial navigation already lands on
            # support-places.accenture.com before the JS redirect to Microsoft,
            # so that check fires immediately and gives a false "ready" signal.
            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline:
                jar = {c["name"]: c["value"] for c in context.cookies()}
                if jar.get("glide_session_store"):
                    break
                time.sleep(2)
            else:
                context.close()
                return None

            cookies_list = context.cookies()
        except Exception:
            context.close()
            return None

        context.close()

    return {
        c["name"]: c["value"] for c in cookies_list
        if "accenture.com" in c.get("domain", "")
    }


def silent_refresh() -> bool:
    """Headed Firefox SAML re-auth on a private Xvfb virtual display — fully invisible.

    Runs headed Firefox on a virtual framebuffer (Xvfb). Xvfb is completely separate
    from the WSLg display — no Windows windows, no flicker, no user interaction.

    Returns True if a fresh authenticated SN session was obtained and saved.
    Does NOT prompt the user — if MFA is needed this will return False.
    """
    import os
    import shutil
    import subprocess

    if not shutil.which("Xvfb"):
        return False

    # Find a free X display slot (skip :0 which is WSLg)
    disp_num = 20
    while disp_num < 30:
        if not os.path.exists(f"/tmp/.X{disp_num}-lock"):
            break
        disp_num += 1
    else:
        return False

    xvfb_proc = None
    cookies = None
    old_display = os.environ.get("DISPLAY")
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", f":{disp_num}", "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)  # let Xvfb initialise
        if xvfb_proc.poll() is not None:
            return False  # Xvfb failed to start

        os.environ["DISPLAY"] = f":{disp_num}"
        cookies = _run_browser(headless=False, timeout_ms=SILENT_TIMEOUT_MS)
    finally:
        if old_display is not None:
            os.environ["DISPLAY"] = old_display
        else:
            os.environ.pop("DISPLAY", None)
        if xvfb_proc is not None:
            xvfb_proc.terminate()
            try:
                xvfb_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb_proc.kill()

    if not cookies or "glide_session_store" not in cookies:
        return False
    _save_cookies(cookies)
    return True


def main() -> int:
    config.SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 65)
    print("  Places bootstrap — login a support-places.accenture.com")
    print()
    print("  Si apre Firefox. Se non sei già loggato, fai login con il")
    print("  tuo account Accenture (MFA inclusa). Lo script si chiude")
    print("  da solo quando la sessione è pronta.")
    print("=" * 65)
    print()

    print("In attesa del login completato...")
    cookies = _run_browser(headless=False, timeout_ms=TIMEOUT_MS)

    if not cookies or "glide_session_store" not in cookies:
        print("Errore: glide_session_store non trovato. Il login non è andato a buon fine.")
        return 1

    _save_cookies(cookies)
    print(f"Sessione salvata ({len(cookies)} cookie) → {config.PLACES_COOKIES_FILE}")

    # Verify with a real API call
    from .booking import list_future_bookings
    try:
        bookings = list_future_bookings()
        print(f"Verifica API: OK ({len(bookings)} prenotazioni attive)")
    except Exception as e:
        print(f"Attenzione: verifica API fallita — {e}")
        return 1

    # Clear the expiry-notified flag so the keep-alive will re-notify if needed.
    (config.STATE_DIR / "sn_expiry_notified.flag").unlink(missing_ok=True)

    # Kick the keep-alive timer immediately so the first ping happens now
    # rather than up to 10 minutes from now (prevents session expiry in the gap).
    import subprocess
    subprocess.run(
        ["systemctl", "--user", "start", "parkbot-sn-keepalive.service"],
        capture_output=True,
    )

    print()
    print("Pronto. Usa /seat, /park o /book su Telegram per prenotare.")
    print("Keep-alive attivo: sessione rinnovata ogni 10 minuti.")
    return 0
