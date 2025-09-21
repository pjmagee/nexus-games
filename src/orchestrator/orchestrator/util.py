"""Utility functions (timestamp & atomic JSON) for orchestrator."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def ts() -> float:
    return time.time()


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)

__all__ = ["ts", "atomic_write_json"]
