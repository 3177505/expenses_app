from pathlib import Path
from typing import Any, Dict, List, Optional

from expense_scanner.invoice_store import kvd_income_counts_by_month, list_income_rows
from expense_scanner.obligations_store import build_obligations_summary, get_meta


def _kvd_due_for_month(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    if m == 12:
        return f"{y + 1}-01-25"
    return f"{y}-{m + 1:02d}-25"


def _slot_status(due: str, today: str) -> str:
    if due < today:
        return "past_due"
    if due == today:
        return "due_today"
    return "upcoming"


def _ym_ok(ym: str) -> bool:
    if len(ym) != 7 or ym[4] != "-":
        return False
    try:
        y, m = int(ym[:4]), int(ym[5:7])
    except ValueError:
        return False
    return 1 <= m <= 12 and y >= 2000


def build_reminder_overview(
    output_dir: Path, inbox_root: Optional[Path] = None
) -> Dict[str, Any]:
    summary = build_obligations_summary(output_dir)
    meta = get_meta(output_dir)
    today = str(summary.get("as_of_date") or "")
    if len(today) != 10:
        today = summary.get("as_of_date", "")

    vat_on = bool(meta.get("vat_identified"))
    vat_from = meta.get("vat_identified_from")

    income_rows = list_income_rows(output_dir).get("rows") or []
    rc_by_month = kvd_income_counts_by_month(income_rows)

    kvd_rows: List[Dict[str, Any]] = []
    for ym in sorted(rc_by_month.keys()):
        if not _ym_ok(ym):
            continue
        if (
            vat_on
            and isinstance(vat_from, str)
            and len(vat_from) >= 7
            and ym < vat_from[:7]
        ):
            continue
        due = _kvd_due_for_month(ym)
        kvd_rows.append(
            {
                "period_key": ym,
                "period_month": ym,
                "period_month_from": ym,
                "rc_count": rc_by_month[ym],
                "due_date": due,
                "status": _slot_status(due, today) if len(today) == 10 else "upcoming",
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
        "kvd_monthly": True,
        "kvd_source": "income",
        "kvd_rows": kvd_rows,
        "obligation_reminders": obligation_reminders,
        "counts": summary.get("counts") or {},
    }
