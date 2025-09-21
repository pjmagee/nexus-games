"""Standalone Session Manager Service.

Extracted from prior orchestrator.session_manager module so it can behave like
other independent services with its own installable package and script entry point.

Synthetic FSM (placeholder): IDLE -> QUEUED -> LOADING -> ACTIVE -> ENDED -> COMPLETED

Future improvements (Phase 1+):
- Integrate real replay launch + process tracking
- Replace timed transitions with real cues (frames present, timer OCR, victory/defeat)
"""

from __future__ import annotations

import signal
import time
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, TextIO



def ts() -> float:  # simple timestamp helper
    """Simple monotonic timestamp helper."""
    return time.time()


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomic JSON write by writing to a temporary file and then replacing the original."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


BASE_DIR = Path.cwd()
REPLAYS_DIR = BASE_DIR / "replays"
QUEUE_DIR = REPLAYS_DIR / "queue"
ACTIVE_DIR = REPLAYS_DIR / "active"
COMPLETED_DIR = REPLAYS_DIR / "completed"
CORRUPT_DIR = REPLAYS_DIR / "corrupt"
SESSION_DIR = BASE_DIR / "sessions" / "current"
STATE_DIR = SESSION_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_JSON = SESSION_DIR / "session.json"
HEARTBEAT = STATE_DIR / "heartbeat_session.json"
SERVICE_JSON = STATE_DIR / "session_service.json"

RUNNING = True
LOG_DIR = STATE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "session-manager.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").lower()
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


