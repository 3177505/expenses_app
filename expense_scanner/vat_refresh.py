from pathlib import Path
from typing import Any, Dict, List

from expense_scanner.extract import extract_text
from expense_scanner.json_fs import atomic_write_json, load_json
from expense_scanner.parse import parse_receipt_text
from expense_scanner.receipt_edit import (
    list_month_data_files,
    recalc_totals,
    safe_inbox_file,
)


def refresh_vat_from_source_files(
    output_dir: Path,
    inbox_root: Path,
    *,
    ocr_lang: str = "deu+eng+ces",
    force: bool = False,
) -> Dict[str, Any]:
    updated_files = 0
    updated_receipts = 0
    skipped_files = 0
    skipped_receipts = 0
    errors: List[str] = []

    for path in list_month_data_files(output_dir):
        data = load_json(path)
        recs = data.get("receipts")
        if not isinstance(recs, list):
            skipped_files += 1
            continue
        touched = False
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            rel = rec.get("source_rel") or rec.get("source_file")
            if not rel:
                skipped_receipts += 1
                continue
            need_vat = force or rec.get("vat_amount") is None
            need_rate = force or rec.get("vat_rate") is None
            if not need_vat and not need_rate:
                skipped_receipts += 1
                continue
            try:
                fpath = safe_inbox_file(inbox_root, str(rel))
            except ValueError:
                skipped_receipts += 1
                continue
            if not fpath.is_file():
                skipped_receipts += 1
                continue
            try:
                text = extract_text(fpath, ocr_lang)
                parsed = parse_receipt_text(text)
            except Exception as e:
                errors.append(f"{rel!s}: {e!r}")
                skipped_receipts += 1
                continue
            ch = False
            if need_vat and parsed.vat_amount is not None:
                rec["vat_amount"] = round(float(parsed.vat_amount), 2)
                ch = True
            if need_rate and parsed.vat_rate is not None:
                rec["vat_rate"] = float(parsed.vat_rate)
                ch = True
            if ch:
                updated_receipts += 1
                touched = True
            else:
                skipped_receipts += 1
        if touched:
            recalc_totals(data)
            atomic_write_json(path, data)
            updated_files += 1

    return {
        "updated_files": updated_files,
        "updated_receipts": updated_receipts,
        "skipped_receipts": skipped_receipts,
        "skipped_files": skipped_files,
        "errors": errors[:50],
    }
