import calendar
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expense_scanner.cnb_fx import foreign_to_czk
from expense_scanner.invoice_scan import (
    default_invoices_dir,
    file_id_for_path,
    list_invoice_pdfs,
    scan_pdf,
)
from expense_scanner.json_fs import atomic_write_json, load_json

_STATE_NAME = "income_invoices.json"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_PAID_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_UNSET: Any = object()


def _norm_income_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    t = str(value).strip()
    return t if t else None


def _year_month_from_paid_month(pm: str) -> Optional[Tuple[int, int]]:
    z = pm.strip()
    mo = _RE_PAID_MONTH.match(z)
    if not mo:
        return None
    y, m = int(mo.group(1)), int(mo.group(2))
    if y and 1 <= m <= 12:
        return (y, m)
    return None


def _year_month_from_payment_date(pd: str) -> Optional[Tuple[int, int]]:
    z = pd.strip()
    if not _ISO_DATE.match(z):
        return None
    y, m = int(z[0:4]), int(z[5:7])
    if 1 <= m <= 12:
        return (y, m)
    return None


def _last_day_of_month_iso(y: int, m: int) -> str:
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-{last:02d}"


def _income_valuation_date_for_paid(row: Dict[str, Any]) -> Optional[str]:
    pd = row.get("payment_date")
    if isinstance(pd, str):
        s = pd.strip()
        if _ISO_DATE.match(s):
            return s
    pm = row.get("paid_month")
    if isinstance(pm, str):
        ym = _year_month_from_paid_month(pm)
        if ym:
            y, m = ym
            return _last_day_of_month_iso(y, m)
    return None


def income_state_path(output_dir: Path) -> Path:
    return output_dir / _STATE_NAME


def _empty_state() -> Dict[str, Any]:
    return {
        "invoices_dir": default_invoices_dir(),
        "by_id": {},
        "updated_at": None,
    }


def load_income_state(output_dir: Path) -> Dict[str, Any]:
    data = load_json(income_state_path(output_dir))
    if not isinstance(data, dict):
        return _empty_state()
    by_id = data.get("by_id")
    if not isinstance(by_id, dict):
        by_id = {}
    ddir = data.get("invoices_dir")
    if not isinstance(ddir, str) or not ddir.strip():
        ddir = default_invoices_dir()
    return {
        "invoices_dir": ddir.strip(),
        "by_id": by_id,
        "updated_at": data.get("updated_at"),
    }


