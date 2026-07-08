"""parkbot CLI — `python -m parkbot.cli <subcommand>` or `parkbot <subcommand>`."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date

from . import booking, bootstrap, bootstrap_edge, config, dateparse, notify, queue, tokens
from .places import booking as pb, config as pc
from .places.session import SessionExpiredError


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    # Default path drives NATIVE Windows Edge over CDP. Headed Chromium under WSLg
    # freezes the whole PC, so the old Playwright path is kept only as `bootstrap-wslg`.
    return bootstrap_edge.main()


def _cmd_bootstrap_wslg(_args: argparse.Namespace) -> int:
    # Legacy fallback: headed Chromium via WSLg (KNOWN to freeze this PC — avoid).
    return bootstrap.main()


def _cmd_places_bootstrap(_args: argparse.Namespace) -> int:
    from .places import bootstrap as pb_boot
    return pb_boot.main()


def _cmd_token(_args: argparse.Namespace) -> int:
    try:
        b = tokens.get_access_token()
    except tokens.TokenError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(b.authorization_header)
    return 0


def _cmd_book(args: argparse.Namespace) -> int:
    try:
        result = booking.book_parking(args.date, lot_id=args.lot_id)
    except booking.BookingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(result.short)
    return 0 if result.success else 3


def _cmd_queue(args: argparse.Namespace) -> int:
    path, is_new = queue.queue_booking(args.date, args.type, lot_id=args.lot_id, source="cli")
    print(f"{'queued' if is_new else 'already queued'}: {path}")
    return 0


def _cmd_list_queue(_args: argparse.Namespace) -> int:
    entries = queue.list_queue()
    if not entries:
        print("(queue empty)")
        return 0
    for e in entries:
        print(f"  {e.date}  type={e.type}  source={e.source}")
    return 0


def _cmd_future(_args: argparse.Namespace) -> int:
    # Milanofiori Nord
    mfn = booking.list_future_bookings()
    # ServiceNow
    try:
        sn = pb.list_future_bookings()
    except SessionExpiredError:
        sn = []
    print(json.dumps({"milanofiori_nord": mfn, "places_sn": sn}, indent=2, ensure_ascii=False))
    return 0


def _cmd_availability(args: argparse.Namespace) -> int:
    lots = booking.check_availability(args.date)
    print(json.dumps(lots, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# fire — unified queue processor (runs at midnight via Windows Task Scheduler)
# ---------------------------------------------------------------------------

def _fire_parking_entry(entry: queue.QueueEntry, token) -> tuple[bool, bool]:
    """Process a Milanofiori Nord parking entry.

    Returns (success, stay_queued).
    stay_queued=True means leave in queue for later retry.
    """
    try:
        window = booking.get_booking_window(token=token)
    except Exception as e:
        print(f"WARN: could not fetch MFN window ({e}); attempting blindly")
        window = None

    state = window.status(entry.date) if window else "bookable"

    if state == "waiting":
        print(f"MFN WAIT {entry.date}: fuori finestra (max {window.max_date})")
        return False, True  # stay queued

    if state == "past":
        queue.mark_failed(entry.date, entry.type, f"data passata (min {window.min_date})")
        notify.notify(f"🅿️⏭️ {dateparse.to_display(entry.date)}: "
                      f"data passata, rimossa dalla coda.")
        return False, False

    if state == "unavailable":
        print(f"MFN FULL {entry.date}: segnata non disponibile")
        return False, True  # stay queued, retry tomorrow

    # bookable
    try:
        result = booking.book_parking(entry.date, lot_id=entry.lot_id, token=token)
    except booking.BookingError as e:
        queue.mark_failed(entry.date, entry.type, f"network: {e}")
        notify.notify(f"🅿️❌ {dateparse.to_display(entry.date)}: errore di rete MFN — {e}")
        return False, False

    if result.success:
        # Fetch spot detail
        spot = None
        try:
            for b in booking.list_future_bookings(token=token):
                if (b.get("data") or b.get("date")) == entry.date:
                    spot = b.get("posti_codice")
                    break
        except Exception:
            pass
        queue.mark_done(entry.date, entry.type, result.booking_id or 0)
        spot_line = f"\nPosto: <b>{spot}</b>" if spot else ""
        notify.notify(
            f"🅿️✅ Parcheggio prenotato per <b>{dateparse.to_display(entry.date)}</b>\n"
            f"📍 Milanofiori Nord{spot_line}\n"
            f"(id: {result.booking_id})"
        )
        return True, False
    elif result.status_code == 404 and "disponibil" in result.body.lower():
        # Server refused despite window.status()=="bookable". The disponibilita
        # endpoint is buggy at the window edge (reports the max date bookable) while
        # the POST still enforces the "1 active booking at a time" rule → 404
        # "Non ci sono posti disponibili". This is TRANSIENT — it frees the moment the
        # current active booking is consumed — so keep it queued and retry; never mark
        # it terminally failed (and don't spam Telegram each hourly run).
        print(f"MFN WAIT {entry.date}: 404 no-spots "
              f"(prenotazione attiva in corso?) — resto in coda")
        return False, True
    else:
        queue.mark_failed(entry.date, entry.type,
                          f"http {result.status_code}: {result.body[:300]}")
        notify.notify(
            f"🅿️❌ {dateparse.to_display(entry.date)}: MFN fallito "
            f"(HTTP {result.status_code})."
        )
        return False, False


def _fire_sn_park_entry(entry: queue.QueueEntry, sn_session) -> tuple[bool, bool]:
    """Process a ServiceNow parking entry.

    Returns (success, stay_queued).
    """
    if not pb.is_in_window(entry.date):
        print(f"SN WAIT {entry.date}: fuori finestra SN (~{pc.WINDOW_DAYS}gg)")
        return False, True

    if entry.date < _date.today().isoformat():
        queue.mark_failed(entry.date, entry.type, "data passata")
        notify.notify(f"🅿️⏭️ {dateparse.to_display(entry.date)}: data passata, rimossa.")
        return False, False

    try:
        result = pb.book_parking(entry.date, session=sn_session)
    except SessionExpiredError as e:
        notify.notify(f"🅿️❌ SN sessione scaduta: {e}")
        return False, True  # keep queued — user will re-bootstrap

    if result.success:
        queue.mark_done(entry.date, entry.type, result.sys_id or "")
        notify.notify(
            f"🅿️✅ Parcheggio prenotato per <b>{dateparse.to_display(entry.date)}</b>\n"
            f"📍 {result.building_name} — <b>{result.location_name}</b>\n"
            f"(id: {result.sys_id})"
        )
        return True, False
    elif result.body == "no_spots":
        print(f"SN FULL {entry.date}: nessun posto disponibile")
        return False, True  # stay queued, retry tomorrow
    else:
        queue.mark_failed(entry.date, entry.type,
                          f"http {result.status_code}: {result.body[:300]}")
        notify.notify(
            f"🅿️❌ {dateparse.to_display(entry.date)}: SN parking fallito "
            f"(HTTP {result.status_code})."
        )
        return False, False


def _fire_seat_entry(entry: queue.QueueEntry, sn_session) -> tuple[bool, bool]:
    """Process a seat (desk) booking entry."""
    if entry.date < _date.today().isoformat():
        queue.mark_failed(entry.date, entry.type, "data passata")
        notify.notify(f"🪑⏭️ {dateparse.to_display(entry.date)}: data passata, rimossa.")
        return False, False

    try:
        result = pb.book_desk(entry.date, session=sn_session)
    except SessionExpiredError as e:
        notify.notify(f"🪑❌ SN sessione scaduta: {e}")
        return False, True

    if result.success:
        queue.mark_done(entry.date, entry.type, result.sys_id or "")
        notify.notify(
            f"🪑✅ Scrivania prenotata per <b>{dateparse.to_display(entry.date)}</b>\n"
            f"📍 {result.building_name} — <b>{result.location_name}</b>\n"
            f"(id: {result.sys_id})"
        )
        return True, False
    elif result.body == "no_spots":
        print(f"SEAT FULL {entry.date}: nessuna scrivania disponibile")
        return False, True
    else:
        queue.mark_failed(entry.date, entry.type,
                          f"http {result.status_code}: {result.body[:300]}")
        notify.notify(
            f"🪑❌ {dateparse.to_display(entry.date)}: scrivania fallita "
            f"(HTTP {result.status_code})."
        )
        return False, False


def _cmd_fire(_args: argparse.Namespace) -> int:
    """Process the queue. Cron entry point at 00:00:02."""
    entries = queue.list_queue()
    if not entries:
        print("queue empty — nothing to do")
        return 0

    # Mint Cognito token once for all parking entries
    mfn_token = None
    parking_entries = [e for e in entries if e.type == "parking"]
    if parking_entries:
        try:
            mfn_token = tokens.get_access_token()
        except tokens.TokenError as e:
            print(f"WARN: Cognito token error — {e}", file=sys.stderr)
            notify.notify(
                f"🅿️❌ Token Milanofiori Nord scaduto.\n"
                f"Rilancia <code>parkbot bootstrap</code> (serve un tap MFA).\n"
                f"Interessati: {', '.join(dateparse.to_display(e.date) for e in parking_entries)}"
            )

    # Get SN session once for all SN entries.
    # On expiry: try headless silent refresh first (works if SAML profile tokens
    # are still valid — no MFA needed); only notify user if that also fails.
    sn_session = None
    sn_entries = [e for e in entries if e.type in ("seat", "sn_park")]
    if sn_entries and not pc.PLACES_ENABLED:
        # SN integration disabled — skip silently. For parking, the MFN sibling
        # (if queued) covers the same date; seat entries are simply not processed.
        print(f"SN disabled — skipping {len(sn_entries)} SN entr{'y' if len(sn_entries)==1 else 'ies'} "
              f"({', '.join(e.date for e in sn_entries)})", file=sys.stderr)
        sn_entries = []
    if sn_entries:
        try:
            sn_session = pb.get_session()
        except SessionExpiredError:
            print("SN session expired — trying silent headless refresh...", file=sys.stderr)
            from .places.bootstrap import silent_refresh
            refreshed = silent_refresh()
            if refreshed:
                print("Silent refresh OK — retrying session...", file=sys.stderr)
                try:
                    sn_session = pb.get_session()
                except SessionExpiredError:
                    pass
            if sn_session is None:
                notify.notify(
                    f"🏢❌ Sessione SN scaduta.\n"
                    f"Rilancia <code>parkbot places-bootstrap</code> (serve un tap MFA).\n"
                    f"Interessati: {', '.join(dateparse.to_display(e.date) for e in sn_entries)}"
                )

    # Group entries by date so we can coordinate the parking fallback logic:
    # sn_park and parking for the same date are a priority pair — only one
    # should ultimately succeed. Within a single fire run, if sn_park is full
    # we fall through to parking (MFN) on the same night if it's in window.
    # If either succeeds, the sibling entry is cancelled to prevent double-booking.
    from collections import defaultdict
    by_date: dict[str, dict[str, queue.QueueEntry]] = defaultdict(dict)
    for e in entries:
        by_date[e.date][e.type] = e

    overall_rc = 0
    # Collect results for end-of-run summary notification
    booked_dates: list[str] = []
    waiting_dates: list[str] = []
    failed_dates: list[str] = []

    for date in sorted(by_date):
        type_map = by_date[date]
        print(f"Processing date={date} types={list(type_map)}")

        # --- Parking (sn_park first, MFN fallback) ---
        park_booked = False

        if "sn_park" in type_map:
            if sn_session is None:
                overall_rc = 1
            else:
                success, stay = _fire_sn_park_entry(type_map["sn_park"], sn_session)
                if success:
                    park_booked = True
                    # Cancel MFN sibling to prevent double-booking
                    mfn_path = config.QUEUE_DIR / f"{date}-parking.json"
                    mfn_path.unlink(missing_ok=True)
                elif not stay:
                    overall_rc = 1

        if not park_booked and "parking" in type_map:
            if mfn_token is None:
                overall_rc = 1
            else:
                success, stay = _fire_parking_entry(type_map["parking"], mfn_token)
                if success:
                    park_booked = True
                    # Cancel SN sibling to prevent double-booking
                    sn_path = config.QUEUE_DIR / f"{date}-sn_park.json"
                    sn_path.unlink(missing_ok=True)
                elif stay:
                    waiting_dates.append(date)
                else:
                    failed_dates.append(date)

        if park_booked:
            booked_dates.append(date)

        # --- Seat (independent of parking) ---
        if "seat" in type_map:
            if sn_session is None:
                overall_rc = 1
            else:
                success, stay = _fire_seat_entry(type_map["seat"], sn_session)
                if not success and not stay:
                    overall_rc = 1

    # End-of-run summary notification (only if there's something to report)
    if booked_dates or waiting_dates or failed_dates:
        lines = ["🅿️ <b>Riepilogo prenotazioni</b>"]
        for d in booked_dates:
            lines.append(f"✅ Prenotato: <b>{dateparse.to_display(d)}</b>")
        for d in waiting_dates:
            lines.append(f"⏳ In coda: <b>{dateparse.to_display(d)}</b> — fuori finestra, riprovo domani")
        for d in failed_dates:
            lines.append(f"❌ Fallito: <b>{dateparse.to_display(d)}</b>")
        notify.notify("\n".join(lines))

    return overall_rc


_EXPIRY_NOTIFIED_FLAG = config.STATE_DIR / "sn_expiry_notified.flag"


def _cmd_sn_keepalive(_args: argparse.Namespace) -> int:
    """Ping SN to reset session inactivity timeout. Run every 10 min via systemd timer."""
    if not pc.PLACES_ENABLED:
        print("SN integration disabled (PLACES_ENABLED=False) — keepalive no-op")
        return 0
    from .places.session import SessionExpiredError
    from .places import booking as pb
    try:
        session = pb.get_session()
        session.close()
        # Session alive — clear any previous expiry flag so next expiry re-notifies.
        _EXPIRY_NOTIFIED_FLAG.unlink(missing_ok=True)
        print("SN session alive — cookies refreshed")
        return 0
    except SessionExpiredError:
        # Try silent headed re-auth (uses DISPLAY=:0 off-screen window).
        # Microsoft Conditional Access blocks headless Chrome, so we use headed mode.
        from .places.bootstrap import silent_refresh
        if silent_refresh():
            _EXPIRY_NOTIFIED_FLAG.unlink(missing_ok=True)
            print("SN session refreshed via silent browser re-auth")
            return 0
        # Both failed. Notify only ONCE per expiry cycle — not every 10 minutes.
        if _EXPIRY_NOTIFIED_FLAG.exists():
            print("SN session expired (already notified, waiting for bootstrap)", file=sys.stderr)
            return 1
        _EXPIRY_NOTIFIED_FLAG.touch()
        notify.notify(
            "🏢⚠️ Sessione SN scaduta.\n"
            "Esegui <code>DISPLAY=:0 parkbot places-bootstrap</code> per ripristinarla."
        )
        print("SN session expired — user notified once", file=sys.stderr)
        return 1


def _cmd_serve_bot(_args: argparse.Namespace) -> int:
    from . import telegram_bot
    return telegram_bot.main()


def main() -> int:
    p = argparse.ArgumentParser(prog="parkbot")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="login Milanofiori Nord (Cognito, via native Edge)")
    sub.add_parser("bootstrap-wslg", help="[legacy] login via WSLg Chromium (freezes PC)")
    sub.add_parser("places-bootstrap", help="login ServiceNow places (SAML)")
    sub.add_parser("token", help="stampa access_token Cognito (debug)")

    p_book = sub.add_parser("book", help="prenota subito su Milanofiori Nord")
    p_book.add_argument("date")
    p_book.add_argument("--lot-id", type=int, default=config.DEFAULT_LOT_ID)

    p_q = sub.add_parser("queue", help="metti in coda")
    p_q.add_argument("date")
    p_q.add_argument("--type", default="parking",
                     choices=["parking", "seat", "sn_park"])
    p_q.add_argument("--lot-id", type=int, default=None)

    sub.add_parser("list-queue", help="mostra coda")
    sub.add_parser("future", help="prenotazioni confermate (tutti i sistemi)")

    p_av = sub.add_parser("availability", help="disponibilità Milanofiori Nord")
    p_av.add_argument("date")

    sub.add_parser("fire", help="processa coda (entry point cron)")
    sub.add_parser("sn-keepalive", help="ping SN per mantenere la sessione attiva (ogni 25 min)")
    sub.add_parser("serve-bot", help="avvia il bot Telegram (long-running)")

    args = p.parse_args()
    dispatch = {
        "bootstrap": _cmd_bootstrap,
        "bootstrap-wslg": _cmd_bootstrap_wslg,
        "places-bootstrap": _cmd_places_bootstrap,
        "token": _cmd_token,
        "book": _cmd_book,
        "queue": _cmd_queue,
        "list-queue": _cmd_list_queue,
        "future": _cmd_future,
        "availability": _cmd_availability,
        "fire": _cmd_fire,
        "sn-keepalive": _cmd_sn_keepalive,
        "serve-bot": _cmd_serve_bot,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
