"""Booking actions against the parcheggimilanofiorinord.it API.

All endpoints under /app/api/ are bearer-authenticated. No CSRF header is required;
the API trusts the Authorization header alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, timedelta
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


@dataclass
class ImmediateBookingResult:
    """Outcome of a same-second booking attempt (as opposed to the queue+midnight-fire path).

    outcome: "booked" | "past" | "waiting" | "unavailable" | "error"
    """
    outcome: str
    detail: str
    booking_id: Optional[int] = None
    spot: Optional[str] = None
    available: Optional[int] = None   # free spots left in the lot, right after booking
    capacity: Optional[int] = None    # total spots in the lot


def try_book_now(
    target_date: str | _date,
    *,
    lot_id: int = config.DEFAULT_LOT_ID,
    token: Optional[TokenBundle] = None,
) -> ImmediateBookingResult:
    """Attempt to book `target_date` right now, instead of waiting for the midnight cron.

    Only succeeds if the date is already inside the portal's bookable window (see
    BookingWindow) — the portal itself refuses bookings outside it, there's no way
    around that. Use `queue.queue_booking` + the nightly `fire` job for dates still
    out of window.
    """
    if isinstance(target_date, _date):
        target_date = target_date.isoformat()
    if token is None:
        token = get_access_token()

    window = get_booking_window(token=token)
    state = window.status(target_date)

    if state == "past":
        return ImmediateBookingResult("past", f"data passata (min {window.min_date})")
    if state == "waiting":
        return ImmediateBookingResult("waiting", f"fuori finestra (apre entro il {window.max_date})")
    if state == "unavailable":
        return ImmediateBookingResult("unavailable", "nessun posto disponibile (segnalato dal portale)")

    result = book_parking(target_date, lot_id=lot_id, token=token)
    if not result.success:
        if result.status_code == 404 and "disponibil" in result.body.lower():
            # The window said bookable, but the POST was refused as "non disponibile".
            # Root cause NOT confirmed (as of 2026-10-12) — two live hypotheses, both
            # consistent with observed data, neither proven:
            #   H1 (shared capacity): the 127-spot lot is genuinely full that day,
            #       company-wide, unrelated to this account.
            #   H2 (personal "1 future booking" limit): the portal may allow one
            #       "current" (today) + one "future" booking, not two future ones —
            #       an existing future booking would block any other future date.
            # cli._fire_parking_entry previously asserted H2 as fact; that's wrong,
            # it was a guess. Don't re-introduce that claim here without real
            # evidence (e.g. comparing postiDisponibili before/after a future booking
            # is cancelled, or once it lapses — see README/CLAUDE.md).
            return ImmediateBookingResult("unavailable", "nessun posto disponibile per questa data")
        return ImmediateBookingResult("error", f"http {result.status_code}: {result.body[:200]}")

    spot = None
    try:
        for b in list_future_bookings(token=token):
            if (b.get("data") or b.get("date")) == target_date:
                spot = b.get("posti_codice")
                break
    except Exception:
        pass

    available = capacity = None
    try:
        for lot in check_availability(target_date, token=token):
            if lot.get("id") == lot_id:
                capacity = lot.get("n_posti")
                # NOTE: "posti" is the lot's fixed catalog of spot codes (always
                # len == n_posti, includes already-booked spots) — NOT who's free.
                # "postiDisponibili" is the actual free-right-now list.
                available = len(lot.get("postiDisponibili") or [])
                break
    except Exception:
        pass

    return ImmediateBookingResult(
        "booked", "prenotato", booking_id=result.booking_id,
        spot=spot, available=available, capacity=capacity,
    )


def list_window_availability(
    *, lot_id: int = config.DEFAULT_LOT_ID, token: Optional[TokenBundle] = None,
) -> list[dict]:
    """Posti liberi per ogni giorno della finestra prenotabile attuale (min_date..max_date).

    Una chiamata API per giorno (la finestra è sempre pochi giorni, non è un problema).
    Ogni voce: {"date": "YYYY-MM-DD", "available": int | None, "capacity": int | None}
    — None se il lotto `lot_id` non compare nella risposta per quel giorno (non
    dovrebbe succedere, ma l'API ha già mostrato inconsistenze al bordo finestra).
    """
    if token is None:
        token = get_access_token()

    window = get_booking_window(token=token)
    start = _date.fromisoformat(window.min_date)
    end = _date.fromisoformat(window.max_date)

    results = []
    d = start
    while d <= end:
        iso = d.isoformat()
        available = capacity = None
        try:
            for lot in check_availability(iso, token=token):
                if lot.get("id") == lot_id:
                    capacity = lot.get("n_posti")
                    # see try_book_now: "posti" is the fixed catalog, not what's free
                    available = len(lot.get("postiDisponibili") or [])
                    break
        except Exception:
            pass
        results.append({"date": iso, "available": available, "capacity": capacity})
        d += timedelta(days=1)

    return results