def save_income_state(output_dir: Path, state: Dict[str, Any]) -> None:
    atomic_write_json(
        income_state_path(output_dir),
        {
            "invoices_dir": state["invoices_dir"],
            "by_id": state["by_id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def set_invoices_dir(output_dir: Path, invoices_dir: str) -> str:
    p = Path(invoices_dir).expanduser().resolve()
    if not p.is_dir():
        raise ValueError("invoices_dir is not a directory")
    state = load_income_state(output_dir)
    state["invoices_dir"] = str(p)
    save_income_state(output_dir, state)
    return str(p)


def patch_row(
    output_dir: Path,
    row_id: str,
    paid: bool,
    paid_month: Optional[str],
    payment_date: Optional[str],
    *,
    client_dic: Any = _UNSET,
    client_vat: Any = _UNSET,
) -> Dict[str, Any]:
    state = load_income_state(output_dir)
    slot: Dict[str, Any] = dict(state["by_id"].get(row_id) or {})
    slot["paid"] = bool(paid)
    pm = (paid_month or "").strip()
    slot["paid_month"] = pm if pm else None
    pd = (payment_date or "").strip()
    slot["payment_date"] = pd if pd and _ISO_DATE.match(pd) else None
    if client_dic is not _UNSET:
        d = _norm_income_text(client_dic)
        if d is None:
            slot.pop("client_dic", None)
        else:
            slot["client_dic"] = d
    if client_vat is not _UNSET:
        v = _norm_income_text(client_vat)
        if v is None:
            slot.pop("client_vat", None)
        else:
            slot["client_vat"] = v
    state["by_id"][row_id] = slot
    save_income_state(output_dir, state)
    return slot


def enrich_income_row_czk(row: Dict[str, Any]) -> None:
    row["amount_czk"] = None
    row["cnb_valuation_date"] = None
    if not row.get("paid"):
        return
    t = row.get("amount")
    ccy = (row.get("currency") or "").strip().upper()
    if not isinstance(t, (int, float)) or not ccy or ccy == "UNK":
        return
    vd = _income_valuation_date_for_paid(row)
    if ccy == "CZK":
        row["amount_czk"] = round(float(t), 2)
        row["cnb_valuation_date"] = vd
        return
    if vd is None:
        return
    czk = foreign_to_czk(float(t), ccy, vd)
    row["cnb_valuation_date"] = vd
    if czk is not None:
        row["amount_czk"] = czk


def _is_cz_income_row(row: Dict[str, Any]) -> bool:
    return (str(row.get("country_hint") or "").strip().upper() == "CZ")


def _income_invoice_month(row: Dict[str, Any]) -> Optional[str]:
    inv = row.get("invoice_date")
    if isinstance(inv, str) and len(inv) >= 7 and inv[4] == "-":
        ym = inv[:7]
        y, m = ym[:4], ym[5:7]
        if y.isdigit() and m.isdigit() and 1 <= int(m) <= 12:
            return ym
    return None


def is_kvd_income_row(row: Dict[str, Any]) -> bool:
    if _is_cz_income_row(row):
        return False
    vat = str(row.get("client_vat") or "").strip().upper()
    if not vat:
        return False
    if vat.startswith("CZ"):
        return False
    return True


def kvd_income_counts_by_month(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        if not is_kvd_income_row(row):
            continue
        ym = _income_invoice_month(row)
        if not ym:
            continue
        counts[ym] = counts.get(ym, 0) + 1
    return counts


def _iter_months_inclusive(start_ym: str, end_ym: str) -> List[str]:
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    ye, me = int(end_ym[:4]), int(end_ym[5:7])
    out: List[str] = []
    while (y, m) <= (ye, me):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            m = 1
            y += 1
        else:
            m += 1
    return out


def build_monthly_approx_summary(
    rows: List[Dict[str, Any]],
    expense_czk_by_month: Dict[str, float],
) -> Dict[str, Any]:
    paid_total = 0.0
    paid_n = 0
    activity: List[str] = []
    for row in rows:
        if row.get("paid"):
            czk = row.get("amount_czk")
            if isinstance(czk, (int, float)):
                paid_total += float(czk)
                paid_n += 1
            pm = row.get("paid_month")
            if isinstance(pm, str) and len(pm) >= 7 and pm[4] == "-":
                activity.append(pm[:7])
            else:
                ym = _income_invoice_month(row)
                if ym:
                    activity.append(ym)
        else:
            ym = _income_invoice_month(row)
            if ym:
                activity.append(ym)

    for ym in expense_czk_by_month:
        if len(ym) == 7 and ym[4] == "-":
            activity.append(ym)

    activity = sorted({ym for ym in activity if len(ym) == 7 and ym[4] == "-"})
    if not activity:
        return {
            "income_total_czk_paid": 0.0,
            "income_paid_count": 0,
            "month_count": 0,
            "avg_income_czk": 0.0,
            "expenses_total_czk": 0.0,
            "months": [],
        }

    months = _iter_months_inclusive(activity[0], activity[-1])
    n = len(months) or 1
    avg = round(paid_total / n, 2) if paid_total else 0.0
    exp_total = 0.0
    month_rows: List[Dict[str, Any]] = []
    for ym in months:
        exp = round(float(expense_czk_by_month.get(ym) or 0.0), 2)
        exp_total += exp
        month_rows.append(
            {
                "year_month": ym,
                "avg_income_czk": avg,
                "expenses_czk": exp,
                "approx_net_czk": round(avg - exp, 2),
            }
        )
    month_rows.reverse()
    return {
        "income_total_czk_paid": round(paid_total, 2),
        "income_paid_count": paid_n,
        "month_count": n,
        "avg_income_czk": avg,
        "expenses_total_czk": round(exp_total, 2),
        "approx_net_total_czk": round(avg * n - exp_total, 2),
        "months": month_rows,
    }


def _income_quarter_key(row: Dict[str, Any]) -> Tuple[int, int]:
    pm = row.get("paid_month")
    if isinstance(pm, str):
        ym = _year_month_from_paid_month(pm)
        if ym:
            y, m = ym
            return (y, (m - 1) // 3 + 1)
    pd = row.get("payment_date")
    if isinstance(pd, str):
        ym = _year_month_from_payment_date(pd)
        if ym:
            y, m = ym
            return (y, (m - 1) // 3 + 1)
    return (0, 0)


def _sort_income_quarter_sections(keys: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    def kf(t: Tuple[int, int]) -> Tuple[int, int, int]:
        y, q = t
        if y == 0:
            return (1, 0, 0)
        return (0, -y, -q)

    return sorted(keys, key=kf)


def build_quarterly_foreign_summary(
    rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    grid: Dict[Tuple[int, int], Dict[str, List[Dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if _is_cz_income_row(row):
            continue
        if not row.get("paid"):
            continue
        yq = _income_quarter_key(row)
        vat = str(row.get("client_vat") or "").strip()
        vk = vat.upper() if vat else "__NO_VAT__"
        grid[yq][vk].append(row)

    sections: List[Dict[str, Any]] = []
    for yq in _sort_income_quarter_sections(list(grid.keys())):
        y, q = yq
        vat_map = grid[yq]
        groups: List[Dict[str, Any]] = []
        for vk in sorted(
            vat_map.keys(), key=lambda x: (x == "__NO_VAT__", x)
        ):
            g_rows = vat_map[vk]
            names: List[str] = sorted(
                {
                    str(r.get("client_name") or "").strip()
                    for r in g_rows
                    if str(r.get("client_name") or "").strip()
                }
            )
            name_disp = ", ".join(names) if names else "—"
            totals_ccy: Dict[str, float] = {}
            total_czk = 0.0
            lines: List[Dict[str, Any]] = []
            for r in g_rows:
                ccy = (r.get("currency") or "").strip().upper()
                t = r.get("amount")
                if isinstance(t, (int, float)) and ccy and ccy != "UNK":
                    totals_ccy[ccy] = totals_ccy.get(ccy, 0.0) + float(t)
                czk = r.get("amount_czk")
                if isinstance(czk, (int, float)):
                    total_czk += float(czk)
                lines.append(
                    {
                        "id": r.get("id"),
                        "invoice_number": r.get("invoice_number"),
                        "client_name": r.get("client_name"),
                        "amount": float(t) if isinstance(t, (int, float)) else None,
                        "currency": ccy if ccy and ccy != "UNK" else None,
                        "amount_czk": round(float(czk), 2)
                        if isinstance(czk, (int, float))
                        else None,
                        "cnb_valuation_date": r.get("cnb_valuation_date"),
                        "payment_date": r.get("payment_date"),
                        "paid_month": r.get("paid_month"),
                    }
                )
            for kc in list(totals_ccy.keys()):
                totals_ccy[kc] = round(totals_ccy[kc], 2)
            lines.sort(
                key=lambda x: (
                    str(x.get("payment_date") or "") or str(x.get("paid_month") or ""),
                    str(x.get("invoice_number") or ""),
                )
            )
            vat_out = None if vk == "__NO_VAT__" else vk
            groups.append(
                {
                    "client_vat": vat_out,
                    "client_name": name_disp,
                    "invoice_count": len(g_rows),
                    "totals_by_currency": {k: totals_ccy[k] for k in sorted(totals_ccy.keys())},
                    "total_czk": round(total_czk, 2) if total_czk else 0.0,
                    "has_czk_gap": any(
                        x.get("amount_czk") is None for x in lines
                    ),
                    "lines": lines,
                }
            )
        q_total = round(sum(g["total_czk"] for g in groups), 2)
        sections.append(
            {
                "year": y,
                "quarter": q,
                "unassigned": y == 0,
                "groups": groups,
                "quarter_total_czk": q_total,
            }
        )
    return {"sections": sections}


def _sort_key(row: Dict[str, Any]) -> tuple:
    d = row.get("invoice_date") or ""
    if not isinstance(d, str):
        d = ""
    return (d, row.get("file_name") or "")


def list_income_rows(output_dir: Path) -> Dict[str, Any]:
    state = load_income_state(output_dir)
    base = Path(state["invoices_dir"])
    rows: List[Dict[str, Any]] = []
    paths = list_invoice_pdfs(base)
    for path in paths:
        try:
            scraped = scan_pdf(path)
        except Exception as exc:
            rid = file_id_for_path(path)
            scraped = {
                "id": rid,
                "source_path": str(path.resolve()),
                "file_name": path.name,
                "invoice_number": None,
                "client_name": None,
                "for_who": None,
                "invoice_date": None,
                "amount": None,
                "currency": None,
                "country_hint": "",
                "client_dic": None,
                "client_vat": None,
                "client_ico": None,
                "scan_error": str(exc)[:300],
            }
        rid = scraped["id"]
        extra = state["by_id"].get(rid) or {}
        if "client_dic" in extra:
            scraped["client_dic"] = extra.get("client_dic")
        if "client_vat" in extra:
            scraped["client_vat"] = extra.get("client_vat")
        scraped["paid"] = bool(extra.get("paid")) if extra.get("paid") is not None else False
        pm = extra.get("paid_month")
        scraped["paid_month"] = pm if isinstance(pm, str) and pm.strip() else None
        pd = extra.get("payment_date")
        if isinstance(pd, str) and pd.strip() and _ISO_DATE.match(pd.strip()):
            scraped["payment_date"] = pd.strip()
        else:
            scraped["payment_date"] = None
        enrich_income_row_czk(scraped)
        rows.append(scraped)
    rows.sort(key=_sort_key, reverse=True)
    quarterly = build_quarterly_foreign_summary(rows)
    persist_quarterly_foreign_summary(output_dir, quarterly)
    return {
        "invoices_dir": str(base),
        "rows": rows,
        "file_count": len(paths),
        "quarterly_foreign": quarterly,
    }


def quarterly_foreign_summary_path(output_dir: Path) -> Path:
    return output_dir / "income_quarterly_foreign.json"


def persist_quarterly_foreign_summary(
    output_dir: Path, summary: Dict[str, Any]
) -> Dict[str, Any]:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "quarterly_foreign": summary,
    }
    atomic_write_json(quarterly_foreign_summary_path(output_dir), payload)
    return payload


def resolve_path_for_id(output_dir: Path, row_id: str) -> Optional[Path]:
    state = load_income_state(output_dir)
    base = Path(state["invoices_dir"])
    for path in list_invoice_pdfs(base):
        if file_id_for_path(path) == row_id:
            return path
    return None
