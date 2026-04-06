import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_bundle: Optional[Dict[str, Any]] = None


def load_tax_categories() -> Dict[str, Any]:
    global _bundle
    if _bundle is None:
        path = Path(__file__).with_name("cz_categories.json")
        _bundle = json.loads(path.read_text(encoding="utf-8"))
    return _bundle


def category_ids() -> List[str]:
    return [c["id"] for c in load_tax_categories()["categories"]]


def valid_category_ids() -> Set[str]:
    return set(category_ids())


def is_valid_category_id(value: Optional[str]) -> bool:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return True
    return value in valid_category_ids()


def normalize_category(value: Optional[str]) -> Optional[str]:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return value.strip()
