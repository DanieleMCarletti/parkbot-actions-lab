"""Bootstrap via NATIVE Windows Edge over CDP.

Replaces the old headed-Chromium-under-WSLg bootstrap: launching a GUI browser through
WSLg freezes the whole Windows PC (WSLg's vGPU/RDP bridge chokes on heavy compositing).
Instead we drive the user's native Windows Edge over the DevTools protocol.

Because WSL is NAT-networked it cannot reach Edge's ``127.0.0.1`` DevTools port, so the
capture client (``win_cdp_capture.py``) runs on the **Windows** side under ``python.exe``.
This module only orchestrates:

  1. Wipe the dedicated Edge profile (forces a clean SSO -> a full authorization_code
     exchange, which is the only response that carries a refresh_token).
  2. Launch native ``msedge.exe`` with a remote-debugging port + dedicated profile,
     opening the portal login page. The user does Accenture SSO + one MFA tap.
  3. Run ``win_cdp_capture.py`` under Windows ``python.exe``; it captures the
     ``/oauth2/token`` bundle and writes it to a ``C:\\`` temp file.
  4. Read the bundle back over ``/mnt/c``, persist the refresh_token (reusing
     ``tokens.save_initial_tokens``), and verify the refresh grant + a real API call.

Re-run when the refresh_token expires (~30 days, Cognito default).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import booking, config, tokens

# --- Windows-side layout (overridable via env for portability) ---------------
WIN_USER = os.environ.get("PARKBOT_WIN_USER", "luigi.sambolino")
DEBUG_PORT = os.environ.get("PARKBOT_EDGE_PORT", "9333")  # NOT 9222 (WSL NAT collision)
EDGE_EXE = os.environ.get(
    "PARKBOT_EDGE_EXE",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

_WIN_LOCAL = rf"C:\Users\{WIN_USER}\AppData\Local"
_WIN_TMP = rf"{_WIN_LOCAL}\Temp"
_WIN_PROFILE = rf"{_WIN_LOCAL}\parkbot-edge-profile"
_WIN_CAPTURE = rf"{_WIN_TMP}\parkbot_win_cdp_capture.py"
_WIN_OUT = rf"{_WIN_TMP}\parkbot_token_capture.json"

# WSL views of the same Windows paths.
_WSL_TMP = Path(f"/mnt/c/Users/{WIN_USER}/AppData/Local/Temp")
_WSL_PROFILE = Path(f"/mnt/c/Users/{WIN_USER}/AppData/Local/parkbot-edge-profile")
_WSL_CAPTURE = _WSL_TMP / "parkbot_win_cdp_capture.py"
_WSL_OUT = _WSL_TMP / "parkbot_token_capture.json"

_LOGIN_URL = f"{config.PORTAL_ORIGIN}/app/login"
_CAPTURE_SRC = Path(__file__).with_name("win_cdp_capture.py")
_TIMEOUT = 300  # seconds the user has to complete SSO + MFA


def _ps(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True,
    )


def _run() -> int:
    config.ensure_dirs()

    win_py = shutil.which("python.exe")
    if not win_py:
        print("[error] Windows python.exe is not on the WSL PATH — cannot drive Edge.\n"
              "        (Expected the Windows Python install to be on PATH.)",
              file=sys.stderr)
        return 3

    # Clear any stale capture output from a previous run.
    _WSL_OUT.unlink(missing_ok=True)

    # Ensure the CDP transport dep is present for Windows python (idempotent, instant
    # if already installed).
    subprocess.run([win_py, "-m", "pip", "install", "--user", "--quiet",
                    "websocket-client"], capture_output=True, text=True)

    # Stage the capture script onto a real C:\ path — Windows python running scripts
    # from a \\wsl$ / /mnt path is flaky; a native path is reliable.
    _WSL_TMP.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_CAPTURE_SRC, _WSL_CAPTURE)

    # Kill any Edge still holding our debug profile, then wipe it for a clean SSO.
    _ps("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -like '*parkbot-edge-profile*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    time.sleep(1.0)
    if _WSL_PROFILE.exists():
        shutil.rmtree(_WSL_PROFILE, ignore_errors=True)

    # Launch native Edge (renders on Windows -> no WSLg, no freeze).
    launch = (
        f"Start-Process -FilePath '{EDGE_EXE}' -ArgumentList "
        f"'--remote-debugging-port={DEBUG_PORT}',"
        f"'--remote-allow-origins=*',"
        f"'--user-data-dir={_WIN_PROFILE}',"
        f"'--no-first-run','--no-default-browser-check',"
        # Suppress Edge's fresh-profile sync / implicit work-account sign-in dialog —
        # it steals focus and blocks the user from completing the portal login.
        f"'--disable-sync','--disable-features=msImplicitSignin,msEdgeSyncPromo',"
        f"'--new-window','{_LOGIN_URL}'"
    )
    r = _ps(launch)
    if r.returncode != 0:
        print(f"[error] Edge launch failed: {r.stderr.strip() or r.stdout.strip()}",
              file=sys.stderr)
        return 3

    print("=" * 70)
    print(" Native Edge is opening on Windows. Log in with Accenture SSO (+ MFA tap).")
    print(" Capture is automatic — it finishes the moment it sees the token bundle.")
    print(f" You have up to {_TIMEOUT // 60} minutes. Edge is safe to close afterwards.")
    print("=" * 70)

    # Run the capture client ON Windows (localhost reachable there). Stream its output
    # so the user sees progress live.
    cap = subprocess.run(
        [win_py, _WIN_CAPTURE,
         "--port", str(DEBUG_PORT),
         "--cognito-domain", config.COGNITO_DOMAIN,
         "--out", _WIN_OUT,
         "--timeout", str(_TIMEOUT)],
        text=True,
    )
    if cap.returncode != 0:
        rc = {2: "no token captured (timed out or login not completed)",
              3: "could not reach Edge's debug port"}.get(cap.returncode, "unknown error")
        print(f"[error] capture failed ({rc}).", file=sys.stderr)
        return 2

    # Read the bundle back from the Windows temp file (tiny propagation window on /mnt).
    for _ in range(20):
        if _WSL_OUT.exists():
            break
        time.sleep(0.25)
    if not _WSL_OUT.exists():
        print(f"[error] capture reported success but {_WSL_OUT} is missing.",
              file=sys.stderr)
        return 2

    captured = json.loads(_WSL_OUT.read_text())
    _WSL_OUT.unlink(missing_ok=True)

    if "refresh_token" not in captured:
        print("[error] captured bundle has no refresh_token.", file=sys.stderr)
        return 2

    bundle = tokens.save_initial_tokens(captured)
    print(f"  ✓ saved refresh_token to {config.TOKEN_FILE}")

    print("  · verifying refresh grant…")
    fresh = tokens.get_access_token()
    if fresh.access_token == bundle.access_token:
        print("  ✓ refresh returned (same) access_token — OK")
    else:
        print("  ✓ refresh minted a NEW access_token — OK")

    print("  · sanity check: future bookings…")
    try:
        result = booking.list_future_bookings(token=fresh)
        print(f"  ✓ API OK — you have {len(result)} future booking(s).")
    except Exception as e:  # noqa: BLE001
        print(f"  ! API check failed: {e}", file=sys.stderr)
        print("    Bootstrap saved the token, but the API rejected it. Investigate.",
              file=sys.stderr)
        return 1

    print()
    print("Done. Refresh token good for ~30 days. Now book the queued dates:")
    print("  parkbot fire")
    return 0


def main() -> int:
    return _run()


if __name__ == "__main__":
    sys.exit(main())
