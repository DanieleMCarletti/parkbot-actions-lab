"""Booking actions against the parcheggimilanofiorinord.it API.

All endpoints under /app/api/ are bearer-authenticated. No CSRF header is required;
the API trusts the Authorization header alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

import httpx

from . import config
from .tokens import TokenBundle, get_access_token


class BookingError(Exception):
    """Booking request failed (HTTP error, validation error, or slot unavailable)."""


@dataclass
class BookingResult:
    success: bool
    booking_id: Optional[int]
    status_code: int
    body: str

    @property
    def short(self) -> str:
        if self.success:
            return f"OK booking_id={self.booking_id}"
        return f"FAIL http={self.status_code} body={self.body[:200]}"


def _client(token: TokenBundle) -> httpx.Client:
    return httpx.Client(
        timeout=15.0,
        headers={
            "Authorization": token.authorization_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The portal SPA sends this; some APIs sniff on it.
            "Origin": config.PORTAL_ORIGIN,
            "Referer": f"{config.PORTAL_ORIGIN}/app/prenotazioni/new",
        },
    )


def book_parking(
    target_date: str | _date,
    *,
    lot_id: int = config.DEFAULT_LOT_ID,
    token: Optional[TokenBundle] = None,
) -> BookingResult:
    """POST /app/api/prenotazioni — create a booking.

    Args:
        target_date: ISO date string "YYYY-MM-DD" or a datetime.date.
        lot_id: parking lot ID (default 54 = Assegnazione Giornaliera).
        token: optional pre-minted TokenBundle. If None, mints a fresh one.

    Returns BookingResult — never raises on HTTP error (lets caller decide).
    Raises BookingError only on serialization / unreachable network.
    """
    if isinstance(target_date, _date):
        target_date = target_date.isoformat()

    if token is None:
        token = get_access_token()

    payload = {
        "lotti_parcheggio_id": lot_id,
        "data": target_date,
        "fascia_oraria": "giorno",
        "ora_ingresso": "00:00",
        "ora_uscita": "23:59",
    }

    with _client(token) as client:
        try:
            r = client.post(f"{config.PORTAL_API}/prenotazioni", json=payload)
        except httpx.HTTPError as e:
            raise BookingError(f"Network error posting booking: {e}") from e

    booking_id = None
    body = r.text
    if r.status_code == 201:
        # Response is the new booking id as integer (or JSON containing it).
        try:
            booking_id = int(body.strip().strip('"'))
        except ValueError:
            # Maybe JSON with an id field
            try:
                j = r.json()
                booking_id = j.get("id") or j.get("prenotazione_id")
            except Exception:
                pass

    return BookingResult(
        success=(r.status_code == 201),
        booking_id=booking_id,
        status_code=r.status_code,
        body=body,
    )


def list_future_bookings(token: Optional[TokenBundle] = None) -> list[dict]:
    """GET /app/api/prenotazioni/future"""
    if token is None:
        token = get_access_token()
    with _client(token) as client:
        r = client.get(f"{config.PORTAL_API}/prenotazioni/future")
    r.raise_for_status()
    return r.json()


def check_availability(target_date: str | _date,
                       *,
                       token: Optional[TokenBundle] = None) -> list[dict]:
    """GET /app/api/parcheggi-disponibili?data=…&fascia-oraria=giorno"""
    if isinstance(target_date, _date):
        target_date = target_date.isoformat()
    if token is None:
        token = get_access_token()
    with _client(token) as client:
        r = client.get(
            f"{config.PORTAL_API}/parcheggi-disponibili",
            params={"data": target_date, "fascia-oraria": "giorno"},
        )
    r.raise_for_status()
    return r.json()


@dataclass
class BookingWindow:
    """The currently-bookable date range, from /prenotazioni/disponibilita.

    The portal only lets you book within [min_date, max_date]. max_date advances
    by one day at each midnight rollover (n_max_giorni_prenotabili days ahead).
    Dates beyond max_date are not yet bookable — booking them returns
    401 "Non abilitato a prenotare".
    """
    min_date: str
    max_date: str
    not_available: list[str]

    def status(self, target_date: str) -> str:
        """Classify a target date relative to the window.

        Returns one of: "past" | "waiting" | "unavailable" | "bookable".
        """
        if target_date < self.min_date:
            return "past"
        if target_date > self.max_date:
            return "waiting"        # not yet in the window — retry a later night
        if target_date in self.not_available:
            return "unavailable"    # in window but flagged full/closed
        return "bookable"


def get_booking_window(token: Optional[TokenBundle] = None) -> BookingWindow:
    """GET /app/api/prenotazioni/disponibilita → the bookable date range."""
    if token is None:
        token = get_access_token()
    with _client(token) as client:
        r = client.get(f"{config.PORTAL_API}/prenotazioni/disponibilita")
    r.raise_for_status()
    data = r.json()
    return BookingWindow(
        min_date=data["minDate"],
        max_date=data["maxDate"],
        not_available=data.get("notAvailable", []) or [],
    )
