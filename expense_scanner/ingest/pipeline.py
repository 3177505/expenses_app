import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from expense_scanner.ingest.extract import extract_text
from expense_scanner.json_fs import atomic_write_json, load_json
from expense_scanner.store.merchant_rules import category_for_merchant, load_rules
from expense_scanner.ingest.parse import parse_receipt_text
from expense_scanner.store.receipt_edit import list_month_data_files, recalc_totals

SKIP_DIR_NAMES = {
    "output",
    "_notreadable",
    "processed",
    ".venv",
    "__pycache__",
    "expense_scanner",
    ".git",
}
RECEIPT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp", ".tif", ".tiff"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_dest(dest_dir: Path, name: str) -> Path:
    base = Path(name).name
    target = dest_dir / base
    if not target.exists():
        return target
    stem = Path(base).stem
    suf = Path(base).suffix
    for i in range(1, 10_000):
        cand = dest_dir / f"{stem}_{i}{suf}"
        if not cand.exists():
            return cand
    raise RuntimeError("could not allocate unique name")


def _ledger_key_for_digest(files_map: Dict[str, Any], digest: str) -> Optional[str]:
    for k, v in files_map.items():
        if isinstance(v, dict) and v.get("sha256") == digest:
            return k
    return None


def _path_is_under_dir(path: Path, dir_path: Path) -> bool:
    try:
        path.resolve().relative_to(dir_path.resolve())
        return True
    except ValueError:
        return False


def _patch_source_rel_in_outputs(
    output_dir: Path, digest: str, new_rel: str
) -> None:
    rid = digest[:16]
    for jpath in list_month_data_files(output_dir):
        data = load_json(jpath)
        changed = False
        for r in data.get("receipts") or []:
            if r.get("sha256") == digest or r.get("id") == rid:
                if r.get("source_rel") != new_rel:
                    r["source_rel"] = new_rel
                    changed = True
        if changed:
            atomic_write_json(jpath, data)


