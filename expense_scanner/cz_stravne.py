from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from expense_scanner.json_fs import load_json

_RATES_PATH = Path(__file__).with_name("cz_stravne_rates.json")


def load_stravne_meta() -> Dict[str, Any]:
    data = load_json(_RATES_PATH)
    return {
        "legal_note_cs": data.get("legal_note_cs") or "",
        "reference_year": data.get("reference_year"),
    }


def inclusive_calendar_days(date_from: str, date_to: str) -> int:
    a = date.fromisoformat(date_from)
    b = date.fromisoformat(date_to)
    return (b - a).days + 1


def suggest_foreign_meal_allowance(
    country_code: str,
    date_from: str,
    date_to: str,
    claim_type: str,
) -> Optional[Dict[str, Any]]:
    if claim_type != "meal_allowance_cz":
        return None
    cc = (country_code or "").strip().upper()[:2]
    if not cc:
        return None
    data = load_json(_RATES_PATH)
    countries = data.get("countries") or {}
    row = countries.get(cc)
    if not isinstance(row, dict):
        return None
    raw_amt = row.get("amount")
    if raw_amt is None:
        return None
    try:
        per_day = float(raw_amt)
    except (TypeError, ValueError):
        return None
    days = inclusive_calendar_days(date_from, date_to)
    if days < 1:
        return None
    cur = str(row.get("currency") or "EUR").strip().upper()[:3]
    total = round(per_day * days, 2)
    return {
        "total": total,
        "currency": cur,
        "per_day": per_day,
        "days": days,
        "detail_cs": (
            f"{per_day:g} {cur} × {days} kal. dny "
            f"(celé dny; 1. a poslední den lze krátit 1/3–100 % dle hodin)"
        ),
    }


def effective_trip_amounts(trip: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "amount_total": trip.get("amount_total"),
        "currency": (trip.get("currency") or "CZK").strip().upper()[:3],
        "from_stravne_table": False,
        "stravne_detail_cs": None,
    }
    at = out["amount_total"]
    if at is not None and isinstance(at, (int, float)):
        return out
    sug = suggest_foreign_meal_allowance(
        str(trip.get("country_code") or ""),
        str(trip.get("date_from") or ""),
        str(trip.get("date_to") or ""),
        str(trip.get("claim_type") or "meal_allowance_cz"),
    )
    if not sug:
        return out
    out["amount_total"] = sug["total"]
    out["currency"] = sug["currency"]
    out["from_stravne_table"] = True
    out["stravne_detail_cs"] = sug["detail_cs"]
    return out
