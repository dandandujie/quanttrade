
import json
from pathlib import Path

MARKS_PATH = Path(__file__).resolve().parent.parent / "data" / "manual_marks.json"


def load_marks() -> dict:
    if not MARKS_PATH.exists():
        return {}
    try:
        return json.loads(MARKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_marks(marks: dict) -> None:
    MARKS_PATH.parent.mkdir(exist_ok=True)
    MARKS_PATH.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")


def excluded_for(day: str) -> set[str]:
    return set(load_marks().get(day, {}).get("exclude", []))
