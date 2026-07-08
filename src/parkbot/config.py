"""Constants and paths — no logic, just identity."""
from __future__ import annotations

import os
from pathlib import Path

# Cognito (eu-south-1, Milan) — from recon 2026-05-28
COGNITO_DOMAIN = "parcheggi-milanofiori-nord.auth.eu-south-1.amazoncognito.com"
COGNITO_CLIENT_ID = "3okj0mmk5fhbvs4afddf9002h8"
COGNITO_TOKEN_URL = f"https://{COGNITO_DOMAIN}/oauth2/token"

# Parking portal API
PORTAL_ORIGIN = "https://parcheggimilanofiorinord.it"
PORTAL_API = f"{PORTAL_ORIGIN}/app/api"

# Default lot for Luigi's office (Assegnazione Giornaliera, 127 spots, auto-assigned)
DEFAULT_LOT_ID = 54

# Local state — kept OUTSIDE OneDrive on purpose. These hold session secrets.
STATE_DIR = Path(os.environ.get("PARKBOT_STATE_DIR",
                                 Path.home() / ".local" / "share" / "parkbot"))
SECRETS_DIR = STATE_DIR / "secrets"
QUEUE_DIR = STATE_DIR / "queue"
LOG_DIR = STATE_DIR / "logs"
BOOTSTRAP_PROFILE_DIR = STATE_DIR / "bootstrap-profile"

TOKEN_FILE = SECRETS_DIR / "tokens.json"
TELEGRAM_CONFIG_FILE = SECRETS_DIR / "telegram.json"


def ensure_dirs() -> None:
    """Create state directories with restrictive permissions."""
    for d in (STATE_DIR, SECRETS_DIR, QUEUE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Tighten perms on the secrets dir specifically
    os.chmod(SECRETS_DIR, 0o700)
