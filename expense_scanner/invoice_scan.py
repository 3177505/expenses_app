import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expense_scanner.extract import extract_text

_DEFAULT_INVOICES_DIR = (
    "/Users/tereznovak/Library/CloudStorage/Dropbox/_Documents/DE-Rechnung/2026"
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_INV_NO = re.compile(
    r"(?i)(?:Faktura|Invoice|Rechnung)\s*(?:No\.?|Nr\.?)\s*([\w\-.:]+)",
    re.MULTILINE,
)
_RE_PROJECT = re.compile(r"(?im)^(Projekt|Project):\s*(.+)$")
_RE_DATE_DMY = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_RE_ICO_LABEL = re.compile(r"(?i)IČO\s*:\s*(\d{8,10})")
_RE_IC_LABEL = re.compile(r"(?i)I\u010c\s*:\s*(\d{8,10})")
_RE_IC_ASCII_LINE = re.compile(r"(?im)^\s*IC\s*:\s*(\d{8,10})")
_RE_DIC = re.compile(r"(?i)DIČ\s*:\s*(CZ[\dA-Z]+|\d{8,10})")
_RE_VAT_LINE = re.compile(r"(?i)\bVAT\s*:\s*([A-Z]{2}[A-Z0-9][A-Z0-9+\-*]{0,20})")
_RE_PESEL = re.compile(r"(?i)PESEL\s*:\s*(\d{11})")
_IC_LABEL_LINESTART = re.compile(r"(?im)^\s*I\u010c\s*:")
_RE_TOTAL_CZK = re.compile(
    r"(?is)Celkem\D{0,40}?([\d\s\u00a0.,]+)\s*CZK",
)
_RE_TOTAL_EUR_GES = re.compile(
    r"(?is)Gesamtbetrag\s+brutto\D{0,40}?([\d\s\u00a0.,]+)\s*€",
)
_RE_TOTAL_EUR_SIMPLE = re.compile(
    r"(?is)(?:^|\n)Total\D{0,20}?([\d\s\u00a0.,]+)\s*€",
)


def _load_local_env() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_local_env()
_SUPPLIER_ICO = (os.environ.get("EXPENSE_SUPPLIER_ICO") or "").strip()
_SUPPLIER_VAT = (os.environ.get("EXPENSE_SUPPLIER_VAT") or "").strip()


def default_invoices_dir() -> str:
    env = (os.environ.get("EXPENSE_INVOICES_DIR") or "").strip()
    if env:
        return env
    return _DEFAULT_INVOICES_DIR


def file_id_for_path(path: Path) -> str:
    raw = str(path.resolve())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def country_hint_from_filename(name: str) -> str:
    m = re.search(r"_([A-Z]{2})_", name)
    if m:
        return m.group(1)
    return ""


def _customer_slice(text: str) -> str:
    markers = (
        "Tímto vystavuju",
        "I hereby invoice",
        "Hiermit stelle ich",
        "Hiermit stelle",
    )
    cut = len(text)
    for m in markers:
        i = text.find(m)
        if i >= 0:
            cut = min(cut, i)
    head = text[:cut]
    return head[:3500]


def _parse_amount(raw: str) -> Optional[float]:
    s = raw.strip()
    s = s.replace("\u00a0", " ").replace(" ", "")
    if not s:
        return None
    if s.count(",") == 1 and s.count(".") == 0:
        parts = s.split(",")
        if len(parts[1]) <= 2:
            s = parts[0] + "." + parts[1]
        else:
            s = s.replace(",", "")
    elif "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _first_amount_currency(text: str) -> Tuple[Optional[float], Optional[str]]:
    for rx, ccy in (
        (_RE_TOTAL_CZK, "CZK"),
        (_RE_TOTAL_EUR_GES, "EUR"),
        (_RE_TOTAL_EUR_SIMPLE, "EUR"),
    ):
        m = rx.search(text)
        if m:
            amt = _parse_amount(m.group(1))
            if amt is not None:
                return amt, ccy
    m2 = re.search(
        r"(?:^|\n)(?:Total|Gesamt)\D{0,30}?([\d\s\u00a0.,]+)\s*(?:€|EUR)\b",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if m2:
        amt = _parse_amount(m2.group(1))
        if amt is not None:
            return amt, "EUR"
    return None, None


def _invoice_date_iso(text: str) -> Optional[str]:
    head = "\n".join(text.splitlines()[:40])
    candidates: List[Tuple[str, int]] = []
    for m in _RE_DATE_DMY.finditer(head):
        d, mo, y = m.group(1), m.group(2), m.group(3)
        iso = f"{y}-{mo}-{d}"
        line_start = head.rfind("\n", 0, m.start()) + 1
        line = head[line_start : head.find("\n", m.start())].strip()
        prio = 0
        if re.match(r"^\d{2}\.\d{2}\.\d{4}$", line):
            prio = 3
        elif len(line) < 40:
            prio = 2
        candidates.append((iso, prio))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _client_name_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    skip = {
        "description",
        "popis",
        "total",
        "celkem",
        "amount",
        "invoice",
        "faktura",
        "rechnung",
    }
    for ln in lines[:20]:
        low = ln.lower()
        if low in skip or low.startswith("projekt") or low.startswith("project"):
            continue
        if ln.endswith(":") and len(ln) < 30:
            continue
        if _RE_DATE_DMY.fullmatch(ln):
            continue
        if re.match(r"^IČO\s*:", ln, re.I):
            continue
        if _IC_LABEL_LINESTART.match(ln):
            continue
        if re.match(r"^IC\s*:", ln, re.I):
            continue
        if len(ln) < 3:
            continue
        if ln[0].isdigit() and "," in ln:
            continue
        return ln
    return ""


def _extract_tax_ids(customer: str, _country_hint: str) -> Tuple[str, str, str]:
    client_dic = ""
    client_vat = ""
    client_ico = ""

    for rx in (_RE_ICO_LABEL, _RE_IC_LABEL, _RE_IC_ASCII_LINE):
        for m in rx.finditer(customer):
            val = m.group(1)
            if val == _SUPPLIER_ICO:
                continue
            client_ico = val
            break
        if client_ico:
            break

    dm = _RE_DIC.search(customer)
    if dm:
        v = dm.group(1).strip()
        vu = v.upper()
        if vu != _SUPPLIER_VAT.upper():
            if vu.startswith("CZ"):
                client_dic = v
            elif v.isdigit() and 8 <= len(v) <= 10:
                if not client_ico:
                    client_ico = v

    for m in _RE_VAT_LINE.finditer(customer):
        v = m.group(1).strip()
        vu = v.upper()
        if vu == _SUPPLIER_VAT.upper() or vu.startswith("CZ"):
            continue
        client_vat = v
        break

    pm = _RE_PESEL.search(customer)
    if pm and not client_vat:
        client_vat = pm.group(1)

    return client_dic, client_vat, client_ico


def parse_invoice_text(
    text: str, filename: str, country_hint: str
) -> Dict[str, Any]:
    customer = _customer_slice(text)
    inv_m = _RE_INV_NO.search(text)
    invoice_number = inv_m.group(1).strip() if inv_m else ""
    proj_m = _RE_PROJECT.search(text)
    for_who = proj_m.group(2).strip() if proj_m else ""
    inv_date = _invoice_date_iso(text)
    amount, currency = _first_amount_currency(text)
    client_name = _client_name_line(customer)
    client_dic, client_vat, client_ico = _extract_tax_ids(customer, country_hint)

    ch = (country_hint or "").upper()
    if ch == "CZ" and not client_ico and client_dic:
        client_ico = client_dic

    return {
        "invoice_number": invoice_number,
        "client_name": client_name,
        "for_who": for_who,
        "invoice_date": inv_date if inv_date and _ISO_DATE.match(inv_date or "") else None,
        "amount": amount,
        "currency": currency,
        "country_hint": ch,
        "client_dic": client_dic or None,
        "client_vat": client_vat or None,
        "client_ico": client_ico or None,
    }


def scan_pdf(path: Path) -> Dict[str, Any]:
    path = path.resolve()
    text = extract_text(path)
    fn = path.name
    hint = country_hint_from_filename(fn)
    parsed = parse_invoice_text(text, fn, hint)
    return {
        "id": file_id_for_path(path),
        "source_path": str(path),
        "file_name": fn,
        **parsed,
    }


def list_invoice_pdfs(invoices_dir: Path) -> List[Path]:
    if not invoices_dir.is_dir():
        return []
    out: List[Path] = []
    for p in sorted(invoices_dir.iterdir(), key=lambda x: x.name):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".pdf":
            continue
        if p.name.startswith("."):
            continue
        out.append(p.resolve())
    return out
