from pathlib import Path
from typing import Any, Dict, List, Tuple

from expense_scanner.obligations_store import build_obligations_summary, get_meta


def _kvd_due_iso(year: int, quarter: int) -> str:
    if quarter == 1:
        return f"{year}-04-25"
    if quarter == 2:
        return f"{year}-07-25"
    if quarter == 3:
        return f"{year}-10-25"
    return f"{year + 1}-01-25"


def _date_to_quarter(iso_date: str) -> Tuple[int, int]:
    y, m, _ = map(int, iso_date.split("-"))
    q = (m - 1) // 3 + 1
    return y, q


def _earliest_open_quarter(iso_today: str) -> Tuple[int, int]:
    y, q = _date_to_quarter(iso_today)
    while True:
        if q > 1:
            py, pq = y, q - 1
        else:
            py, pq = y - 1, 4
        due = _kvd_due_iso(py, pq)
        if due < iso_today:
            break
        y, q = py, pq
    return y, q


def _forward_quarters_from(y: int, q: int, count: int) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    cy, cq = y, q
    for _ in range(count):
        out.append((cy, cq))
        if cq == 4:
            cq = 1
            cy += 1
        else:
            cq += 1
    return out


def _slot_status(due: str, today: str) -> str:
    if due < today:
        return "past_due"
    if due == today:
        return "due_today"
    return "upcoming"


def build_reminder_overview(output_dir: Path) -> Dict[str, Any]:
    summary = build_obligations_summary(output_dir)
    meta = get_meta(output_dir)
    today = str(summary.get("as_of_date") or "")
    if len(today) != 10:
        today = summary.get("as_of_date", "")

    vat_on = bool(meta.get("vat_identified"))
    vat_from = meta.get("vat_identified_from")
    cy = int(today[:4]) if len(today) >= 4 else 0

    kvd_rows: List[Dict[str, Any]] = []
    if vat_on:
        vf = (
            vat_from[:10]
            if isinstance(vat_from, str) and len(vat_from) >= 10
            else today
        )
        yvf, qvf = _date_to_quarter(vf)
        y0, q0 = _earliest_open_quarter(today)
        if (y0, q0) < (yvf, qvf):
            y0, q0 = yvf, qvf
        for y, q in _forward_quarters_from(y0, q0, 8):
            if (y, q) < (yvf, qvf):
                continue
            if y > cy:
                break
            if y != cy:
                continue
            q_start_m = (q - 1) * 3 + 1
            due = _kvd_due_iso(y, q)
            kvd_rows.append(
                {
                    "period_key": f"{y}-Q{q}",
                    "calendar_quarter": q,
                    "year": y,
                    "period_month_from": f"{y:04d}-{q_start_m:02d}",
                    "due_date": due,
                    "status": _slot_status(due, today),
                }
            )
    else:
        for y in (cy,):
            for q in range(1, 5):
                q_start_m = (q - 1) * 3 + 1
                due = _kvd_due_iso(y, q)
                kvd_rows.append(
                    {
                        "period_key": f"{y}-Q{q}",
                        "calendar_quarter": q,
                        "year": y,
                        "period_month_from": f"{y:04d}-{q_start_m:02d}",
                        "due_date": due,
                        "status": _slot_status(due, today),
                    }
                )

    unpaid = summary.get("unpaid") or {}
    raw_items = list(unpaid.get("items") or [])

    def _due_key(row: Dict[str, Any]) -> str:
        d = row.get("due_date")
        return str(d) if d else "9999-99-99"

    raw_items.sort(
        key=lambda r: (
            0 if r.get("overdue") else 1,
            _due_key(r),
            str(r.get("kind") or ""),
        )
    )

    obligation_reminders: List[Dict[str, Any]] = []
    for it in raw_items:
        due = it.get("due_date")
        if not due:
            continue
        obligation_reminders.append(
            {
                "kind": str(it.get("kind") or ""),
                "title": it.get("title"),
                "period_month": it.get("period_month"),
                "due_date": due,
                "overdue": bool(it.get("overdue")),
                "synthetic": bool(it.get("synthetic")),
                "amount": it.get("amount"),
                "currency": it.get("currency") or "CZK",
            }
        )

    return {
        "as_of_date": today,
        "vat_identified": vat_on,
        "vat_identified_from": vat_from,
        "kvd_rows": kvd_rows,
        "obligation_reminders": obligation_reminders,
        "counts": summary.get("counts") or {},
    }
