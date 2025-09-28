"""Integration-style tests for generating detection sidecars over existing frames.

These tests purposely invoke the same logic as the running detection service but
without the perpetual loop – we call `process_frame` directly for each BMP in
`sessions/current/frames` and assert a valid sidecar (schema v3) is created.

Assumptions:
  * Tests are executed from repository root or any working directory – they
    rely on relative paths to find `sessions/current/frames`.
  * Frames already exist (produced by capture). If none exist, the test is
    skipped gracefully.

Notes:
  * We reuse the service module; to avoid the background loop executing, we
    only import needed symbols.
  * YOLO backend may or may not be enabled depending on environment; we assert
    shape not semantic content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Import minimal public pieces from detection service
from detection import service  # type: ignore

FRAMES_DIR = Path(
    os.getenv("FRAMES_DIR", Path.cwd() / "sessions" / "current" / "frames")
)


@pytest.fixture(scope="module")
def detection_ctx():
    config = service.build_service_config()
    return service.build_runtime_context(config)


def _collect_frames(limit: int | None = None):
    frames = sorted(FRAMES_DIR.glob("*.bmp"))
    if limit is not None:
        frames = frames[:limit]
    return frames


def test_frames_directory_exists():
    if not FRAMES_DIR.exists():  # pragma: no cover - runtime / environment dependent
        pytest.skip("frames directory missing – no capture run yet")
    assert FRAMES_DIR.is_dir()


def test_generate_sidecars_for_all_frames(detection_ctx, tmp_path: Path):
    frames = _collect_frames()
    if not frames:
        pytest.skip("no frames present – run capture first")

    # Use a fresh Stats instance to avoid cross-run accumulation
    stats = service.Stats()

    created = 0
    for frame in frames:
        sidecar = frame.with_suffix(".detections.json")
        # Remove if exists to force regeneration (only for test isolation)
        if sidecar.exists():
            sidecar.unlink()
        service.process_frame(detection_ctx, frame, stats)
        assert sidecar.exists(), f"Sidecar missing for {frame.name}"
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Basic schema assertions
        assert data.get("version") == 3
        assert data.get("frame") == frame.stem
        assert "camera" in data
        assert "inference" in data
        inf = data["inference"]
        assert {"model", "latency_ms", "enabled"}.issubset(inf.keys())
        created += 1

    assert created == len(frames)


def test_idempotent_processing(detection_ctx):
    frames = _collect_frames(limit=5)
    if not frames:
        pytest.skip("no frames present – run capture first")
    stats = service.Stats()
    # First pass
    for frame in frames:
        service.process_frame(detection_ctx, frame, stats)
    mtimes_first = {
        f.with_suffix(".detections.json"): f.with_suffix(".detections.json")
        .stat()
        .st_mtime
        for f in frames
    }
    # Second pass (should be no modification since sidecars exist)
    for frame in frames:
        service.process_frame(detection_ctx, frame, stats)
    mtimes_second = {p: p.stat().st_mtime for p in mtimes_first.keys()}
    assert (
        mtimes_first == mtimes_second
    ), "Sidecar files modified on second processing pass"
    assert (
        mtimes_first == mtimes_second
    ), "Sidecar files modified on second processing pass"
