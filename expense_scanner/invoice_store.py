import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
) -> Dict[str, Any]:
    state = load_income_state(output_dir)
    slot: Dict[str, Any] = dict(state["by_id"].get(row_id) or {})
    slot["paid"] = bool(paid)
    pm = (paid_month or "").strip()
    slot["paid_month"] = pm if pm else None
    pd = (payment_date or "").strip()
    slot["payment_date"] = pd if pd and _ISO_DATE.match(pd) else None
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
    pay_d = row.get("payment_date")
    inv_d = row.get("invoice_date")
    vd: Optional[str] = None
    if isinstance(pay_d, str):
        s = pay_d.strip()
        if _ISO_DATE.match(s):
            vd = s
    if vd is None and isinstance(inv_d, str):
        s = inv_d.strip()
        if _ISO_DATE.match(s):
            vd = s
    if ccy == "CZK":
        row["amount_czk"] = round(float(t), 2)
        row["cnb_valuation_date"] = vd
        return
    if vd is None:
        return
    czk = foreign_to_czk(float(t), ccy, vd)
    if czk is not None:
        row["amount_czk"] = czk
        row["cnb_valuation_date"] = vd


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
    return {
        "invoices_dir": str(base),
        "rows": rows,
        "file_count": len(paths),
    }


def resolve_path_for_id(output_dir: Path, row_id: str) -> Optional[Path]:
    state = load_income_state(output_dir)
    base = Path(state["invoices_dir"])
    for path in list_invoice_pdfs(base):
        if file_id_for_path(path) == row_id:
            return path
    return None
