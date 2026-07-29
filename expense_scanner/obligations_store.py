import re
import uuid
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from expense_scanner.json_fs import atomic_write_json, load_json

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_YM = re.compile(r"^\d{4}-\d{2}$")
_ISO_Q = re.compile(r"^\d{4}-Q[1-4]$", re.IGNORECASE)

VALID_KINDS = frozenset(
    {
        "health",
        "sickness",
        "pension",
        "tax",
        "vat",
        "vat_summary",
        "fine",
        "other",
    }
)

INSURANCE_KINDS = frozenset({"health", "sickness", "pension"})

PRESETS: List[Dict[str, Any]] = [
    {"kind": "health", "default_amount": 3306.0, "currency": "CZK"},
    {"kind": "sickness", "default_amount": 243.0, "currency": "CZK"},
    {"kind": "pension", "default_amount": 5005.0, "currency": "CZK"},
]


def obligations_file(output_dir: Path) -> Path:
    return output_dir / "obligations.json"


def obligations_summary_file(output_dir: Path) -> Path:
    return output_dir / "obligations_summary.json"


def _normalize_kind(raw: Optional[str]) -> str:
    k = (raw or "other").strip().lower()
    return k if k in VALID_KINDS else "other"


def load_obligations_doc(output_dir: Path) -> Dict[str, Any]:
    data = load_json(obligations_file(output_dir))
    if not isinstance(data, dict):
        return {"meta": {}, "entries": []}
    ent = data.get("entries")
    if not isinstance(ent, list):
        ent = []
    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    return {"meta": meta, "entries": ent}


def list_presets() -> List[Dict[str, Any]]:
    return list(PRESETS)


def list_entries(output_dir: Path) -> List[Dict[str, Any]]:
    return list(load_obligations_doc(output_dir)["entries"])


def get_meta(output_dir: Path) -> Dict[str, Any]:
    m = load_obligations_doc(output_dir)["meta"]
    vi = m.get("vat_identified")
    return {
        "osvc_since": m.get("osvc_since") if m.get("osvc_since") else None,
        "sickness_from": m.get("sickness_from") if m.get("sickness_from") else None,
        "vat_identified": bool(vi) if vi is not None else False,
        "vat_identified_from": m.get("vat_identified_from") or None,
    }


def _preset_amount(kind: str) -> float:
    for p in PRESETS:
        if p["kind"] == kind:
            v = p.get("default_amount")
            return float(v) if v is not None else 0.0
    return 0.0


