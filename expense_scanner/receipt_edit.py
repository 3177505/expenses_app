import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expense_scanner.categories import is_valid_category_id, normalize_category
from expense_scanner.json_fs import atomic_write_json, load_json

LEDGER_NAME = "_ledger.json"
_NON_MONTH_JSON = frozenset(
    {
        "_ledger.json",
        "landing_notes.json",
        "merchant_category_rules.json",
        "travel_allowances.json",
        "income_invoices.json",
        "obligations.json",
        "obligations_summary.json",
        "tax_rc_review.json",
        "income_quarterly_foreign.json",
    }
)
_DATE_ISO_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def list_month_data_files(output_dir: Path) -> List[Path]:
    return sorted(
        p for p in output_dir.glob("*.json") if p.name.lower() not in _NON_MONTH_JSON
    )


def recalc_totals(data: Dict[str, Any]) -> None:
    totals: Dict[str, float] = {}
    vat_totals: Dict[str, float] = {}
    by_cat: Dict[str, Dict[str, float]] = {}
    for r in data.get("receipts") or []:
        c = (r.get("currency") or "UNK").upper()
        t = r.get("total")
        va = r.get("vat_amount")
        raw_cat = r.get("category")
        if raw_cat is None or (isinstance(raw_cat, str) and raw_cat.strip() == ""):
            cat_key = "_uncategorized"
        else:
            cat_key = str(raw_cat)
        if isinstance(t, (int, float)):
            fv = float(t)
            totals[c] = totals.get(c, 0.0) + fv
            slot = by_cat.setdefault(cat_key, {})
            slot[c] = slot.get(c, 0.0) + fv
        if isinstance(va, (int, float)):
            fv_v = float(va)
            vat_totals[c] = vat_totals.get(c, 0.0) + fv_v
    data["totals_by_currency"] = {k: round(v, 2) for k, v in sorted(totals.items())}
    data["vat_totals_by_currency"] = {
        k: round(v, 2) for k, v in sorted(vat_totals.items())
    }
    data["totals_by_category"] = {
        ck: {cc: round(vv, 2) for cc, vv in sorted(cv.items())}
        for ck, cv in sorted(by_cat.items())
    }
    data["generated_at"] = datetime.now(timezone.utc).isoformat()


