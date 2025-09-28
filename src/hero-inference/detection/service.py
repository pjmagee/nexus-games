"""Detection service with YOLOv11 integration."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import signal
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, TextIO, Tuple

from .util import atomic_write_json, ts  # type: ignore

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

MODULE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = MODULE_ROOT.parent.parent if len(MODULE_ROOT.parents) >= 2 else MODULE_ROOT
# Use NEXUS_BASE_DIR if set, otherwise infer from module location
REPO_ROOT = (
    Path(os.getenv("NEXUS_BASE_DIR"))
    if os.getenv("NEXUS_BASE_DIR")
    else (SRC_ROOT.parent if len(MODULE_ROOT.parents) >= 3 else SRC_ROOT)
)

RUNNING = True
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
DEFAULT_CONFIG_PATH = MODULE_ROOT / "config" / "defaults.toml"
DEFAULT_CONFIG: Dict[str, Any] = {
    "paths": {
        "frames": "sessions/current/frames",
        "state": "sessions/current/state",
    },
    "detector": {
        "model": "src/hero-training/outputs/yolov12-long3/weights/best.pt",
        "device": "cpu",
        "confidence": 0.05,
        "iou": 0.45,
        "annotate": True,
        "crop_mode": "br-sixth",
    },
    "logging": {
        "level": "info",
        "stdout": False,
    },
}


@dataclass(frozen=True)
class PathsConfig:
    frames_dir: Path
    state_dir: Path
    detections_dir: Path
    annotated_dir: Path
    heartbeat_path: Path
    log_file: Path


@dataclass(frozen=True)
class DetectionSettings:
    """Container for runtime detection configuration."""

    model_path: Path
    device: str
    conf_threshold: float
    iou_threshold: float
    annotate: bool
    crop_mode: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: Path) -> "DetectionSettings":
        model_value = data.get("model", DEFAULT_CONFIG["detector"]["model"])
        model_path = (base_dir / model_value).resolve()
        return cls(
            model_path=model_path,
            device=str(data.get("device", "cpu")),
            conf_threshold=float(data.get("confidence", 0.05)),
            iou_threshold=float(data.get("iou", 0.45)),
            annotate=bool(data.get("annotate", True)),
            crop_mode=str(data.get("crop_mode", "br-sixth")).lower(),
        )


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    stdout: bool


@dataclass(frozen=True)
class ServiceConfig:
    paths: PathsConfig
    detection: DetectionSettings
    logging: LoggingConfig


@dataclass
class RuntimeContext:
    config: ServiceConfig
    logger: "JsonLogger"
    backend: "DetectionBackend"


class JsonLogger:
    """Simple JSON logger with optional stdout mirroring."""

    def __init__(
        self,
        service_name: str,
        service_role: str,
        path: Path,
        level: str = "info",
        stdout: bool = False,
    ):
        self.service_name = service_name
        self.service_role = service_role
        self.path = path
        self.level = level if level in _LEVEL_ORDER else "info"
        self.stdout = stdout
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
            if self.stdout:
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


def _deep_update(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _load_config_data(config_path: Optional[Path]) -> Dict[str, Any]:
    data = copy.deepcopy(DEFAULT_CONFIG)
    candidate = config_path or DEFAULT_CONFIG_PATH
    if candidate and candidate.exists():
        with candidate.open("rb") as fh:
            file_data = tomllib.load(fh)
        _deep_update(data, file_data)
    return data


def build_service_config(
    config_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> ServiceConfig:
    base_dir = (base_dir or REPO_ROOT).resolve()
    raw = _load_config_data(config_path)
    if overrides:
        _deep_update(raw, overrides)

    paths_section = raw.get("paths", {})
    frames_dir = (
        base_dir / paths_section.get("frames", DEFAULT_CONFIG["paths"]["frames"])
    ).resolve()
    state_dir = (
        base_dir / paths_section.get("state", DEFAULT_CONFIG["paths"]["state"])
    ).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    detections_dir = (state_dir / "detections").resolve()
    detections_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir = (state_dir / "annotated").resolve()
    annotated_dir.mkdir(parents=True, exist_ok=True)
    log_file = (state_dir / "logs" / "detection.log").resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path = state_dir / "heartbeat_detection.json"

    paths = PathsConfig(
        frames_dir=frames_dir,
        state_dir=state_dir,
        detections_dir=detections_dir,
        annotated_dir=annotated_dir,
        heartbeat_path=heartbeat_path,
        log_file=log_file,
    )

    detection_section = raw.get("detector", {})
    detection = DetectionSettings.from_dict(detection_section, base_dir)

    logging_section = raw.get("logging", {})
    logging = LoggingConfig(
        level=str(logging_section.get("level", "info")).lower(),
        stdout=bool(logging_section.get("stdout", False)),
    )

    return ServiceConfig(paths=paths, detection=detection, logging=logging)


def build_runtime_context(config: ServiceConfig) -> RuntimeContext:
    logger = JsonLogger(
        "detection",
        "detection",
        config.paths.log_file,
        level=config.logging.level,
        stdout=config.logging.stdout,
    )
    backend = DetectionBackend(config.detection, logger)
    return RuntimeContext(config=config, logger=logger, backend=backend)


class DetectionBackend:
    """Encapsulates YOLO model loading & inference with graceful fallback.

    If YOLO or OpenCV is unavailable or model fails to load, ``enabled`` is
    False and ``infer`` returns an empty list quickly.
    """

    def __init__(self, settings: DetectionSettings, logger: JsonLogger) -> None:
        self.enabled: bool = False
        self.model: Any = None
        self.model_name: str = "synthetic"
        self.model_path: Path = settings.model_path
        self.classes: Dict[int, str] = {}
        self.settings = settings
        self.crop_mode: str = settings.crop_mode
        self.last_region: Optional[Dict[str, int | str]] = None
        self.logger = logger
        self._attempt_load()

    def _attempt_load(self) -> None:
        if not (_HAS_YOLO and _HAS_CV2):
            self.logger.warning(
                "backend.disabled",
                reason="missing_deps",
                has_yolo=_HAS_YOLO,
                has_cv2=_HAS_CV2,
            )
            return
        model_path = self.settings.model_path
        if not model_path.exists():
            self.logger.error(
                "backend.load_failed",
                event_type="backend.load_failed",
                model=str(model_path),
                error_type="FileNotFoundError",
                error_message="model checkpoint not found",
            )
            self.enabled = False
            return
        try:
            self.model = YOLO(str(model_path))  # type: ignore[call-arg]
            # names may be a dict[int,str]
            self.classes = getattr(self.model, "names", {}) or {}
            self.model_name = model_path.name
            self.model_path = model_path
            self.enabled = True
            self.logger.info(
                "backend.loaded",
                event_type="backend.loaded",
                model=self.model_name,
                model_path=str(model_path),
                conf=self.settings.conf_threshold,
                device=self.settings.device,
            )
        except Exception as e:  # pragma: no cover - runtime path
            self.logger.error(
                "backend.load_failed",
                event_type="backend.load_failed",
                model=str(model_path),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.enabled = False

    def _extract_region(self, img: Any) -> tuple[Any, tuple[int, int], tuple[int, int]]:
        """Return cropped image, offset, and original dimensions based on crop mode."""

        if getattr(img, "shape", None) is None:
            return img, (0, 0), (0, 0)
        height, width = img.shape[:2]
        if width <= 0 or height <= 0:
            return img, (0, 0), (width, height)
        mode = self.crop_mode
        if mode in {"full", "none", "off"}:
            return img, (0, 0), (width, height)
        if mode in {"br-sixth", "bottom-right-sixth", "bottom_right_sixth"}:
            slice_w = max(width // 3, 1)
            slice_h = max(height // 2, 1)
            offset_x = width - slice_w
            offset_y = height - slice_h
            cropped = img[offset_y:height, offset_x:width]
            return cropped, (offset_x, offset_y), (width, height)
        # Fallback to full frame if mode unrecognised
        return img, (0, 0), (width, height)

    def infer(
        self, frame_path: Path
    ) -> Tuple[float, list[dict[str, Any]], tuple[int, int]]:
        """Run inference on the frame path.

        Returns (latency_ms, objects, (width,height)). Objects empty if disabled.
        """
        if not self.enabled:
            self.last_region = None
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
            self.last_region = None
            return 0.0, [], (0, 0)
        cropped, (offset_x, offset_y), (width, height) = self._extract_region(img)
        crop_h = cropped.shape[0] if getattr(cropped, "shape", None) is not None else 0
        crop_w = cropped.shape[1] if getattr(cropped, "shape", None) is not None else 0
        if crop_h == 0 or crop_w == 0:
            cropped = img
            offset_x = offset_y = 0
            height, width = img.shape[:2]
        try:
            # Ultralytics models accept numpy arrays (BGR fine)
            results = self.model.predict(
                cropped,
                verbose=False,
                conf=self.settings.conf_threshold,
                device=self.settings.device,
            )
        except Exception as e:  # pragma: no cover
            self.logger.error(
                "inference.error",
                event_type="inference.error",
                frame=frame_path.name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.last_region = None
            return 0.0, [], (width, height)
        latency_ms = (time.perf_counter() - start) * 1000.0
        objects: list[dict[str, Any]] = []
        self.last_region = {
            "mode": self.crop_mode,
            "offset_x": int(offset_x),
            "offset_y": int(offset_y),
            "width": int(crop_w if crop_w else width),
            "height": int(crop_h if crop_h else height),
        }
        # results is an iterable (batch size 1)
        try:
            res0 = results[0]
            boxes = getattr(res0, "boxes", None)
            if boxes is not None and getattr(boxes, "xyxy", None) is not None:
                xyxy = boxes.xyxy.cpu().numpy()  # type: ignore
                confs = boxes.conf.cpu().numpy()  # type: ignore
                cls_ids = boxes.cls.cpu().numpy().astype(int)  # type: ignore
                for idx, (x1_raw, y1_raw, x2_raw, y2_raw) in enumerate(xyxy):
                    conf = float(confs[idx])
                    cls_id = int(cls_ids[idx])
                    x1 = float(x1_raw + offset_x)
                    y1 = float(y1_raw + offset_y)
                    x2 = float(x2_raw + offset_x)
                    y2 = float(y2_raw + offset_y)
                    x1 = max(0.0, min(x1, float(width)))
                    y1 = max(0.0, min(y1, float(height)))
                    x2 = max(0.0, min(x2, float(width)))
                    y2 = max(0.0, min(y2, float(height)))
                    w = max(0.0, x2 - x1)
                    h = max(0.0, y2 - y1)
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
            self.logger.error(
                "inference.parse_error",
                event_type="inference.parse_error",
                frame=frame_path.name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.last_region = None
            objects = []
        return latency_ms, objects, (width, height)


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


def write_heartbeat(ctx: RuntimeContext, stats: Stats) -> None:
    """Write detection service heartbeat JSON."""

    backend = ctx.backend
    atomic_write_json(
        ctx.config.paths.heartbeat_path,
        {
            "service": "detection",
            "role": "detection",
            "ts": ts(),
            "processed": stats.processed,
            "last_frame": stats.last_frame,
            "status": classify_status(stats.processed),
            "version": 2,
            "model": backend.model_name,
            "model_path": str(backend.model_path),
            "backend_enabled": backend.enabled,
            "last_latency_ms": round(stats.last_latency_ms, 2),
            "avg_latency_ms": round(stats.avg_latency_ms, 2),
        },
    )


def process_frame(ctx: RuntimeContext, frame_path: Path, stats: Stats) -> None:
    """Process a single frame, updating stats and sidecar as needed."""

    backend = ctx.backend
    logger = ctx.logger
    detection_cfg = ctx.config.detection

    stem = frame_path.stem
    frame_sidecar = frame_path.with_suffix(".detections.json")
    state_sidecar = ctx.config.paths.detections_dir / f"{stem}.detections.json"
    state_annotated_dir = ctx.config.paths.annotated_dir

    objects: list[dict[str, Any]] = []
    width = height = 0
    latency_ms = 0.0
    status = classify_status(stats.processed)

    existing_state = state_sidecar.exists()
    legacy_sidecar_exists = frame_sidecar.exists()

    if existing_state or legacy_sidecar_exists:
        sidecar_path = state_sidecar if existing_state else frame_sidecar
        sc: Dict[str, Any] | None = None
        try:
            with sidecar_path.open("r", encoding="utf-8") as f:
                sc = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            sc = None
        else:
            if not existing_state and sc is not None:
                try:
                    atomic_write_json(state_sidecar, sc)
                except OSError as e:
                    logger.error(
                        "state_sidecar.write_failed",
                        event_type="state_sidecar.write_failed",
                        frame=stem,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
            if sc is not None:
                objects = sc.get("objects", [])
                width = sc.get("width", 0)
                height = sc.get("height", 0)
                status = sc.get("status", status)
                camera = sc.get("camera", {})
                stats.camera_x = float(camera.get("center_x", stats.camera_x))
                stats.camera_y = float(camera.get("center_y", stats.camera_y))
                stats.last_frame = sc.get("frame", stem)
                inference_info = sc.get("inference", {})
                stats.last_latency_ms = float(
                    inference_info.get("latency_ms", stats.last_latency_ms)
                )
                if stats.avg_latency_ms == 0 and stats.last_latency_ms:
                    stats.avg_latency_ms = stats.last_latency_ms
        if legacy_sidecar_exists:
            try:
                frame_sidecar.unlink()
            except OSError:
                pass
    else:
        latency_ms, objects, (width, height) = backend.infer(frame_path)
        stats.last_latency_ms = latency_ms
        if latency_ms > 0:
            stats.avg_latency_ms = (
                latency_ms
                if stats.avg_latency_ms == 0
                else stats.avg_latency_ms * 0.8 + latency_ms * 0.2
            )

        if objects:
            sum_x = sum(o["center"]["x"] for o in objects)
            sum_y = sum(o["center"]["y"] for o in objects)
            stats.camera_x = sum_x / (len(objects) * max(width, 1))
            stats.camera_y = sum_y / (len(objects) * max(height, 1))
            camera_source = "hero-mean"
            camera_count = len(objects)
        else:
            if stats.processed == 0 and not backend.enabled:
                sx, sy = synthetic_camera_target(stem)
                stats.camera_x, stats.camera_y = sx, sy
            camera_source = "synthetic" if not backend.enabled else "fallback-prev"
            camera_count = 0

        payload = {
            "version": 3,
            "frame": stem,
            "ts": ts(),
            "status": status,
            "width": width,
            "height": height,
            "inference": {
                "model": backend.model_name,
                "model_path": str(backend.model_path),
                "latency_ms": round(latency_ms, 2),
                "enabled": backend.enabled,
                "region": backend.last_region,
            },
            "objects": objects,
            "camera": {
                "center_x": round(stats.camera_x, 4),
                "center_y": round(stats.camera_y, 4),
                "source": camera_source,
                "count": camera_count,
            },
        }
        try:
            atomic_write_json(state_sidecar, payload)
        except OSError as e:
            logger.error(
                "state_sidecar.write_failed",
                event_type="state_sidecar.write_failed",
                frame=stem,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return
        if legacy_sidecar_exists:
            try:
                frame_sidecar.unlink()
            except OSError:
                pass
        stats.processed += 1
        stats.last_frame = stem
        logger.debug(
            "frame.processed",
            event_type="frame.processed",
            **{
                "frame.index": stem,
                "processed": stats.processed,
                "objects": len(objects),
                "latency_ms": round(latency_ms, 2),
            },
        )

    anno_name = f"{stem}.annotated.jpg"
    state_anno_path = state_annotated_dir / anno_name
    legacy_anno_path = frame_path.parent / anno_name

    if legacy_anno_path.exists() and not state_anno_path.exists():
        try:
            shutil.move(str(legacy_anno_path), state_anno_path)
        except OSError as e:
            logger.error(
                "annotation.migrate_failed",
                event_type="annotation.migrate_failed",
                frame=stem,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    if not detection_cfg.annotate:
        return

    if state_anno_path.exists():
        return

    if not _HAS_CV2:
        logger.debug(
            "annotation.skipped",
            event_type="annotation.skipped",
            frame=stem,
            reason="missing_opencv",
            backend_enabled=backend.enabled,
        )
        return

    try:
        img = cv2.imread(str(frame_path))  # type: ignore
        if img is None:
            logger.debug(
                "annotation.read_failed",
                event_type="annotation.read_failed",
                frame=stem,
            )
            return
        if backend.enabled and objects:
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
                cv2.putText(
                    img,
                    label,
                    (x, max(0, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )  # type: ignore
        else:
            status_txt = "NO DETECTIONS" if backend.enabled else "BACKEND DISABLED"
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
        tmp_anno = state_annotated_dir / f"{stem}.annotated.tmp.jpg"
        success = cv2.imwrite(str(tmp_anno), img)  # type: ignore
        if not success:
            logger.error(
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
            os.replace(tmp_anno, state_anno_path)
        except OSError as e:
            logger.error(
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
        logger.debug(
            "frame.annotated",
            event_type="frame.annotated",
            frame=stem,
            annotated=str(state_anno_path.name),
            objects=len(objects),
            backend_enabled=backend.enabled,
        )
    except (OSError, ValueError, RuntimeError) as e:  # pragma: no cover
        logger.error(
            "annotation.failed",
            event_type="annotation.failed",
            frame=stem,
            error_type=type(e).__name__,
            error_message=str(e),
        )


def scan_existing(ctx: RuntimeContext, stats: Stats) -> None:
    """Scan existing frames in the directory, updating stats and sidecars as needed."""

    frames_dir = ctx.config.paths.frames_dir
    logger = ctx.logger

    if not frames_dir.exists():
        logger.warning(
            "frames.dir.missing",
            event_type="frames.dir.missing",
            path=str(frames_dir),
        )
        return
    frames = sorted(frames_dir.glob("*.bmp"))
    if not frames:
        logger.info(
            "frames.empty",
            event_type="frames.empty",
            path=str(frames_dir),
        )
    for frame in frames:
        process_frame(ctx, frame, stats)

    state_files = sorted(ctx.config.paths.detections_dir.glob("*.detections.json"))
    if state_files:
        stats.processed = max(stats.processed, len(state_files))
        if stats.last_frame is None:
            stats.last_frame = state_files[-1].stem
    logger.info(
        "initial.scan.complete",
        event_type="initial.scan",
        processed=stats.processed,
        last_frame=stats.last_frame,
    )


def handle_signal(_signum, _frame):  # type: ignore[override]
    """Handle termination signals."""
    global RUNNING  # noqa: PLW0603 (explicit global for shutdown flag)
    RUNNING = False


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nexus detection service")
    parser.add_argument("--config", type=Path, help="Path to detection config TOML")
    parser.add_argument(
        "--base-dir", type=Path, help="Override repository base directory"
    )
    parser.add_argument(
        "--run-once", action="store_true", help="Process pending frames and exit"
    )
    parser.add_argument("--model", type=Path, help="Override checkpoint path")
    parser.add_argument("--device", type=str, help="Torch device to run inference on")
    parser.add_argument(
        "--confidence", type=float, help="Confidence threshold override"
    )
    parser.add_argument("--iou", type=float, help="IoU threshold override")
    parser.add_argument("--crop-mode", type=str, help="Crop mode override")
    parser.add_argument(
        "--no-annotate", action="store_true", help="Disable annotated JPG output"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        help="Logging level override (debug/info/warning/error)",
    )
    parser.add_argument(
        "--log-stdout", action="store_true", help="Mirror logs to stdout"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Seconds between scans for new frames",
    )
    return parser.parse_args(argv)


def _build_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    detector: Dict[str, Any] = {}
    logging: Dict[str, Any] = {}

    if args.model:
        detector["model"] = str(args.model)
    if args.device:
        detector["device"] = args.device
    if args.confidence is not None:
        detector["confidence"] = float(args.confidence)
    if args.iou is not None:
        detector["iou"] = float(args.iou)
    if args.crop_mode:
        detector["crop_mode"] = args.crop_mode
    if args.no_annotate:
        detector["annotate"] = False

    if detector:
        overrides["detector"] = detector

    if args.log_level:
        logging["level"] = args.log_level
    if args.log_stdout:
        logging["stdout"] = True

    if logging:
        overrides["logging"] = logging

    return overrides


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main detection loop."""

    args = _parse_args(argv)
    overrides = _build_overrides(args)
    config = build_service_config(
        config_path=args.config,
        base_dir=args.base_dir,
        overrides=overrides,
    )
    ctx = build_runtime_context(config)
    frames_dir = ctx.config.paths.frames_dir
    logger = ctx.logger

    global RUNNING
    RUNNING = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    stats = Stats()
    last_hb = 0.0
    scan_existing(ctx, stats)
    write_heartbeat(ctx, stats)

    if args.run_once:
        for frame in sorted(frames_dir.glob("*.bmp")):
            state_sidecar = (
                ctx.config.paths.detections_dir / f"{frame.stem}.detections.json"
            )
            if not state_sidecar.exists():
                process_frame(ctx, frame, stats)
        write_heartbeat(ctx, stats)
        logger.info(
            "run.once.complete",
            event_type="run.once.complete",
            processed=stats.processed,
            last_frame=stats.last_frame,
        )
        return 0

    while RUNNING:
        for frame in sorted(frames_dir.glob("*.bmp")):
            state_sidecar = (
                ctx.config.paths.detections_dir / f"{frame.stem}.detections.json"
            )
            if not state_sidecar.exists():
                process_frame(ctx, frame, stats)
        now = ts()
        if now - last_hb >= 2.0:
            write_heartbeat(ctx, stats)
            logger.debug(
                "heartbeat",
                event_type="heartbeat.summary",
                processed=stats.processed,
                last_frame=stats.last_frame,
            )
            last_hb = now
        time.sleep(max(args.poll_interval, 0.05))

    write_heartbeat(ctx, stats)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
