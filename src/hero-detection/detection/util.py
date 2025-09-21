"""Utility helpers for detection package."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def ts() -> float:
    """Return the current time in seconds since the epoch."""
    return time.time()


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write a JSON payload to a file atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


__all__ = ["ts", "atomic_write_json"]
