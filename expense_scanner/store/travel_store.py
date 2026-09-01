import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from expense_scanner.json_fs import atomic_write_json, load_json

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def travel_file(output_dir: Path) -> Path:
    return output_dir / "travel_allowances.json"


def list_trips(output_dir: Path) -> List[Dict[str, Any]]:
    data = load_json(travel_file(output_dir))
    trips = data.get("trips")
    if not isinstance(trips, list):
        return []
    return trips


def _save_all(output_dir: Path, trips: List[Dict[str, Any]]) -> None:
    atomic_write_json(
        travel_file(output_dir),
        {
            "trips": trips,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def validate_trip_dates(d0: str, d1: str) -> None:
    if not _DATE.match(d0) or not _DATE.match(d1):
        raise ValueError("dates must be YYYY-MM-DD")
    if d0 > d1:
        raise ValueError("date_from must be <= date_to")


def add_trip(output_dir: Path, fields: Dict[str, Any]) -> Dict[str, Any]:
    validate_trip_dates(fields["date_from"], fields["date_to"])
    now = datetime.now(timezone.utc).isoformat()
    cc_raw = (fields.get("country_code") or "").strip().upper()
    trip: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "purpose": (fields.get("purpose") or "").strip(),
        "country_code": cc_raw[:2] if cc_raw else "",
        "destination": (fields.get("destination") or "").strip(),
        "date_from": fields["date_from"],
        "date_to": fields["date_to"],
        "claim_type": fields.get("claim_type") or "meal_allowance_cz",
        "amount_total": fields.get("amount_total"),
        "currency": (fields.get("currency") or "CZK").strip().upper()[:3],
        "notes": (fields.get("notes") or "").strip() or None,
        "created_at": now,
        "updated_at": now,
    }
    trips = list_trips(output_dir)
    trips.insert(0, trip)
    _save_all(output_dir, trips)
    return trip


def update_trip(
    output_dir: Path, tid: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    trips = list_trips(output_dir)
    for i, t in enumerate(trips):
        if t.get("id") != tid:
            continue
        merged = dict(t)
        if "purpose" in fields:
            merged["purpose"] = (fields.get("purpose") or "").strip()
        if "country_code" in fields:
            cc = fields.get("country_code") or ""
            merged["country_code"] = str(cc).strip().upper()[:2] if cc else ""
        if "destination" in fields:
            merged["destination"] = (fields.get("destination") or "").strip()
        if "date_from" in fields:
            merged["date_from"] = fields["date_from"]
        if "date_to" in fields:
            merged["date_to"] = fields["date_to"]
        if "claim_type" in fields:
            merged["claim_type"] = fields.get("claim_type") or "meal_allowance_cz"
        if "amount_total" in fields:
            merged["amount_total"] = fields["amount_total"]
        if "currency" in fields:
            merged["currency"] = (
                (fields.get("currency") or "CZK").strip().upper()[:3]
            )
        if "notes" in fields:
            merged["notes"] = (fields.get("notes") or "").strip() or None
        validate_trip_dates(merged["date_from"], merged["date_to"])
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        trips[i] = merged
        _save_all(output_dir, trips)
        return merged
    raise KeyError(tid)


def delete_trip(output_dir: Path, tid: str) -> None:
    trips = list_trips(output_dir)
    new_trips = [t for t in trips if t.get("id") != tid]
    if len(new_trips) == len(trips):
        raise KeyError(tid)
    _save_all(output_dir, new_trips)
