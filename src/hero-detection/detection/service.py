"""Detection service with YOLOv11 integration.

Phase 1 upgrade: replace synthetic placeholder detection with optional real YOLO
inference (Ultralytics). If a model cannot be loaded (missing file or library),
the service transparently falls back to the prior synthetic behavior so that
downstream components are never blocked.

Environment variables:
        DETECTION_MODEL_PATH   Path to a custom YOLOv11 weights file (.pt). If not
                                                     set, attempts to load a default small model name
                                                     (e.g. "yolo11n.pt"). If load fails -> synthetic.
        DETECTION_DEVICE       "cpu" (default) or e.g. "cuda:0" when GPU available.
        DETECTION_CONF         Float confidence threshold (default: 0.25).
        DETECTION_IOU          Float IoU threshold (default: 0.45) (future use).
        FRAMES_DIR             Override frames directory (debug/testing).
        LOG_LEVEL              Logging verbosity (debug|info|warning|error|critical).

Sidecar schema v3:
{
    "version": 3,
    "frame": "00042",
    "ts": 1234567.89,
    "status": "active",
    "width": 2448,
    "height": 1488,
    "inference": {"model": "yolo11n.pt", "latency_ms": 42.7},
    "objects": [
        {"id":0,"class_id":3,"class":"Jaina","conf":0.87,
         "bbox":{"x":123,"y":456,"w":80,"h":92},
         "center":{"x":163,"y":502}}
    ],
    "camera": {"center_x":0.512,"center_y":0.441,"source":"hero-mean","count":8}
}

Fallback synthetic mode emits an identical shape with empty objects and
"camera.source" == "synthetic".
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Any, Optional, TextIO

from .util import ts, atomic_write_json  # type: ignore

# Optional heavy imports guarded to allow graceful fallback if missing
try:  # pragma: no cover - import side effects
    from ultralytics import YOLO  # type: ignore
    _HAS_YOLO = True
except (ImportError, OSError):  # pragma: no cover
    YOLO = None  # type: ignore
    _HAS_YOLO = False

try:  # pragma: no cover
    import cv2  # type: ignore
    _HAS_CV2 = True
except (ImportError, OSError):  # pragma: no cover
    cv2 = None  # type: ignore
    _HAS_CV2 = False

BASE_DIR = Path.cwd()
SESSIONS_DIR = BASE_DIR / "sessions" / "current"

# Allow override for testing / decoupled runs
FRAMES_DIR = Path(os.getenv("FRAMES_DIR", str(SESSIONS_DIR / "frames")))
STATE_DIR = SESSIONS_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
HEARTBEAT_PATH = STATE_DIR / "heartbeat_detection.json"
RUNNING = True
LOG_DIR = STATE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "detection.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").lower()
LOG_STDOUT = os.getenv("DETECTION_STDOUT", "0") in {"1", "true", "True"}
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


class JsonLogger:
    """Simple JSON logger with optional stdout mirroring."""


    def __init__(
        self, service_name: str, service_role: str, path: Path, level: str = "info"
    ):
        self.service_name = service_name
        self.service_role = service_role
        self.path = path
        self.level = level if level in _LEVEL_ORDER else "info"
        self._fh: Optional[TextIO] = None

    def _open(self) -> TextIO:
        if self._fh is None:
            self._fh = self.path.open("a", encoding="utf-8")
        return self._fh

    def _should(self, level: str) -> bool:
        return _LEVEL_ORDER[level] >= _LEVEL_ORDER[self.level]

    def _ts(self) -> str:
        # Use timezone-aware now() instead of deprecated utcnow(); ensures proper UTC ISO formatting
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def log(self, level: str, message: str, **fields: Any) -> None:
        """Log a message with optional structured fields."""
        level = level.lower()
        if level not in _LEVEL_ORDER:
            level = "info"
        if not self._should(level):
            return
        rec: Dict[str, Any] = {
            "@timestamp": self._ts(),
            "service.name": self.service_name,
            "service.role": self.service_role,
            "process.pid": os.getpid(),
            "log.level": level,
            "message": message,
        }
        rec.update(fields)
        try:
            fh = self._open()
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            fh.flush()
            if LOG_STDOUT:
                # Write compact single-line mirror for immediate user visibility
                print(json.dumps(rec, separators=(",", ":")))
        except OSError:
            return

    def debug(self, msg: str, **f: Any) -> None:
        """Log a debug message."""
        self.log("debug", msg, **f)

    def info(self, msg: str, **f: Any) -> None:
        """Log an info message."""
        self.log("info", msg, **f)

    def warning(self, msg: str, **f: Any) -> None:
        """Log a warning message."""
        self.log("warning", msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        """Log an error message."""
        self.log("error", msg, **f)


LOGGER = JsonLogger("detection", "detection", LOG_FILE, LOG_LEVEL)

MODEL_PATH = os.getenv("DETECTION_MODEL_PATH", "yolo11n.pt")
DEVICE = os.getenv("DETECTION_DEVICE", "cpu")
CONF_THRESHOLD = float(os.getenv("DETECTION_CONF", "0.25"))
IOU_THRESHOLD = float(os.getenv("DETECTION_IOU", "0.45"))  # reserved
ANNOTATE = os.getenv("DETECTION_ANNOTATE", "1") not in {"0", "false", "False"}


class DetectionBackend:
    """Encapsulates YOLO model loading & inference with graceful fallback.

    If YOLO or OpenCV is unavailable or model fails to load, ``enabled`` is
    False and ``infer`` returns an empty list quickly.
    """

    def __init__(self) -> None:
        self.enabled: bool = False
        self.model: Any = None
        self.model_name: str = "synthetic"
        self.classes: Dict[int, str] = {}
        self._attempt_load()

    def _attempt_load(self) -> None:
        if not (_HAS_YOLO and _HAS_CV2):
            LOGGER.warning(
                "backend.disabled", reason="missing_deps", has_yolo=_HAS_YOLO, has_cv2=_HAS_CV2
            )
            return
        try:
            self.model = YOLO(MODEL_PATH)  # type: ignore[call-arg]
            # names may be a dict[int,str]
            self.classes = getattr(self.model, "names", {}) or {}
            self.model_name = Path(MODEL_PATH).name
            self.enabled = True
            LOGGER.info(
                "backend.loaded", event_type="backend.loaded", model=self.model_name, conf=CONF_THRESHOLD, device=DEVICE
            )
        except Exception as e:  # pragma: no cover - runtime path
            LOGGER.error(
                "backend.load_failed", event_type="backend.load_failed", model=MODEL_PATH, error_type=type(e).__name__, error_message=str(e)
            )
            self.enabled = False

    def infer(self, frame_path: Path) -> Tuple[float, list[dict[str, Any]], tuple[int, int]]:
        """Run inference on the frame path.

        Returns (latency_ms, objects, (width,height)). Objects empty if disabled.
        """
        if not self.enabled:
            # Attempt lightweight dimension read via OpenCV if available else (0,0)
            width = height = 0
            if _HAS_CV2:
                img = cv2.imread(str(frame_path))  # type: ignore
                if img is not None:
                    height, width = img.shape[:2]
            return 0.0, [], (width, height)
        start = time.perf_counter()
        img = cv2.imread(str(frame_path))  # type: ignore
        if img is None:
            return 0.0, [], (0, 0)
        height, width = img.shape[:2]
        try:
            # Ultralytics models accept numpy arrays (BGR fine)
            results = self.model.predict(
                img, verbose=False, conf=CONF_THRESHOLD, device=DEVICE  # type: ignore[attr-defined]
            )
        except Exception as e:  # pragma: no cover
            LOGGER.error(
                "inference.error", event_type="inference.error", frame=frame_path.name, error_type=type(e).__name__, error_message=str(e)
            )
            return 0.0, [], (width, height)
        latency_ms = (time.perf_counter() - start) * 1000.0
        objects: list[dict[str, Any]] = []
        # results is an iterable (batch size 1)
        try:
            res0 = results[0]
            boxes = getattr(res0, "boxes", None)
            if boxes is not None and getattr(boxes, "xyxy", None) is not None:
                xyxy = boxes.xyxy.cpu().numpy()  # type: ignore
                confs = boxes.conf.cpu().numpy()  # type: ignore
                cls_ids = boxes.cls.cpu().numpy().astype(int)  # type: ignore
                for idx, (x1, y1, x2, y2) in enumerate(xyxy):
                    conf = float(confs[idx])
                    cls_id = int(cls_ids[idx])
                    w = max(0, float(x2 - x1))
                    h = max(0, float(y2 - y1))
                    cx = float(x1 + w / 2.0)
                    cy = float(y1 + h / 2.0)
                    objects.append(
                        {
                            "id": idx,
                            "class_id": cls_id,
                            "class": self.classes.get(cls_id, str(cls_id)),
                            "conf": round(conf, 4),
                            "bbox": {
                                "x": int(x1),
                                "y": int(y1),
                                "w": int(w),
                                "h": int(h),
                            },
                            "center": {"x": int(cx), "y": int(cy)},
                        }
                    )
        except Exception as e:  # pragma: no cover
            LOGGER.error(
                "inference.parse_error", event_type="inference.parse_error", frame=frame_path.name, error_type=type(e).__name__, error_message=str(e)
            )
            objects = []
        return latency_ms, objects, (width, height)


BACKEND = DetectionBackend()


# Frame count thresholds for synthetic status classification (still used)
LOADING_THRESHOLD = 5
ENDED_THRESHOLD = 120


def classify_status(processed: int) -> str:
    """Classify session status based on frame count."""
    if processed < LOADING_THRESHOLD:
        return "loading"
    if processed >= ENDED_THRESHOLD:
        return "ended"
    return "active"


def synthetic_camera_target(stem: str) -> Tuple[float, float]:
    """Produce deterministic pseudo camera center for fallback mode."""
    try:
        idx = int(stem)
    except ValueError:
        idx = 0
    x = 0.3 + ((idx * 37) % 100) / 250.0
    y = 0.3 + ((idx * 53) % 100) / 250.0
    return min(x, 0.7), min(y, 0.7)


@dataclass
class Stats:
    """Container for detection statistics."""

    processed: int = 0
    last_frame: str | None = None
    last_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    camera_x: float = 0.5
    camera_y: float = 0.5
    max_seen_index: int = -1  # highest numeric frame index observed


def write_heartbeat(stats: Stats) -> None:
    """Write detection service heartbeat JSON."""

    atomic_write_json(
        HEARTBEAT_PATH,
        {
            "service": "detection",
            "role": "detection",
            "ts": ts(),
            "processed": stats.processed,
            "last_frame": stats.last_frame,
            "status": classify_status(stats.processed),
            "version": 2,
            "model": BACKEND.model_name,
            "backend_enabled": BACKEND.enabled,
            "last_latency_ms": round(stats.last_latency_ms, 2),
            "avg_latency_ms": round(stats.avg_latency_ms, 2),
        },
    )


def process_frame(frame_path: Path, stats: Stats) -> None:
    """Process a single frame, updating stats and sidecar as needed."""

    stem = frame_path.stem
    sidecar = frame_path.with_suffix(".detections.json")
    existing_sidecar = sidecar.exists()
    objects: list[dict[str, Any]] = []
    width = height = 0
    latency_ms = 0.0
    status = classify_status(stats.processed)

    if existing_sidecar:
        # Load existing sidecar so we can still annotate if needed
        try:
            with sidecar.open("r", encoding="utf-8") as f:
                sc = json.load(f)
            if sc.get("version") == 3:
                objects = sc.get("objects", [])
                width = sc.get("width", 0)
                height = sc.get("height", 0)
                # camera_x/y remain untouched for continuity
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    else:
        latency_ms, objects, (width, height) = BACKEND.infer(frame_path)
        stats.last_latency_ms = latency_ms
        if latency_ms > 0:
            if stats.avg_latency_ms == 0:
                stats.avg_latency_ms = latency_ms
            else:
                stats.avg_latency_ms = stats.avg_latency_ms * 0.8 + latency_ms * 0.2

        if objects:
            sum_x = sum(o["center"]["x"] for o in objects)
            sum_y = sum(o["center"]["y"] for o in objects)
            stats.camera_x = sum_x / (len(objects) * max(width, 1))
            stats.camera_y = sum_y / (len(objects) * max(height, 1))
            camera_source = "hero-mean"
            camera_count = len(objects)
        else:
            if stats.processed == 0 and not BACKEND.enabled:
                sx, sy = synthetic_camera_target(stem)
                stats.camera_x, stats.camera_y = sx, sy
            camera_source = "synthetic" if not BACKEND.enabled else "fallback-prev"
            camera_count = 0

        payload = {
            "version": 3,
            "frame": stem,
            "ts": ts(),
            "status": status,
            "width": width,
            "height": height,
            "inference": {
                "model": BACKEND.model_name,
                "latency_ms": round(latency_ms, 2),
                "enabled": BACKEND.enabled,
            },
            "objects": objects,
            "camera": {
                "center_x": round(stats.camera_x, 4),
                "center_y": round(stats.camera_y, 4),
                "source": camera_source,
                "count": camera_count,
            },
        }
        tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp, sidecar)
        except OSError as e:
            LOGGER.error(
                "sidecar.write_failed",
                event_type="sidecar.write_failed",
                frame=stem,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return
        stats.processed += 1
        stats.last_frame = stem
        LOGGER.debug(
            "frame.processed",
            event_type="frame.processed",
            **{
                "frame.index": stem,
                "processed": stats.processed,
                "objects": len(objects),
                "latency_ms": round(latency_ms, 2),
            },
        )

    # Annotation (both new and existing sidecars)
    if ANNOTATE:
        anno_path = frame_path.parent / f"{frame_path.stem}.annotated.jpg"
        if anno_path.exists():
            return
        if not _HAS_CV2:
            LOGGER.debug(
                "annotation.skipped",
                event_type="annotation.skipped",
                frame=stem,
                reason="missing_opencv",
                backend_enabled=BACKEND.enabled,
            )
            return
        try:
            img = cv2.imread(str(frame_path))  # type: ignore
            if img is None:
                LOGGER.debug(
                    "annotation.read_failed",
                    event_type="annotation.read_failed",
                    frame=stem,
                )
                return
            if BACKEND.enabled and objects:
                for obj in objects:
                    x = int(obj.get("bbox", {}).get("x", 0))
                    y = int(obj.get("bbox", {}).get("y", 0))
                    w = int(obj.get("bbox", {}).get("w", 0))
                    h = int(obj.get("bbox", {}).get("h", 0))
                    cls_name = obj.get("class", "?")
                    conf = obj.get("conf", 0.0)
                    color = (0, 255, 0)
                    cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)  # type: ignore
                    label = f"{cls_name}:{conf:.2f}"
                    cv2.putText(img, label, (x, max(0, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)  # type: ignore
            else:
                status_txt = "NO DETECTIONS" if BACKEND.enabled else "BACKEND DISABLED"
                cv2.putText(
                    img,
                    status_txt,
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (50, 50, 200),
                    2,
                    cv2.LINE_AA,  # type: ignore
                )  # type: ignore
            # Use a temp filename that still ends with .jpg so OpenCV selects correct encoder.
            tmp_anno = anno_path.parent / f"{frame_path.stem}.annotated.tmp.jpg"
            success = cv2.imwrite(str(tmp_anno), img)  # type: ignore
            if not success:
                LOGGER.error(
                    "annotation.failed",
                    event_type="annotation.failed",
                    frame=stem,
                    error_type="EncodeError",
                    error_message="cv2.imwrite returned False",
                )
                try:
                    if tmp_anno.exists():
                        tmp_anno.unlink()
                except OSError:
                    pass
                return
            try:
                os.replace(tmp_anno, anno_path)
            except OSError as e:
                LOGGER.error(
                    "annotation.failed",
                    event_type="annotation.failed",
                    frame=stem,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                try:
                    if tmp_anno.exists():
                        tmp_anno.unlink()
                except OSError:
                    pass
                return
            LOGGER.debug(
                "frame.annotated",
                event_type="frame.annotated",
                frame=stem,
                annotated=str(anno_path.name),
                objects=len(objects),
                backend_enabled=BACKEND.enabled,
            )
        except (OSError, ValueError, RuntimeError) as e:  # pragma: no cover
            LOGGER.error(
                "annotation.failed",
                event_type="annotation.failed",
                frame=stem,
                error_type=type(e).__name__,
                error_message=str(e),
            )


def scan_existing(stats: Stats) -> None:
    """Scan existing frames in the directory, updating stats and sidecars as needed."""

    if not FRAMES_DIR.exists():
        LOGGER.warning(
            "frames.dir.missing",
            event_type="frames.dir.missing",
            path=str(FRAMES_DIR),
        )
        return
    frames = sorted(FRAMES_DIR.glob("*.bmp"))
    if not frames:
        LOGGER.info(
            "frames.empty",
            event_type="frames.empty",
            path=str(FRAMES_DIR),
        )
    for frame in frames:
        # Update max_seen_index for incremental loop optimization
        try:
            idx = int(frame.stem)
            if idx > stats.max_seen_index:
                stats.max_seen_index = idx
        except ValueError:
            pass
        process_frame(frame, stats)
    LOGGER.info(
        "initial.scan.complete",
        event_type="initial.scan",
        processed=stats.processed,
        last_frame=stats.last_frame,
    )


def handle_signal(_signum, _frame):  # type: ignore[override]
    """Handle termination signals."""
    global RUNNING  # noqa: PLW0603 (explicit global for shutdown flag)
    RUNNING = False


def main(poll_interval: float = 0.5) -> int:
    """Main detection loop."""

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    stats = Stats()
    last_hb = 0.0
    scan_existing(stats)
    write_heartbeat(stats)
    run_once = os.getenv("DETECTION_RUN_ONCE", "0") in {"1", "true", "True"}
    if run_once:
        # In run-once mode we process any remaining frames not yet having a sidecar then exit.
        for frame in sorted(FRAMES_DIR.glob("*.bmp")):
            if not frame.with_suffix(".detections.json").exists():
                process_frame(frame, stats)
        write_heartbeat(stats)
        LOGGER.info(
            "run.once.complete",
            event_type="run.once.complete",
            processed=stats.processed,
            last_frame=stats.last_frame,
        )
        return 0
    while RUNNING:
        # Incremental approach: only consider frames with index > max_seen_index
        new_frames: list[Path] = []
        for frame in FRAMES_DIR.glob("*.bmp"):
            try:
                idx = int(frame.stem)
            except ValueError:
                idx = -1
            if idx > stats.max_seen_index:
                new_frames.append(frame)
        if new_frames:
            for nf in sorted(new_frames, key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
                try:
                    nf_idx = int(nf.stem)
                    if nf_idx > stats.max_seen_index:
                        stats.max_seen_index = nf_idx
                except ValueError:
                    pass
                if not nf.with_suffix(".detections.json").exists():
                    process_frame(nf, stats)
        else:
            # Fallback safety sweep (rare) for any earlier missed frames without sidecars
            for frame in FRAMES_DIR.glob("*.bmp"):
                side = frame.with_suffix(".detections.json")
                if not side.exists():
                    process_frame(frame, stats)
        now = ts()
        if now - last_hb >= 2.0:
            write_heartbeat(stats)
            LOGGER.debug(
                "heartbeat",
                event_type="heartbeat.summary",
                processed=stats.processed,
                last_frame=stats.last_frame,
            )
            last_hb = now
        time.sleep(poll_interval)
    write_heartbeat(stats)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
