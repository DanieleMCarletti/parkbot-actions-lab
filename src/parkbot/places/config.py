"""Constants for support-places.accenture.com (ServiceNow WSD).

All sys_ids confirmed via network capture 2026-06-07.
"""
from pathlib import Path

# Master switch for the ServiceNow / support-places integration.
# Disabled 2026-06-11: Microsoft Conditional Access forces an interactive MFA
# re-auth on this portal roughly every ~20 min, which silent_refresh cannot
# satisfy headlessly. That makes SN booking non-automatable, so parkbot relies
# solely on the Milanofiori Nord (Cognito) flow. Flip back to True to re-enable.
PLACES_ENABLED = False

PLACES_BASE = "https://support-places.accenture.com"
PLACES_API  = f"{PLACES_BASE}/api/sn_wsd_rsv"

# reservable_module sys_ids
MODULE_PARKING = "386ced2c976255105854fed3a253afcd"
MODULE_DESK    = "3ad47df0976ad910e546fdd3a253af76"

# Assago, Via del Mulino 1
BUILDING_ASSAGO = "82131fc1870f15108beba71e0ebb35db"
FLOOR_PARKING   = "803eb51f97c5759081aaf1d11153afc9"

# Full-day slot in UTC (= 09:00–18:00 Europe/Rome)
SLOT_START_UTC = "T07:00:00Z"
SLOT_END_UTC   = "T16:00:00Z"
TIMEZONE       = "Europe/Rome"

# Booking window confirmed 2026-06-07: prenotabili fino a +14 giorni
WINDOW_DAYS = 14

TRANSACTION_SOURCE = (
    "Interface=Web,Interface-Name=PLACES,"
    "Interface-Type=Service Portal,"
    "Interface-SysID=92e59b4e97ef0950673b9934a253af5f"
)

STATE_DIR   = Path.home() / ".local" / "share" / "parkbot"
SECRETS_DIR = STATE_DIR / "secrets"

PLACES_COOKIES_FILE  = SECRETS_DIR / "places_cookies.json"
PLACES_PROFILE_DIR   = STATE_DIR / "places-profile"
