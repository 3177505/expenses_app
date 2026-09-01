import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expense_scanner.json_fs import atomic_write_json, load_json
from expense_scanner.store.receipt_edit import list_month_data_files, safe_inbox_file

_STATE_NAME = "tax_rc_review.json"

_PDF_KEYWORD_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"reverse[-\s]?charge", re.I), "reverse charge"),
    (re.compile(r"abzugsverfahren", re.I), "DE Abzugsverfahren"),
    (re.compile(r"umsatzsteuerschuld\s+liegt\s+beim", re.I), "DE USt-Schuld Kunde"),
    (re.compile(r"steuerschuld.*kunden", re.I), "DE Steuerschuld Kunde"),
    (re.compile(r"art\.\s*44", re.I), "Art. 44 EU"),
    (re.compile(r"196\s+ec|2006/112", re.I), "EU 2006/112"),
    (re.compile(r"p[řr]enesen[íi]\s+da[nň]", re.I), "CS přenesení"),
    (re.compile(r"re[žz]im\s+p[řr]enes", re.I), "CS režim"),
]

def _pdf_text_head(path: Path, max_pages: int = 2) -> str:
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return ""
    try:
        import fitz
    except Exception:
        return ""
    try:
        doc = fitz.open(path)
        try:
            parts: List[str] = []
            n = min(max_pages, len(doc))
            for i in range(n):
                parts.append(str(doc[i].get_text() or ""))
            return "\n".join(parts)
        finally:
            doc.close()
    except Exception:
        return ""


def _keywords_in_text(t: str) -> List[str]:
    if not t or len(t) < 10:
        return []
    out: List[str] = []
    for rx, label in _PDF_KEYWORD_PATTERNS:
        if rx.search(t):
            out.append(label)
    return out


def _total_positive(r: Dict[str, Any]) -> bool:
    t = r.get("total")
    return isinstance(t, (int, float)) and float(t) > 0.0


def _review_state_path(output_dir: Path) -> Path:
    return output_dir / _STATE_NAME


def load_dismissed_receipt_ids(output_dir: Path) -> set:
    data = load_json(_review_state_path(output_dir))
    if not isinstance(data, dict):
        return set()
    raw = data.get("dismissed_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x}


def set_dismissed_receipt_id(
    output_dir: Path, receipt_id: str, dismissed: bool
) -> set:
    rid = (receipt_id or "").strip()
    if not rid:
        raise ValueError("receipt_id required")
    s = load_dismissed_receipt_ids(output_dir)
    if dismissed:
        s.add(rid)
    else:
        s.discard(rid)
    atomic_write_json(
        _review_state_path(output_dir),
        {"dismissed_ids": sorted(s)},
    )
    return s


def _row_from_receipt(
    r: Dict[str, Any],
    bucket_name: str,
    ym: str,
    fpath: Optional[Path],
    kw_labels: List[str],
) -> Dict[str, Any]:
    ccy = (r.get("currency") or "UNK").strip().upper()[:3]
    rid = r.get("id")
    rel = (r.get("source_rel") or r.get("source_file") or "").strip()
    return {
        "id": rid,
        "bucket_file": bucket_name,
        "year_month": ym,
        "date": r.get("date"),
        "total": r.get("total"),
        "currency": ccy,
        "vat_amount": r.get("vat_amount"),
        "vat_rate": r.get("vat_rate"),
        "merchant_hint": r.get("merchant_hint"),
        "source_file": r.get("source_file"),
        "source_rel": rel,
        "pdf_keywords_matched": kw_labels,
        "file_exists": bool(fpath and fpath.is_file()),
    }


def build_tax_rc_review(
    output_dir: Path, inbox_root: Path, *, scan_pdf: bool = True
) -> Dict[str, Any]:
    all_kw: List[Dict[str, Any]] = []
    for bucket in list_month_data_files(output_dir):
        data = load_json(bucket)
        ym = data.get("year_month") or bucket.stem
        for r in data.get("receipts") or []:
            if not isinstance(r, dict):
                continue
            if not _total_positive(r):
                continue
            rid = r.get("id")
            if not rid:
                continue
            rel = (r.get("source_rel") or r.get("source_file") or "").strip()
            if not rel:
                continue
            kw_labels: List[str] = []
            fpath: Optional[Path] = None
            if scan_pdf:
                try:
                    fpath = safe_inbox_file(inbox_root, rel)
                except ValueError:
                    fpath = None
                if fpath and fpath.suffix.lower() == ".pdf" and fpath.is_file():
                    head = _pdf_text_head(fpath)
                    kw_labels = _keywords_in_text(head)
            if not kw_labels:
                continue
            all_kw.append(
                _row_from_receipt(r, bucket.name, ym, fpath, kw_labels)
            )

    dismissed = load_dismissed_receipt_ids(output_dir)
    active = [x for x in all_kw if str(x.get("id")) not in dismissed]
    hidden = [x for x in all_kw if str(x.get("id")) in dismissed]

    def _sort_key(x: Dict[str, Any]) -> str:
        d = str(x.get("date") or "")
        if len(d) != 10:
            d = "0000-00-00"
        return f"{d}\t{x.get('id') or ''}"

    active.sort(key=_sort_key, reverse=True)
    hidden.sort(key=_sort_key, reverse=True)
    return {
        "rows": active,
        "dismissed_rows": hidden,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "scan_pdf": scan_pdf,
    }


def rc_invoice_counts_by_month(
    output_dir: Path, inbox_root: Path, *, scan_pdf: bool = True
) -> Dict[str, int]:
    data = build_tax_rc_review(output_dir, inbox_root, scan_pdf=scan_pdf)
    counts: Dict[str, int] = {}
    for row in data.get("rows") or []:
        d = row.get("date")
        ym: Optional[str] = None
        if isinstance(d, str) and len(d) >= 7 and d[4] == "-":
            ym = d[:7]
        else:
            raw = row.get("year_month")
            if isinstance(raw, str) and len(raw) >= 7:
                ym = raw[:7]
        if not ym:
            continue
        counts[ym] = counts.get(ym, 0) + 1
    return counts
