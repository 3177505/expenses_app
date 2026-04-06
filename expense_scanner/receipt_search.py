import re
from pathlib import Path
from typing import Any, Dict, List

from expense_scanner.json_fs import load_json
from expense_scanner.receipt_edit import list_month_data_files


def _receipt_haystack(r: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "merchant_hint",
        "category",
        "category_note",
        "source_file",
        "source_rel",
        "id",
        "sha256",
        "currency",
        "payment_date",
        "vat_amount",
        "vat_rate",
    ):
        v = r.get(key)
        if v is not None and v != "":
            parts.append(str(v))
    for key in ("date", "total"):
        v = r.get(key)
        if v is not None and v != "":
            parts.append(str(v))
    return "\n".join(parts).lower()


def _query_tokens(q: str) -> List[str]:
    t = (q or "").strip().lower()
    if not t:
        return []
    return [x for x in re.split(r"\s+", t) if x]


def search_receipts(
    output_dir: Path, query: str, *, limit: int = 200
) -> List[Dict[str, Any]]:
    tokens = _query_tokens(query)
    if not tokens:
        return []
    lim = max(1, min(int(limit), 500))
    out: List[Dict[str, Any]] = []
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        for r in data.get("receipts") or []:
            hay = _receipt_haystack(r)
            if not all(tok in hay for tok in tokens):
                continue
            rid = r.get("id")
            out.append(
                {
                    "id": rid,
                    "bucket_file": path.name,
                    "date": r.get("date"),
                    "total": r.get("total"),
                    "currency": r.get("currency"),
                    "vat_amount": r.get("vat_amount"),
                    "vat_rate": r.get("vat_rate"),
                    "merchant_hint": r.get("merchant_hint"),
                    "category": r.get("category"),
                    "source_file": r.get("source_file"),
                }
            )
            if len(out) >= lim:
                return out
    return out
