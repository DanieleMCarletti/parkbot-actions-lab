"""Booking queue — pending bookings live as one JSON file per date+type.

  ~/.local/share/parkbot/queue/2026-06-04-parking.json
    {"date":"2026-06-04","type":"parking","lot_id":54,"queued_at":...,"source":"telegram"}

  ~/.local/share/parkbot/queue/2026-06-04-seat.json
    {"date":"2026-06-04","type":"seat","lot_id":null,"queued_at":...,"source":"telegram"}

type values:
  "parking"  → parcheggimilanofiorinord.it (Milanofiori Nord, Cognito auth)
               fire logic: window-aware, midnight cron
  "seat"     → ServiceNow Open Workspace (Assago building)
               fire logic: book immediately; retry if full
  "sn_park"  → ServiceNow Parking (Assago building)
               fire logic: window-aware (~14d), retry if full

The midnight `fire` job reads each *.json, attempts the booking, then renames the
file to <date>-<type>.done.json (success) or <date>-<type>.failed-<ts>.json (failure).

Back-compat: old entries without `type` default to "parking".
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import config

VALID_TYPES = {"parking", "seat", "sn_park"}


@dataclass
class QueueEntry:
    date: str                   # ISO YYYY-MM-DD
    type: str                   # "parking" | "seat" | "sn_park"
    lot_id: Optional[int]
    queued_at: float
    source: str                 # "telegram" | "cli"
    # SN only — sys_id of a confirmed SN booking (set by fire on success)
    sn_sys_id: Optional[str] = field(default=None)

    @classmethod
    def load(cls, path: Path) -> "QueueEntry":
        with path.open() as f:
            data = json.load(f)
        # back-compat: old entries have no "type"
        data.setdefault("type", "parking")
        data.setdefault("sn_sys_id", None)
        return cls(
            date=data["date"],
            type=data["type"],
            lot_id=data.get("lot_id"),
            queued_at=data["queued_at"],
            source=data.get("source", "cli"),
            sn_sys_id=data.get("sn_sys_id"),
        )


def _queue_path(date: str, booking_type: str) -> Path:
    return config.QUEUE_DIR / f"{date}-{booking_type}.json"


def queue_booking(
    date: str,
    booking_type: str = "parking",
    *,
    lot_id: Optional[int] = None,
    source: str = "cli",
) -> tuple[Path, bool]:
    """Queue a booking entry. Returns (path, is_new).

    Idempotent: if a pending entry for (date, type) already exists, returns
    (path, False) without overwriting. Also migrates any old-format {date}.json
    to {date}-parking.json to prevent back-compat duplicates.
    """
    if booking_type not in VALID_TYPES:
        raise ValueError(f"Unknown booking type: {booking_type!r}")
    if booking_type == "parking" and lot_id is None:
        lot_id = config.DEFAULT_LOT_ID
    config.ensure_dirs()

    path = _queue_path(date, booking_type)

    # Migrate old-format file if it exists and no new-format file does yet
    old_path = config.QUEUE_DIR / f"{date}.json"
    if old_path.exists() and not path.exists() and booking_type == "parking":
        old_path.rename(path)

    if path.exists():
        return path, False  # already queued — idempotent

    entry = QueueEntry(
        date=date, type=booking_type,
        lot_id=lot_id, queued_at=time.time(), source=source,
    )
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(asdict(entry), f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path, True


def list_queue(booking_type: Optional[str] = None) -> list[QueueEntry]:
    """Return pending queue entries, optionally filtered by type.

    Deduplicates by (date, type): if both an old-format {date}.json and a
    new-format {date}-{type}.json exist for the same (date, parking), the
    old-format file is migrated/removed to avoid showing duplicates.
    """
    if not config.QUEUE_DIR.exists():
        return []
    seen: set[tuple[str, str]] = set()
    out = []
    for p in sorted(config.QUEUE_DIR.glob("*.json")):
        if ".done." in p.name or ".failed-" in p.name:
            continue
        try:
            entry = QueueEntry.load(p)
        except Exception:
            continue

        key = (entry.date, entry.type)

        # If old-format file (no type in name) but new-format already seen → remove old
        is_old_format = p.name == f"{entry.date}.json"
        new_path = _queue_path(entry.date, entry.type)
        if is_old_format and new_path.exists():
            p.unlink(missing_ok=True)
            continue

        if key in seen:
            continue
        seen.add(key)

        if booking_type is None or entry.type == booking_type:
            out.append(entry)
    return out


def mark_done(date: str, booking_type: str, booking_id: object) -> None:
    src = _queue_path(date, booking_type)
    if not src.exists():
        # back-compat: old queue files had no type suffix
        src = config.QUEUE_DIR / f"{date}.json"
    if not src.exists():
        return
    dst = _queue_path(date, booking_type)
    dst = dst.parent / (dst.stem + ".done.json")
    data = json.loads(src.read_text())
    data["booking_id"] = str(booking_id)
    data["completed_at"] = time.time()
    dst.write_text(json.dumps(data, indent=2))
    src.unlink()


def mark_failed(date: str, booking_type: str, error: str) -> None:
    src = _queue_path(date, booking_type)
    if not src.exists():
        src = config.QUEUE_DIR / f"{date}.json"
    if not src.exists():
        return
    dst = _queue_path(date, booking_type)
    dst = dst.parent / f"{dst.stem}.failed-{int(time.time())}.json"
    data = json.loads(src.read_text())
    data["error"] = error
    data["failed_at"] = time.time()
    dst.write_text(json.dumps(data, indent=2))
    src.unlink()
