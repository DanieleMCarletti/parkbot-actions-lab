"""Booking actions for support-places.accenture.com (ServiceNow WSD).

Covers:
  - Desk (Open Workspace) booking and cancellation
  - Parking (Assago building) booking and cancellation
  - Availability check (best_match → empty = full or out of window)
  - List future reservations
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, timedelta
from typing import Optional

from . import config
from .session import PlacesSession, SessionExpiredError, get_session


class PlacesBookingError(Exception):
    pass


@dataclass
class PlacesResult:
    success: bool
    sys_id: Optional[str]
    location_name: Optional[str]
    building_name: Optional[str]
    status_code: int
    body: str

    @property
    def short(self) -> str:
        if self.success:
            return f"OK sys_id={self.sys_id} location={self.location_name}"
        return f"FAIL http={self.status_code} body={self.body[:200]}"


def _date_str(d: str | _date) -> str:
    return d.isoformat() if isinstance(d, _date) else d


def _slot(date_iso: str) -> tuple[str, str]:
    """Return (start, end) UTC strings for a full working day."""
    return (
        date_iso + config.SLOT_START_UTC,
        date_iso + config.SLOT_END_UTC,
    )


def _find_best_spot(
    session: PlacesSession,
    date_iso: str,
    module: str,
    floor: Optional[str] = None,
) -> Optional[dict]:
    """Call best_match and return the first available reservable unit, or None."""
    start, end = _slot(date_iso)
    params: dict = {
        "reservable_module": module,
        "start": start,
        "end": end,
        "timezone": config.TIMEZONE,
        "include_standard_services": "true",
        "include_reservable_purposes": "true",
    }
    q_parts = [f"building={config.BUILDING_ASSAGO}"]
    if floor:
        q_parts.append(f"floorIN{floor}")
    q_parts += ["u_require_charge_code=false", "requires_approval=false"]
    params["q"] = "^".join(q_parts)

    r = session.client().get(f"{config.PLACES_API}/search/best_match", params=params)
    if r.status_code != 200:
        raise PlacesBookingError(f"best_match HTTP {r.status_code}: {r.text[:200]}")

    units = r.json().get("result", {}).get("reservableUnits", [])
    return units[0] if units else None


def _book(
    session: PlacesSession,
    date_iso: str,
    module: str,
    unit: dict,
) -> PlacesResult:
    start, end = _slot(date_iso)
    name = unit.get("name", "")
    payload = {
        "reservable_module": module,
        "start": start,
        "end": end,
        "location": unit["sys_id"],
        "subject": f"Reservation for {name}",
        "sub_source": "servicenow_quick_reserve",
        "sensitivity": "normal",
        "timezone": config.TIMEZONE,
    }
    r = session.client().post(f"{config.PLACES_API}/reservation/add", json=payload)
    body = r.text
    if r.status_code == 200:
        result = r.json().get("result", {})
        sys_id = result.get("sys_id")
        building = (unit.get("building") or {}).get("display_value", "Assago")
        return PlacesResult(
            success=True,
            sys_id=sys_id,
            location_name=name,
            building_name=building,
            status_code=200,
            body=body,
        )
    return PlacesResult(
        success=False,
        sys_id=None,
        location_name=None,
        building_name=None,
        status_code=r.status_code,
        body=body,
    )


def is_in_window(date_iso: str) -> bool:
    """True if date_iso is within the SN booking window (today … today+14)."""
    today = _date.today().isoformat()
    max_date = (_date.today() + timedelta(days=config.WINDOW_DAYS)).isoformat()
    return today <= date_iso <= max_date


def book_parking(
    target_date: str | _date,
    session: Optional[PlacesSession] = None,
) -> PlacesResult:
    """Find and book the first available parking spot at Assago building."""
    date_iso = _date_str(target_date)
    if session is None:
        session = get_session()
    unit = _find_best_spot(session, date_iso, config.MODULE_PARKING, config.FLOOR_PARKING)
    if unit is None:
        return PlacesResult(
            success=False, sys_id=None, location_name=None, building_name=None,
            status_code=0, body="no_spots",
        )
    return _book(session, date_iso, config.MODULE_PARKING, unit)


def book_desk(
    target_date: str | _date,
    session: Optional[PlacesSession] = None,
) -> PlacesResult:
    """Find and book the first available desk at Assago building."""
    date_iso = _date_str(target_date)
    if session is None:
        session = get_session()
    unit = _find_best_spot(session, date_iso, config.MODULE_DESK)
    if unit is None:
        return PlacesResult(
            success=False, sys_id=None, location_name=None, building_name=None,
            status_code=0, body="no_spots",
        )
    return _book(session, date_iso, config.MODULE_DESK, unit)


def cancel_booking(
    sys_id: str,
    session: Optional[PlacesSession] = None,
) -> bool:
    """Cancel a SN reservation by sys_id. Returns True on success."""
    if session is None:
        session = get_session()
    r = session.client().patch(
        f"{config.PLACES_API}/reservation/cancel/{sys_id}",
        json={"last_updated_sub_source": "servicenow_quick_reserve"},
    )
    return r.status_code == 200


def list_future_bookings(
    session: Optional[PlacesSession] = None,
) -> list[dict]:
    """Return active/future SN reservations for the current user."""
    owned = session is None
    if owned:
        session = get_session()
    try:
        params = {
            "encodedQuery": (
                "stateINdraft,awaiting_approval,confirmed,in_progress,approved,"
                "awaiting_confirmation^active=true^reservation_subtype!=group_parent"
                "^requested_forDYNAMIC90d1921e5f510100a9ad2572f2b477fe"
                "^start>=javascript:gs.beginningOfCurrentHour()"
            ),
            "endIndex": 50,
            "startIndex": 0,
        }
        r = session.client().get(f"{config.PLACES_API}/reservation/list", params=params)
        r.raise_for_status()
        return r.json().get("result", {}).get("reservations", [])
    finally:
        if owned:
            session.close()
