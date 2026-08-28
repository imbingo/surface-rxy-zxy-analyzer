"""Version metadata kept separate from UI and algorithms."""

import json
import sys
from pathlib import Path


APP_VERSION = "V4.5.0"
SOURCE_BASE_VERSION = "V3.9.3"


def _packaged_source_commit() -> str:
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])),
        Path(__file__).resolve().parents[1],
    ]
    for root in roots:
        path = root / "installer" / "generated_build_info.json"
        if not path.exists():
            continue
        try:
            value = str(json.loads(path.read_text(encoding="utf-8")).get("source_commit", "")).strip()
        except (OSError, ValueError, TypeError):
            continue
        if value:
            return value
    return "development"


SOURCE_COMMIT = _packaged_source_commit()

__version__ = APP_VERSION
