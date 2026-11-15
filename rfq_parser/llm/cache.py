"""Disk cache for model responses: one JSON file per request, named by the request's hash.

The cache is what lets a run replay with no API key and makes a second run free.
The entries for the dev inbox are committed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "llm_cache"


def request_key(request: dict) -> str:
    """Hash the whole request, so a change to model, prompt, schema or input gives a new key."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, cache_dir: Path | None = None):
        self.dir = Path(cache_dir or os.environ.get("RFQ_LLM_CACHE_DIR") or DEFAULT_CACHE_DIR)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):  # a missing or corrupt entry is a cache miss
            return None

    def put(self, key: str, entry: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self._path(key))  # atomic, so a crash never leaves half an entry
