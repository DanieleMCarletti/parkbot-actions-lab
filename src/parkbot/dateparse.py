"""Lenient date parsing for human input (Italian).

Accepts:
  - Italian:       08/06/2026, 08-06-2026, 08/06 (year inferred)
  - ISO:           2026-06-04
  - relative:      oggi, domani, dopodomani
  - weekday names: lun/lunedì, mar/martedì, mer/mercoledì, gio/giovedì,
                   ven/venerdì, sab/sabato, dom/domenica
                   → resolves to the NEXT future occurrence (today excluded if
                     same weekday, since you book for upcoming days).

Internally everything is ISO (YYYY-MM-DD) — that's what the API wants and it keeps
queue filenames sortable. Use `to_display()` to render dd/mm/yyyy for the user.
"""
from __future__ import annotations

import re
from datetime import date, timedelta


def to_display(iso: str) -> str:
    """ISO YYYY-MM-DD → Italian dd/mm/yyyy for user-facing text."""
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso  # leave untouched if not a clean ISO date

_WEEKDAYS = {
    "lunedi": 0, "lunedì": 0, "lun": 0,
    "martedi": 1, "martedì": 1, "mar": 1,
    "mercoledi": 2, "mercoledì": 2, "mer": 2,
    "giovedi": 3, "giovedì": 3, "gio": 3,
    "venerdi": 4, "venerdì": 4, "ven": 4,
    "sabato": 5, "sab": 5,
    "domenica": 6, "dom": 6,
}


def parse_date(text: str, *, today: date | None = None) -> str:
    today = today or date.today()
    t = text.strip().lower()

    if t == "oggi":
        return today.isoformat()
    if t == "domani":
        return (today + timedelta(days=1)).isoformat()
    if t in ("dopodomani", "dopo domani"):
        return (today + timedelta(days=2)).isoformat()

    if t in _WEEKDAYS:
        target = _WEEKDAYS[t]
        # days until next occurrence; if today is that weekday, jump a full week
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return (today + timedelta(days=delta)).isoformat()

    d = _try_explicit_date(t, today)
    if d is None:
        raise ValueError(
            f"Non capisco la data '{text}'. Usa gg/mm/aaaa (es. 08/06/2026), "
            f"'domani', o un giorno (es. 'giovedì')."
        )
    if d < today:
        raise ValueError(f"La data {to_display(d.isoformat())} è nel passato.")
    return d.isoformat()


def _try_explicit_date(t: str, today: date) -> date | None:
    """Parse Italian dd/mm[/yyyy] (also '-' separator) or ISO. Returns None on miss."""
    # Italian dd/mm/yyyy or dd-mm-yyyy
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if m:
        day, month, year = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    # Italian dd/mm (no year) — infer year so the date is in the future
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", t)
    if m:
        day, month = (int(g) for g in m.groups())
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, month, day)
            except ValueError:
                return None
            if cand >= today:
                return cand
        return None
    # ISO yyyy-mm-dd
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None
