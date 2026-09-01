from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from expense_scanner.json_fs import atomic_write_json, load_json


def notes_path(output_dir: Path) -> Path:
    return output_dir / "landing_notes.json"


def get_notes(output_dir: Path) -> Dict[str, Any]:
    data = load_json(notes_path(output_dir))
    text = data.get("text")
    if not isinstance(text, str):
        text = ""
    return {"text": text, "updated_at": data.get("updated_at")}


def set_notes(output_dir: Path, text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "text": text,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(notes_path(output_dir), out)
    return out