def list_incomplete_receipts(output_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        for r in data.get("receipts") or []:
            missing: List[str] = []
            if r.get("date") is None:
                missing.append("date")
            if r.get("total") is None:
                missing.append("total")
            if r.get("currency") is None:
                missing.append("currency")
            if not missing:
                continue
            out.append(
                {
                    "id": r.get("id"),
                    "source_file": r.get("source_file"),
                    "source_rel": r.get("source_rel"),
                    "bucket_file": path.name,
                    "missing": missing,
                    "receipt": r,
                }
            )
    return out


def list_uncategorized_receipts(output_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        for r in data.get("receipts") or []:
            c = r.get("category")
            if c is not None and str(c).strip() != "":
                continue
            out.append(
                {
                    "id": r.get("id"),
                    "source_file": r.get("source_file"),
                    "source_rel": r.get("source_rel"),
                    "bucket_file": path.name,
                    "receipt": r,
                }
            )
    return out


def find_duplicate_receipt_groups(output_dir: Path) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, float, str], List[Dict[str, Any]]] = defaultdict(list)
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        for r in data.get("receipts") or []:
            d = r.get("date")
            t = r.get("total")
            if d is None or t is None:
                continue
            cur = (r.get("currency") or "UNK").upper()
            try:
                tr = round(float(t), 2)
            except (TypeError, ValueError):
                continue
            k = (str(d), tr, cur)
            buckets[k].append(
                {
                    "id": r.get("id"),
                    "bucket_file": path.name,
                    "receipt": r,
                }
            )
    out: List[Dict[str, Any]] = []
    for (d, total, cur), items in sorted(buckets.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
        if len(items) >= 2:
            out.append(
                {
                    "date": d,
                    "total": total,
                    "currency": cur,
                    "items": items,
                }
            )
    return out


def remove_receipts_by_ids(
    output_dir: Path,
    ledger_path: Path,
    remove_ids: List[str],
) -> Dict[str, Any]:
    if not remove_ids:
        return {"removed": [], "missing": []}
    ledger = load_json(ledger_path)
    files: Dict[str, Any] = ledger.setdefault("files", {})
    removed: List[str] = []
    missing: List[str] = []
    for rid in remove_ids:
        found = find_receipt(output_dir, rid)
        if not found:
            missing.append(rid)
            continue
        path, data, idx = found
        rec = data["receipts"][idx]
        rel = str(rec.get("source_rel") or rec.get("source_file") or "")
        data["receipts"].pop(idx)
        recalc_totals(data)
        atomic_write_json(path, data)
        if rel and rel in files:
            files.pop(rel)
        removed.append(rid)
    atomic_write_json(ledger_path, ledger)
    return {"removed": removed, "missing": missing}


def find_receipt(
    output_dir: Path, rid: str
) -> Optional[Tuple[Path, Dict[str, Any], int]]:
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        receipts = data.get("receipts") or []
        for i, r in enumerate(receipts):
            if r.get("id") == rid:
                return path, data, i
    return None


def _target_path_for_date(output_dir: Path, date_val: Optional[str]) -> Path:
    if not date_val:
        return output_dir / "unknown.json"
    if not _DATE_ISO_FULL.match(date_val):
        raise ValueError("date must be YYYY-MM-DD")
    y, m, _ = date_val.split("-")
    return output_dir / f"{y}-{m}.json"


def _default_month_payload(stem: str) -> Dict[str, Any]:
    if stem == "unknown":
        return {
            "year_month": None,
            "receipts": [],
            "totals_by_currency": {},
            "vat_totals_by_currency": {},
        }
    return {
        "year_month": stem,
        "receipts": [],
        "totals_by_currency": {},
        "vat_totals_by_currency": {},
    }


def _ledger_month_from_date(date_val: Optional[str]) -> str:
    if not date_val:
        return "unknown"
    y, m, _ = date_val.split("-")
    return f"{y}-{m}"


def _unique_dest_dir(dest_dir: Path, name: str) -> Path:
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


def _processed_bucket_from_rel(rel: str) -> Optional[str]:
    parts = Path(rel).parts
    if len(parts) >= 2 and parts[0] == "processed":
        return parts[1]
    return None


def maybe_relocate_to_processed_month(
    inbox_root: Path, rec: Dict[str, Any], target_ym: str
) -> None:
    rel = rec.get("source_rel") or rec.get("source_file")
    if not rel:
        return
    rel_s = str(rel)
    cur = _processed_bucket_from_rel(rel_s)
    if cur == target_ym:
        return
    try:
        src = safe_inbox_file(inbox_root, rel_s)
    except ValueError:
        return
    if not src.is_file():
        return
    base = inbox_root.resolve()
    dest_dir = (base / "processed" / target_ym).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest_dir(dest_dir, src.name)
    shutil.move(str(src), str(dest))
    rec["source_rel"] = str(dest.relative_to(base))


def apply_receipt_update(
    output_dir: Path,
    ledger_path: Path,
    inbox_root: Path,
    rid: str,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    found = find_receipt(output_dir, rid)
    if not found:
        raise KeyError(f"receipt not found: {rid}")

    src_path, src_data, idx = found
    rec = src_data["receipts"][idx]
    old_rel = str(rec.get("source_rel") or rec.get("source_file") or "")

    if "date" in patch:
        date = patch["date"]
        if date is None or (isinstance(date, str) and date.strip() == ""):
            rec["date"] = None
        else:
            if not _DATE_ISO_FULL.match(date):
                raise ValueError("date must be YYYY-MM-DD")
            rec["date"] = date

    if "payment_date" in patch:
        pd = patch["payment_date"]
        if pd is None or (isinstance(pd, str) and pd.strip() == ""):
            rec.pop("payment_date", None)
        else:
            if not isinstance(pd, str) or not _DATE_ISO_FULL.match(pd.strip()):
                raise ValueError("payment_date must be YYYY-MM-DD")
            rec["payment_date"] = pd.strip()

    if "total" in patch:
        t = patch["total"]
        if t is None:
            rec["total"] = None
        else:
            try:
                rec["total"] = round(float(t), 2)
            except (TypeError, ValueError):
                raise ValueError("total must be a number") from None

    if "currency" in patch:
        currency = patch["currency"]
        rec["currency"] = (
            currency.strip().upper()
            if currency and str(currency).strip()
            else None
        )

    if "vat_amount" in patch:
        va = patch["vat_amount"]
        if va is None:
            rec.pop("vat_amount", None)
        else:
            try:
                fv = float(va)
            except (TypeError, ValueError):
                raise ValueError("vat_amount must be a number") from None
            if fv < 0:
                raise ValueError("vat_amount must be >= 0")
            if fv == 0:
                rec.pop("vat_amount", None)
            else:
                rec["vat_amount"] = round(fv, 2)

    if "vat_rate" in patch:
        vr = patch["vat_rate"]
        if vr is None:
            rec.pop("vat_rate", None)
        else:
            try:
                rv = float(vr)
            except (TypeError, ValueError):
                raise ValueError("vat_rate must be a number") from None
            if rv < 0 or rv > 100:
                raise ValueError("vat_rate must be between 0 and 100")
            if rv == 0:
                rec.pop("vat_rate", None)
            else:
                rec["vat_rate"] = rv

    if "category" in patch:
        cat = normalize_category(patch["category"])
        if not is_valid_category_id(cat):
            raise ValueError("unknown category id")
        rec["category"] = cat

    if "category_note" in patch:
        n = patch["category_note"]
        rec["category_note"] = (
            str(n).strip() if n is not None and str(n).strip() != "" else None
        )

    if "merchant_hint" in patch:
        mh = patch["merchant_hint"]
        if mh is None or (isinstance(mh, str) and mh.strip() == ""):
            rec["merchant_hint"] = None
        else:
            rec["merchant_hint"] = str(mh).strip()

    maybe_relocate_to_processed_month(
        inbox_root, rec, _ledger_month_from_date(rec.get("date"))
    )

    dst_path = _target_path_for_date(output_dir, rec.get("date"))

    if src_path.resolve() != dst_path.resolve():
        src_data["receipts"].pop(idx)
        recalc_totals(src_data)
        atomic_write_json(src_path, src_data)

        dst_data = load_json(dst_path)
        if not dst_data or not isinstance(dst_data.get("receipts"), list):
            dst_data = _default_month_payload(dst_path.stem)
        dst_data.setdefault("receipts", [])
        dst_data["receipts"] = [x for x in dst_data["receipts"] if x.get("id") != rid]
        dst_data["receipts"].append(rec)
        if dst_path.stem == "unknown":
            dst_data["year_month"] = None
        else:
            dst_data["year_month"] = dst_path.stem
        recalc_totals(dst_data)
        atomic_write_json(dst_path, dst_data)
    else:
        recalc_totals(src_data)
        atomic_write_json(src_path, src_data)

    new_rel = str(rec.get("source_rel") or rec.get("source_file") or "")
    ledger = load_json(ledger_path)
    files = ledger.setdefault("files", {})
    if old_rel and old_rel != new_rel and old_rel in files:
        ent = files.pop(old_rel)
    else:
        ent = files.get(new_rel, {})
    if not isinstance(ent, dict):
        ent = {}
    ent = {
        "sha256": rec.get("sha256") or ent.get("sha256", ""),
        "month": _ledger_month_from_date(rec.get("date")),
        "id": rec.get("id") or ent.get("id", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if new_rel:
        files[new_rel] = ent
    atomic_write_json(ledger_path, ledger)

    if patch.get("remember_for_merchant"):
        try:
            from expense_scanner.merchant_rules import (
                apply_rules_to_uncategorized,
                set_merchant_rule,
            )

            mh = rec.get("merchant_hint")
            cat = rec.get("category")
            if mh and cat and str(cat).strip():
                set_merchant_rule(output_dir, str(mh), str(cat))
                apply_rules_to_uncategorized(output_dir)
        except ValueError:
            pass

    return {"ok": True, "bucket_file": dst_path.name, "receipt": rec}


def safe_inbox_file(inbox_root: Path, source_rel: str) -> Path:
    if not source_rel:
        raise ValueError("invalid source_rel")
    rel = Path(source_rel)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("invalid source_rel")
    base = inbox_root.resolve()
    candidate = (base / rel).resolve()
    candidate.relative_to(base)
    return candidate
