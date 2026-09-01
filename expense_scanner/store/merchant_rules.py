import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expense_scanner.tax.categories import is_valid_category_id, normalize_category
from expense_scanner.json_fs import atomic_write_json, load_json

RULES_FILE = "merchant_category_rules.json"


def rules_path(output_dir: Path) -> Path:
    return output_dir / RULES_FILE


def normalize_merchant_key(hint: Optional[str]) -> str:
    if not hint or not str(hint).strip():
        return ""
    t = unicodedata.normalize("NFKC", str(hint))
    t = t.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def load_rules(output_dir: Path) -> Dict[str, str]:
    path = rules_path(output_dir)
    data = load_json(path)
    raw = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        nk = normalize_merchant_key(k)
        if not nk:
            continue
        cat = normalize_category(v)
        if cat and is_valid_category_id(cat):
            out[nk] = cat
    return out


def _persist_rules(path: Path, rules: Dict[str, str]) -> None:
    atomic_write_json(path, {"rules": dict(sorted(rules.items()))})


def save_rules(output_dir: Path, rules: Dict[str, str]) -> None:
    clean: Dict[str, str] = {}
    for k, v in rules.items():
        nk = normalize_merchant_key(k)
        if not nk:
            continue
        cat = normalize_category(v)
        if cat and is_valid_category_id(cat):
            clean[nk] = cat
    _persist_rules(rules_path(output_dir), clean)


def set_merchant_rule(
    output_dir: Path, merchant_hint: Optional[str], category_id: str
) -> Dict[str, Any]:
    cat = normalize_category(category_id)
    if not cat or not is_valid_category_id(cat):
        raise ValueError("unknown category id")
    key = normalize_merchant_key(merchant_hint)
    if not key:
        raise ValueError("empty merchant hint")
    rules = load_rules(output_dir)
    rules[key] = cat
    save_rules(output_dir, rules)
    return {"ok": True, "key": key, "category": cat}


def delete_merchant_rule(output_dir: Path, merchant_key: str) -> Dict[str, Any]:
    key = normalize_merchant_key(merchant_key)
    if not key:
        return {"ok": False, "removed": False}
    rules = load_rules(output_dir)
    if key not in rules:
        return {"ok": True, "removed": False}
    del rules[key]
    save_rules(output_dir, rules)
    return {"ok": True, "removed": True}


def category_for_merchant(
    rules: Dict[str, str], merchant_hint: Optional[str]
) -> Optional[str]:
    key = normalize_merchant_key(merchant_hint)
    if not key:
        return None
    exact = rules.get(key)
    if exact:
        return exact
    matches: List[Tuple[int, str]] = []
    for rk, cat in rules.items():
        if len(rk) < 4:
            continue
        if rk in key or key in rk:
            matches.append((len(rk), cat))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def list_rules_public(output_dir: Path) -> Dict[str, Any]:
    return {"rules": load_rules(output_dir)}


def apply_rules_to_uncategorized(output_dir: Path) -> Dict[str, Any]:
    from expense_scanner.store.receipt_edit import list_month_data_files, recalc_totals

    rules = load_rules(output_dir)
    if not rules:
        return {"updated_files": 0, "updated_receipts": 0}
    touched_files = 0
    touched_receipts = 0
    for path in list_month_data_files(output_dir):
        data = load_json(path)
        changed = False
        for r in data.get("receipts") or []:
            c = r.get("category")
            if c is not None and str(c).strip() != "":
                continue
            cat = category_for_merchant(rules, r.get("merchant_hint"))
            if cat:
                r["category"] = cat
                changed = True
                touched_receipts += 1
        if changed:
            recalc_totals(data)
            atomic_write_json(path, data)
            touched_files += 1
    return {"updated_files": touched_files, "updated_receipts": touched_receipts}