class JsonLogger:
    """JSON logger for structured logging."""

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
        return (
            datetime.utcnow()
            .replace(tzinfo=timezone.utc)
            .isoformat(timespec="milliseconds")
        )

    def log(self, level: str, message: str, **fields: Any) -> None:
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
        except OSError:
            return

    # Convenience methods
    def debug(self, msg: str, **f: Any) -> None:
        self.log("debug", msg, **f)

    def info(self, msg: str, **f: Any) -> None:
        self.log("info", msg, **f)

    def warning(self, msg: str, **f: Any) -> None:
        self.log("warning", msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        self.log("error", msg, **f)


LOGGER = JsonLogger("session-manager", "lifecycle", LOG_FILE, LOG_LEVEL)


# Timings (seconds) for synthetic transitions
LOADING_DELAY = (
    5.0  # retained (not heavily used after new FSM, but kept for compatibility)
)
ACTIVE_DURATION = 10.0
ENDED_DELAY = 3.0

# Launch / activation tuning (env override capable)
LAUNCH_SUCCESS_FRAMES = int(os.getenv("SESSION_LAUNCH_SUCCESS_FRAMES", "3"))
LAUNCH_TIMEOUT_SECONDS = float(os.getenv("SESSION_LAUNCH_TIMEOUT", "90"))

# How long after ACTIVE before synthetic ENDED (placeholder until real end detection)
ACTIVE_PHASE_MAX = float(os.getenv("SESSION_ACTIVE_MAX", "120"))


@dataclass
class Session:
    """Session state representation."""

    replay_filename: Optional[str] = None
    state: str = (
        "IDLE"  # IDLE -> QUEUED -> LAUNCHING -> ACTIVE -> ENDED -> COMPLETED | ABORTED | CORRUPT
    )
    claimed_ts: Optional[float] = None
    launch_ts: Optional[float] = None
    loading_ts: Optional[float] = None  # retained for compatibility
    active_ts: Optional[float] = None
    ended_ts: Optional[float] = None
    completed_ts: Optional[float] = None
    aborted_ts: Optional[float] = None
    game_pid: Optional[int] = None  # future use (if we attach to process)
    consecutive_success_frames: int = 0

    def to_json(self) -> Dict[str, Any]:
        """Serialize state to JSON-serializable dictionary."""

        return {
            "schema_version": 1,
            "replay": self.replay_filename,
            "state": self.state,
            "timestamps": {
                "claimed": self.claimed_ts,
                "launch": self.launch_ts,
                "loading": self.loading_ts,
                "active": self.active_ts,
                "ended": self.ended_ts,
                "completed": self.completed_ts,
                "aborted": self.aborted_ts,
            },
            "metrics": {
                "consecutive_success_frames": self.consecutive_success_frames,
                "launch_success_frames_required": LAUNCH_SUCCESS_FRAMES,
            },
            "updated_ts": ts(),
            "version": 1,
        }


def write_session(session: Session) -> None:
    """Write session state."""

    atomic_write_json(SESSION_JSON, session.to_json())


def write_heartbeat(session: Session) -> None:
    """Write lifecycle heartbeat for Prometheus scraping."""

    atomic_write_json(
        HEARTBEAT,
        {
            "service": "session-manager",
            "role": "lifecycle",
            "state": session.state,
            "replay": session.replay_filename,
            "ts": ts(),
            "schema_version": 1,
            "version": 1,
        },
    )


def write_service_meta() -> None:
    """Write initial service metadata if missing."""

    if not SERVICE_JSON.exists():
        atomic_write_json(
            SERVICE_JSON,
            {
                "service": "session-manager",
                "schema_version": 1,
                "created_ts": ts(),
                "notes": "Synthetic FSM placeholder (Phase 1 minimal).",
            },
        )


def claim_replay(session: Session) -> bool:
    """Atomically MOVE the next replay from queue -> active.

    We intentionally *move* (take) the file so it cannot be claimed twice. The
    initial copy semantics are deprecated now that the internal queue is already
    considered a safe staging area (original source remains in OneDrive)."""

    if session.replay_filename is not None:
        return True
    if not QUEUE_DIR.exists():
        return False
    candidates = sorted([p for p in QUEUE_DIR.glob("*.StormReplay") if p.is_file()])
    if not candidates:
        return False
    replay = candidates[0]
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    target = ACTIVE_DIR / replay.name
    try:
        # os.replace is atomic on the same filesystem; acts as our lock.
        os.replace(replay, target)
    except FileNotFoundError:
        # Another (future) concurrent session manager could have raced us.
        return False
    session.replay_filename = replay.name
    session.state = "QUEUED"
    session.claimed_ts = ts()
    write_session(session)
    LOGGER.info(
        "replay.claim", event_type="replay.claim", **{"replay.file": replay.name}
    )
    return True


def load_capture_heartbeat() -> Optional[Dict[str, Any]]:
    """Load the last capture heartbeat if available."""

    hb_path = STATE_DIR / "heartbeat_capture.json"
    if not hb_path.exists():
        return None
    try:
        with hb_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def maybe_launch(session: Session) -> None:
    """Attempt to launch the replay if in LAUNCHING state."""

    if session.state != "QUEUED" or not session.replay_filename:
        return
    # Launch using OS association (.StormReplay registered) via os.startfile (Windows-only).
    try:
        replay_path = (ACTIVE_DIR / session.replay_filename).resolve()
        if replay_path.exists():
            os.startfile(str(replay_path))  # type: ignore[attr-defined]
            session.state = "LAUNCHING"
            session.launch_ts = ts()
            write_session(session)
            LOGGER.info(
                "replay.launch",
                event_type="replay.launch",
                **{
                    "replay.file": session.replay_filename,
                    "replay.path": str(replay_path),
                },
            )
        else:
            LOGGER.error(
                "replay.launch.missing",
                event_type="replay.launch.missing",
                **{"replay.file": session.replay_filename},
            )
    except OSError as exc:
        LOGGER.error(
            "replay.launch.error",
            event_type="replay.launch.error",
            **{"replay.file": session.replay_filename, "error": str(exc)},
        )


def maybe_transition(session: Session) -> None:
    """Evaluate and transition session states as needed."""

    now = ts()
    # QUEUED -> (attempt launch) -> LAUNCHING handled separately
    if session.state == "QUEUED":
        maybe_launch(session)
        return

    if session.state == "LAUNCHING":
        # Check capture heartbeat for consecutive success frames
        hb = load_capture_heartbeat()
        success = bool(hb and hb.get("success"))
        fail_reason = hb.get("result") if hb else None
        if success:
            session.consecutive_success_frames += 1
        else:
            # Reset only if we had accumulated some and then lost window
            if session.consecutive_success_frames > 0:
                session.consecutive_success_frames = 0
        # Activation criteria
        if session.consecutive_success_frames >= LAUNCH_SUCCESS_FRAMES:
            session.state = "ACTIVE"
            session.active_ts = now
            write_session(session)
            LOGGER.info(
                "state.transition",
                event_type="state.transition",
                from_state="LAUNCHING",
                to_state="ACTIVE",
                **{"success.frames": session.consecutive_success_frames},
            )
            return
        # Timeout / abort / corrupt classification
        if session.launch_ts and (now - session.launch_ts) > LAUNCH_TIMEOUT_SECONDS:
            classify_corrupt = fail_reason in {"no_window", "too_small"}
            if classify_corrupt:
                session.state = "CORRUPT"
                session.aborted_ts = now
                write_session(session)
                LOGGER.error(
                    "state.transition",
                    event_type="state.transition",
                    from_state="LAUNCHING",
                    to_state="CORRUPT",
                    reason="launch_no_window",
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                )
                if session.replay_filename:
                    active_path = ACTIVE_DIR / session.replay_filename
                    if active_path.exists():
                        CORRUPT_DIR.mkdir(parents=True, exist_ok=True)
                        try:
                            os.replace(
                                active_path, CORRUPT_DIR / session.replay_filename
                            )
                        except OSError:
                            pass
                return
            else:
                session.state = "ABORTED"
                session.aborted_ts = now
                write_session(session)
                LOGGER.error(
                    "state.transition",
                    event_type="state.transition",
                    from_state="LAUNCHING",
                    to_state="ABORTED",
                    reason="launch_timeout",
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                )
                # Move replay back to queue for retry
                if session.replay_filename:
                    active_path = ACTIVE_DIR / session.replay_filename
                    if active_path.exists():
                        try:
                            os.replace(active_path, QUEUE_DIR / session.replay_filename)
                        except OSError:
                            pass
                return
        return

    if session.state == "ACTIVE":
        # Placeholder end condition: fixed ACTIVE_PHASE_MAX or legacy ACTIVE_DURATION for early finish
        if session.active_ts and (now - session.active_ts) >= min(
            ACTIVE_PHASE_MAX, ACTIVE_DURATION
        ):
            session.state = "ENDED"
            session.ended_ts = now
            write_session(session)
            LOGGER.info(
                "state.transition",
                event_type="state.transition",
                from_state="ACTIVE",
                to_state="ENDED",
            )
        return

    if session.state == "ENDED":
        if session.ended_ts and now - session.ended_ts >= ENDED_DELAY:
            session.state = "COMPLETED"
            session.completed_ts = now
            write_session(session)
            finalize_replay(session)
            LOGGER.info(
                "state.transition",
                event_type="state.transition",
                from_state="ENDED",
                to_state="COMPLETED",
            )
        return


def finalize_replay(session: Session) -> None:
    """Move active -> completed (atomic best-effort)."""

    if not session.replay_filename:
        return
    src = ACTIVE_DIR / session.replay_filename
    if not src.exists():
        return
    COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
    dst = COMPLETED_DIR / session.replay_filename
    try:
        if dst.exists():
            dst.unlink()
        os.replace(src, dst)
    except FileNotFoundError:
        # Source vanished between check and replace.
        return
    except PermissionError:
        return
    except OSError:
        return
    else:
        LOGGER.info(
            "replay.complete",
            event_type="replay.complete",
            **{"replay.file": session.replay_filename},
        )


def handle_signal(_signum, _frame):  # type: ignore[override]
    """Signal handler to initiate graceful shutdown."""
    # Set module-level flag for graceful shutdown.
    # Using mutable container to avoid global statement.
    globals()["RUNNING"] = False


def main(loop_interval: float = 1.0) -> int:
    """Main service loop."""

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    write_service_meta()
    session = Session()
    last_hb = 0.0
    write_session(session)

    while RUNNING:
        if session.state in ("IDLE", "COMPLETED", "ABORTED", "CORRUPT"):
            if claim_replay(session):
                # Immediately attempt transition (possibly launch)
                maybe_transition(session)
        else:
            maybe_transition(session)

        now = ts()
        if now - last_hb >= 2.0:
            write_heartbeat(session)
            last_hb = now
        time.sleep(loop_interval)

    write_heartbeat(session)
    write_session(session)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
