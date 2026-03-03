from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DataStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save({"users": [], "resumes": [], "companies": [], "jobs": [], "matches": [], "variants": [], "events": []})

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2))
