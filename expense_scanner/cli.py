import argparse
import json
from pathlib import Path

from expense_scanner.json_fs import atomic_write_json, load_json
from expense_scanner.ingest.pipeline import process_inbox
from expense_scanner.store.receipt_edit import list_month_data_files, recalc_totals
from expense_scanner.ingest.vat_refresh import refresh_vat_from_source_files


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan receipt files into monthly JSON.")
    ap.add_argument("--input", type=Path, default=Path("."))
    ap.add_argument("--output", type=Path, default=Path("output"))
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--ocr-lang", default="deu+eng+ces")
    ap.add_argument(
        "--refresh-aggregates",
        action="store_true",
        help="Recompute totals_by_currency and totals_by_category in all month JSON files.",
    )
    ap.add_argument(
        "--refresh-vat",
        action="store_true",
        help="Re-run OCR on receipt files and fill vat_amount and vat_rate where missing.",
    )
    ap.add_argument(
        "--refresh-vat-force",
        action="store_true",
        help="With --refresh-vat, overwrite existing VAT fields.",
    )
    args = ap.parse_args()
    if args.refresh_vat:
        summary = refresh_vat_from_source_files(
            args.output.resolve(),
            args.input.resolve(),
            ocr_lang=args.ocr_lang,
            force=args.refresh_vat_force,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return
    if args.refresh_aggregates:
        for path in list_month_data_files(args.output):
            data = load_json(path)
            if data.get("receipts"):
                recalc_totals(data)
                atomic_write_json(path, data)
        print(json.dumps({"refreshed": True}, indent=2))
        return
    summary = process_inbox(
        args.input,
        args.output,
        recursive=args.recursive,
        ocr_lang=args.ocr_lang,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