def _save_doc(output_dir: Path, meta: Dict[str, Any], entries: List[Dict[str, Any]]) -> None:
    atomic_write_json(
        obligations_file(output_dir),
        {
            "meta": meta,
            "entries": entries,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    write_summary_snapshot(output_dir)


def set_meta_fields(
    output_dir: Path,
    osvc_since: Optional[str],
    sickness_from: Optional[str],
    vat_identified: Optional[bool] = None,
    vat_identified_from: Optional[str] = None,
) -> Dict[str, Any]:
    doc = load_obligations_doc(output_dir)
    meta = dict(doc["meta"])
    o = (osvc_since or "").strip()
    s = (sickness_from or "").strip()
    if o and not _DATE.match(o):
        raise ValueError("osvc_since must be YYYY-MM-DD or empty")
    if s and not _DATE.match(s):
        raise ValueError("sickness_from must be YYYY-MM-DD or empty")
    meta["osvc_since"] = o or None
    meta["sickness_from"] = s or None
    if vat_identified is not None:
        meta["vat_identified"] = bool(vat_identified)
        if not meta["vat_identified"]:
            meta["vat_identified_from"] = None
    if vat_identified is not None and meta.get("vat_identified"):
        vf = (vat_identified_from or "").strip() if vat_identified_from else ""
        if vf:
            if not _DATE.match(vf):
                raise ValueError("vat_identified_from must be YYYY-MM-DD or empty")
            meta["vat_identified_from"] = vf
        else:
            meta["vat_identified_from"] = None
    _save_doc(output_dir, meta, doc["entries"])
    return get_meta(output_dir)


def _save_all(output_dir: Path, entries: List[Dict[str, Any]]) -> None:
    doc = load_obligations_doc(output_dir)
    _save_doc(output_dir, doc["meta"], entries)


def _validate_dates(due: str, paid: Optional[str]) -> None:
    if not _DATE.match(due):
        raise ValueError("due_date must be YYYY-MM-DD")
    if paid is not None and paid != "":
        if not _DATE.match(paid):
            raise ValueError("paid_date must be YYYY-MM-DD or empty")


def _norm_period_month(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if len(s) >= 7 and _ISO_YM.match(s[:7]):
        return s[:7]
    return None


def _norm_quarter_key(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper().replace(" ", "")
    if _ISO_Q.match(s):
        return s[:4] + "-Q" + s[-1]
    return None


def normalize_period_field(raw: Optional[str]) -> Optional[str]:
    qk = _norm_quarter_key(raw)
    if qk:
        return qk
    return _norm_period_month(raw)


def add_entry(output_dir: Path, fields: Dict[str, Any]) -> Dict[str, Any]:
    due = (fields.get("due_date") or "").strip()
    paid_raw = fields.get("paid_date")
    paid = (paid_raw or "").strip() if paid_raw is not None else ""
    paid_opt: Optional[str] = paid if paid else None
    _validate_dates(due, paid_opt)
    amt = fields.get("amount")
    if amt is None:
        raise ValueError("amount required")
    try:
        amount = float(amt)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number") from None
    if amount < 0:
        raise ValueError("amount must be >= 0")
    now = datetime.now(timezone.utc).isoformat()
    pm = normalize_period_field(fields.get("period_month"))
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "kind": _normalize_kind(fields.get("kind")),
        "title": (fields.get("title") or "").strip() or None,
        "amount": amount,
        "currency": (fields.get("currency") or "CZK").strip().upper()[:3],
        "due_date": due,
        "paid_date": paid_opt,
        "period_month": pm,
        "notes": (fields.get("notes") or "").strip() or None,
        "created_at": now,
        "updated_at": now,
    }
    entries = list_entries(output_dir)
    entries.insert(0, entry)
    _save_all(output_dir, entries)
    return entry


def update_entry(
    output_dir: Path, eid: str, fields: Dict[str, Any]
) -> Dict[str, Any]:
    entries = list_entries(output_dir)
    for i, row in enumerate(entries):
        if row.get("id") != eid:
            continue
        merged = dict(row)
        if "kind" in fields:
            merged["kind"] = _normalize_kind(fields.get("kind"))
        if "title" in fields:
            merged["title"] = (fields.get("title") or "").strip() or None
        if "amount" in fields:
            try:
                a = float(fields["amount"])
            except (TypeError, ValueError):
                raise ValueError("amount must be a number") from None
            if a < 0:
                raise ValueError("amount must be >= 0")
            merged["amount"] = a
        if "currency" in fields:
            merged["currency"] = (
                (fields.get("currency") or "CZK").strip().upper()[:3]
            )
        if "due_date" in fields:
            merged["due_date"] = (fields.get("due_date") or "").strip()
        if "paid_date" in fields:
            p = fields.get("paid_date")
            if p is None or str(p).strip() == "":
                merged["paid_date"] = None
            else:
                merged["paid_date"] = str(p).strip()
        if "period_month" in fields:
            p2 = fields.get("period_month")
            merged["period_month"] = normalize_period_field(
                str(p2) if p2 is not None else None
            )
        if "notes" in fields:
            merged["notes"] = (fields.get("notes") or "").strip() or None
        due = merged.get("due_date") or ""
        paid_opt = merged.get("paid_date")
        paid_str = paid_opt if isinstance(paid_opt, str) else None
        _validate_dates(due, paid_str)
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        entries[i] = merged
        _save_all(output_dir, entries)
        return merged
    raise KeyError(eid)


def delete_entry(output_dir: Path, eid: str) -> None:
    entries = list_entries(output_dir)
    new_entries = [e for e in entries if e.get("id") != eid]
    if len(new_entries) == len(entries):
        raise KeyError(eid)
    _save_all(output_dir, new_entries)


def _today_calendar() -> str:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Europe/Prague")).date().isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _current_ym() -> str:
    return _today_calendar()[:7]


def _ym_to_sort_tuple(ym: str) -> Tuple[int, int]:
    y, m = map(int, ym.split("-"))
    return y, m


def _iter_months_inclusive(start_ym: str, end_ym: str) -> List[str]:
    if start_ym > end_ym:
        return []
    out: List[str] = []
    y, mo = _ym_to_sort_tuple(start_ym)
    ey, emo = _ym_to_sort_tuple(end_ym)
    cy, cm = y, mo
    while (cy, cm) <= (ey, emo):
        out.append(f"{cy:04d}-{cm:02d}")
        cm += 1
        if cm > 12:
            cm = 1
            cy += 1
    return out


def _next_month_ym(ym: str) -> str:
    y, m = _ym_to_sort_tuple(ym)
    if m == 12:
        return f"{y + 1:04d}-01"
    return f"{y:04d}-{m + 1:02d}"


def due_date_for_insurance(kind: str, period_ym: str) -> str:
    if kind == "health":
        ny, nm = _ym_to_sort_tuple(_next_month_ym(period_ym))
        return f"{ny:04d}-{nm:02d}-08"
    y, m = _ym_to_sort_tuple(period_ym)
    last = monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-{last:02d}"


def infer_period_month(entry: Dict[str, Any]) -> Optional[str]:
    if str(entry.get("kind") or "") == "vat_summary":
        return None
    pm = _norm_period_month(entry.get("period_month"))
    if pm:
        return pm
    kind = str(entry.get("kind") or "")
    due = entry.get("due_date")
    if not isinstance(due, str) or not _DATE.match(due):
        return None
    y, mo, d = map(int, due.split("-"))
    if kind == "health" and d == 8:
        if mo == 1:
            return f"{y - 1}-12"
        return f"{y:04d}-{mo - 1:02d}"
    if kind in ("pension", "sickness"):
        return f"{y:04d}-{mo:02d}"
    return None


def infer_vat_quarter_key(entry: Dict[str, Any]) -> Optional[str]:
    qk = _norm_quarter_key(entry.get("period_month"))
    if qk:
        return qk
    due = entry.get("due_date")
    if not isinstance(due, str) or not _DATE.match(due):
        return None
    y, mo, d = map(int, due.split("-"))
    if d != 25:
        return None
    if mo == 4:
        return f"{y}-Q1"
    if mo == 7:
        return f"{y}-Q2"
    if mo == 10:
        return f"{y}-Q3"
    if mo == 1:
        return f"{y - 1}-Q4"
    return None


def infer_schedule_key(entry: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    kind = str(entry.get("kind") or "")
    if kind == "vat_summary":
        iq = infer_vat_quarter_key(entry)
        if iq:
            return ("vat_summary", iq)
        return None
    if kind in INSURANCE_KINDS:
        ip = infer_period_month(entry)
        if ip:
            return (kind, ip)
    return None


def _paid_schedule_slots(entries: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    for e in entries:
        if not e.get("paid_date"):
            continue
        sk = infer_schedule_key(e)
        if sk:
            out.add(sk)
    return out


def _manual_unpaid_schedule_slots(entries: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    for e in entries:
        if e.get("paid_date"):
            continue
        sk = infer_schedule_key(e)
        if sk:
            out.add(sk)
    return out


def _synthetic_expectations(
    meta: Dict[str, Any], today: str
) -> List[Dict[str, Any]]:
    osvc = meta.get("osvc_since")
    if not isinstance(osvc, str) or not _DATE.match(osvc):
        return []
    start_ym = osvc[:7]
    sick_raw = meta.get("sickness_from")
    sick_ym: Optional[str] = None
    if isinstance(sick_raw, str) and _DATE.match(sick_raw):
        sick_ym = sick_raw[:7]
    end_ym = _current_ym()
    months = _iter_months_inclusive(start_ym, end_ym)
    expect: List[Dict[str, Any]] = []
    for ym in months:
        kinds = ["health", "pension"]
        if sick_ym and ym >= sick_ym:
            kinds.append("sickness")
        for kind in kinds:
            due = due_date_for_insurance(kind, ym)
            amt = _preset_amount(kind)
            overdue = due < today
            expect.append(
                {
                    "id": None,
                    "synthetic": True,
                    "kind": kind,
                    "title": ym,
                    "period_month": ym,
                    "amount": amt,
                    "currency": "CZK",
                    "due_date": due,
                    "overdue": overdue,
                }
            )
    return expect


def _merge_unpaid_list(
    entries: List[Dict[str, Any]],
    meta: Dict[str, Any],
    today: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, float]], float, int, int]:
    paid_slots = _paid_schedule_slots(entries)
    manual_slots = _manual_unpaid_schedule_slots(entries)
    synthetics = _synthetic_expectations(meta, today)
    synthetic_kept: List[Dict[str, Any]] = []
    unpaid_by: Dict[str, Dict[str, float]] = {}
    total_unpaid_czk = 0.0
    overdue_n = 0

    for s in synthetics:
        k = s["kind"]
        ym = s["period_month"]
        if (k, ym) in paid_slots or (k, ym) in manual_slots:
            continue
        synthetic_kept.append(dict(s))
        amt = float(s.get("amount") or 0)
        ccy = str(s.get("currency") or "CZK").upper()
        if ccy not in unpaid_by:
            unpaid_by[ccy] = {}
        unpaid_by[ccy][k] = unpaid_by[ccy].get(k, 0.0) + amt
        if ccy == "CZK":
            total_unpaid_czk += amt
        if s.get("overdue"):
            overdue_n += 1

    merged: List[Dict[str, Any]] = list(synthetic_kept)

    for e in entries:
        if e.get("paid_date"):
            continue
        if str(e.get("kind") or "") == "vat_summary":
            continue
        ccy = str(e.get("currency") or "CZK").upper()
        kind = str(e.get("kind") or "other")
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        due = str(e.get("due_date") or "")
        overdue = bool(due and _DATE.match(due) and due < today)
        if overdue:
            overdue_n += 1
        if ccy not in unpaid_by:
            unpaid_by[ccy] = {}
        unpaid_by[ccy][kind] = unpaid_by[ccy].get(kind, 0.0) + amt
        if ccy == "CZK":
            total_unpaid_czk += amt
        merged.append(
            {
                "id": e.get("id"),
                "synthetic": False,
                "kind": kind,
                "title": e.get("title"),
                "amount": amt,
                "currency": ccy,
                "due_date": due if due else None,
                "period_month": normalize_period_field(e.get("period_month")),
                "overdue": overdue,
            }
        )

    merged.sort(
        key=lambda x: (
            x.get("due_date") or "9999-99-99",
            x.get("kind") or "",
            not x.get("synthetic"),
        )
    )
    return merged, unpaid_by, total_unpaid_czk, len(merged), overdue_n


def build_obligations_summary(output_dir: Path) -> Dict[str, Any]:
    doc = load_obligations_doc(output_dir)
    entries = doc["entries"]
    meta = doc["meta"]
    today = _today_calendar()
    paid_by: Dict[str, Dict[str, float]] = {}
    total_paid_czk = 0.0
    paid_n = 0
    for e in entries:
        if not e.get("paid_date"):
            continue
        paid_n += 1
        ccy = str(e.get("currency") or "CZK").upper()
        kind = str(e.get("kind") or "other")
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if ccy not in paid_by:
            paid_by[ccy] = {}
        paid_by[ccy][kind] = paid_by[ccy].get(kind, 0.0) + amt
        if ccy == "CZK":
            total_paid_czk += amt

    merged_unpaid, unpaid_by, total_unpaid_czk, unpaid_total_rows, overdue_n = (
        _merge_unpaid_list(entries, meta, today)
    )

    manual_unpaid_count = sum(1 for e in entries if not e.get("paid_date"))

    return {
        "as_of_date": today,
        "paid": {
            "by_kind_by_currency": paid_by,
            "total_paid_czk": total_paid_czk,
        },
        "unpaid": {
            "by_kind_by_currency": unpaid_by,
            "total_unpaid_czk": total_unpaid_czk,
            "items": merged_unpaid,
        },
        "counts": {
            "entries_total": len(entries),
            "paid": paid_n,
            "unpaid": unpaid_total_rows,
            "overdue_unpaid": overdue_n,
            "unpaid_manual_entries": manual_unpaid_count,
        },
    }


def persist_summary(output_dir: Path, summary: Dict[str, Any]) -> None:
    atomic_write_json(obligations_summary_file(output_dir), summary)


def write_summary_snapshot(output_dir: Path) -> None:
    persist_summary(output_dir, build_obligations_summary(output_dir))


def all_obligation_nav_months(output_dir: Path) -> set:
    found: set = set()
    for e in list_entries(output_dir):
        for key in ("due_date", "paid_date", "period_month"):
            v = e.get(key)
            if isinstance(v, str) and len(v) >= 7:
                ym = v[:7]
                if _ISO_YM.match(ym):
                    found.add(ym)
    osvc = get_meta(output_dir).get("osvc_since")
    if isinstance(osvc, str) and _DATE.match(osvc):
        found.update(_iter_months_inclusive(osvc[:7], _current_ym()))
    return found