def collect_receipt_files(input_dir: Path, recursive: bool) -> List[Path]:
    input_dir = input_dir.resolve()
    found: List[Path] = []
    if recursive:
        for p in sorted(input_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(input_dir)
            if any(part.lower() in SKIP_DIR_NAMES for part in rel.parts[:-1]):
                continue
            if p.suffix.lower() in RECEIPT_SUFFIXES:
                found.append(p)
    else:
        for p in sorted(input_dir.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() in RECEIPT_SUFFIXES:
                found.append(p)
    return found


def relocate_unreadable(path: Path, not_readable_dir: Path, reason: str) -> Path:
    not_readable_dir.mkdir(parents=True, exist_ok=True)
    meta_dir = not_readable_dir / "_reasons"
    meta_dir.mkdir(exist_ok=True)
    target = _unique_dest(not_readable_dir, path.name)
    shutil.move(str(path), str(target))
    side = meta_dir / (target.stem + target.suffix + ".txt")
    side.write_text(f"{path.name}\n{reason}\n", encoding="utf-8")
    return target


def process_inbox(
    input_dir: Path,
    output_dir: Path,
    not_readable_dir: Optional[Path] = None,
    *,
    recursive: bool = False,
    ocr_lang: str = "deu+eng+ces",
) -> Dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    not_readable_dir = (not_readable_dir or (input_dir / "_notReadable")).resolve()
    ledger_path = output_dir / "_ledger.json"
    ledger = load_json(ledger_path)
    files_map: Dict[str, Any] = ledger.setdefault("files", {})
    merchant_rules = load_rules(output_dir)

    summary = {
        "processed": 0,
        "skipped": 0,
        "relocated": 0,
        "removed_duplicate_inbox": 0,
        "quarantined": 0,
        "errors": [],
    }

    for path in collect_receipt_files(input_dir, recursive):
        rel = str(path.relative_to(input_dir))
        try:
            digest = _sha256_file(path)
            ledger_key = _ledger_key_for_digest(files_map, digest)
            if ledger_key is not None:
                proc_root = (input_dir / "processed").resolve()
                if _path_is_under_dir(path, proc_root):
                    summary["skipped"] += 1
                    continue
                ent = files_map.get(ledger_key)
                if not isinstance(ent, dict):
                    summary["skipped"] += 1
                    continue
                ym = str(ent.get("month") or "unknown")
                dest_dir = (input_dir / "processed" / ym).resolve()
                dest_dir.mkdir(parents=True, exist_ok=True)
                primary = dest_dir / path.name
                if primary.exists() and _sha256_file(primary) == digest:
                    path.unlink()
                    canon_rel = str(primary.relative_to(input_dir))
                    if ledger_key != canon_rel:
                        files_map.pop(ledger_key, None)
                        files_map[canon_rel] = ent
                        ent["updated_at"] = datetime.now(timezone.utc).isoformat()
                        _patch_source_rel_in_outputs(output_dir, digest, canon_rel)
                        atomic_write_json(ledger_path, ledger)
                    summary["removed_duplicate_inbox"] += 1
                    continue
                dest_path = _unique_dest(dest_dir, path.name)
                shutil.move(str(path), str(dest_path))
                new_rel = str(dest_path.relative_to(input_dir))
                if ledger_key != new_rel:
                    files_map.pop(ledger_key, None)
                    files_map[new_rel] = ent
                    ent["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _patch_source_rel_in_outputs(output_dir, digest, new_rel)
                    atomic_write_json(ledger_path, ledger)
                summary["relocated"] += 1
                continue

            text = extract_text(path, ocr_lang=ocr_lang)
            if not text or len(text.strip()) < 20:
                relocate_unreadable(path, not_readable_dir, "text_too_short_or_empty")
                summary["quarantined"] += 1
                continue

            parsed = parse_receipt_text(text)
            if parsed.receipt_date is not None:
                ym = f"{parsed.receipt_date.year:04d}-{parsed.receipt_date.month:02d}"
            else:
                ym = "unknown"
            month_path = output_dir / f"{ym}.json"
            month = load_json(month_path)
            month["year_month"] = None if ym == "unknown" else ym
            month.setdefault("receipts", [])
            rid = digest[:16]
            month["receipts"] = [r for r in month["receipts"] if r.get("id") != rid]
            proc_dir = (input_dir / "processed" / ym).resolve()
            proc_dir.mkdir(parents=True, exist_ok=True)
            dest_path = _unique_dest(proc_dir, path.name)
            shutil.move(str(path), str(dest_path))
            new_rel = str(dest_path.relative_to(input_dir))

            cat_init = category_for_merchant(merchant_rules, parsed.merchant_hint)
            entry = {
                "id": rid,
                "source_file": path.name,
                "source_rel": new_rel,
                "date": parsed.receipt_date.isoformat() if parsed.receipt_date else None,
                "total": parsed.total,
                "currency": parsed.currency,
                "merchant_hint": parsed.merchant_hint,
                "sha256": digest,
                "category": cat_init,
                "category_note": None,
            }
            if parsed.vat_amount is not None:
                entry["vat_amount"] = round(float(parsed.vat_amount), 2)
            if parsed.vat_rate is not None:
                entry["vat_rate"] = float(parsed.vat_rate)
            month["receipts"].append(entry)
            recalc_totals(month)

            atomic_write_json(month_path, month)

            files_map.pop(rel, None)
            files_map[new_rel] = {
                "sha256": digest,
                "month": ym,
                "id": rid,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_json(ledger_path, ledger)
            summary["processed"] += 1
        except Exception as e:
            try:
                relocate_unreadable(path, not_readable_dir, repr(e))
                summary["quarantined"] += 1
            except Exception as e2:
                summary["errors"].append({"file": rel, "error": repr(e2)})
    return summary
