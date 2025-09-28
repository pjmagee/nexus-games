"""Orchestrator supervision entry point (relocated)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .util import atomic_write_json, ts  # type: ignore

# Use NEXUS_BASE_DIR environment variable for consistent path resolution
BASE_DIR = Path(os.getenv("NEXUS_BASE_DIR", Path.cwd()))
SESSIONS_DIR = BASE_DIR / "sessions" / "current"
STATE_DIR = SESSIONS_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
HEARTBEAT = STATE_DIR / "heartbeat_orchestrator.json"

# Build configuration for native capture can be overridden (Debug / Release)
CAPTURE_CONFIG = os.getenv("CAPTURE_CONFIG", "Debug")
CAPTURE_EXE = Path(f"build/cpp/capture/{CAPTURE_CONFIG}/hots_capture.exe")

# Updated C# project paths (previous csharp/ path was legacy)
CSHARP_CONTROL_PROJ = Path("src/game-controller/Control.csproj")
CSHARP_HARVESTER_PROJ = Path("src/replay-harvestor/Harvester.csproj")
DETECTION_SCRIPT = ["uv", "run", "python", "-m", "detection.service"]
# External standalone session manager (module-based execution)
SESSION_MANAGER_CMD = ["uv", "run", "python", "-m", "session_manager.service"]

MAX_RESTARTS = 5
BASE_BACKOFF = 2.0
RUNNING = {"value": True}
LOGS_DIR = STATE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "orchestrator.log"
_env_log_level = os.getenv("LOG_LEVEL")
_orch_verbose = os.getenv("ORCH_VERBOSE") == "1"
if _env_log_level is None and _orch_verbose:
    # Transitional behavior: promote to debug when legacy verbose flag used without explicit LOG_LEVEL
    LOG_LEVEL = "debug"
else:
    LOG_LEVEL = (_env_log_level or "info").lower()
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def _ts_iso() -> str:
    return (
        datetime.utcnow()
        .replace(tzinfo=timezone.utc)
        .isoformat(timespec="milliseconds")
    )


def _log_should(level: str) -> bool:
    return _LEVEL_ORDER.get(level, 20) >= _LEVEL_ORDER.get(LOG_LEVEL, 20)


_DEPRECATION_VERBOSE_LOGGED = False


def log(level: str, message: str, **fields):
    level = level.lower()
    if not _log_should(level):
        return
    rec = {
        "@timestamp": _ts_iso(),
        "service.name": "orchestrator",
        "service.role": "supervisor",
        "process.pid": os.getpid(),
        "log.level": level,
        "message": message,
    }
    rec.update(fields)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        return


# Emit a one-time deprecation notice if ORCH_VERBOSE was used
if _orch_verbose and _env_log_level is None:
    log(
        "warning",
        "orch_verbose.deprecated",
        event_type="orch_verbose.deprecated",
        detail="ORCH_VERBOSE=1 implicitly sets LOG_LEVEL=debug; prefer LOG_LEVEL only",
    )


@dataclass
class ChildSpec:
    name: str
    start_cmd: List[str]
    cwd: Path
    process: Optional[subprocess.Popen] = None
    restarts: int = 0
    last_start: float = 0.0
    backoff: float = BASE_BACKOFF
    failed: bool = False

    def want_restart(self) -> bool:  # retained for future logic
        if self.failed:
            return False
        if self.process is None:
            return True
        return self.process.poll() is not None

    def record_crash(self) -> None:
        self.restarts += 1
        self.last_start = ts()
        if self.restarts > MAX_RESTARTS:
            self.failed = True
        else:
            self.backoff = min(self.backoff * 1.5, 30.0)
        log(
            "warning",
            "child.crash",
            event_type="child.crash",
            child=self.name,
            restarts=self.restarts,
            failed=self.failed,
            backoff=self.backoff,
        )

    def spawn(self) -> None:
        if self.failed:
            return
        now = ts()
        if (
            now - self.last_start < self.backoff
            and self.process is not None
            and self.process.poll() is not None
        ):
            return
        try:
            # Verbose logging: if ORCH_VERBOSE=1 redirect stdout/stderr to per-child log files
            verbose = os.environ.get("ORCH_VERBOSE") == "1"
            stdout_target = subprocess.DEVNULL
            stderr_target = subprocess.DEVNULL
            if verbose:
                logs_dir = STATE_DIR / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                stdout_target = open(
                    logs_dir / f"{self.name}.out.log", "a", encoding="utf-8"
                )  # noqa: SIM115
                stderr_target = open(
                    logs_dir / f"{self.name}.err.log", "a", encoding="utf-8"
                )  # noqa: SIM115
            self.process = subprocess.Popen(
                self.start_cmd,
                cwd=self.cwd,
                stdout=stdout_target,
                stderr=stderr_target,
            )
            self.last_start = now
            log(
                "info",
                "child.spawn",
                event_type="child.spawn",
                child=self.name,
                pid=(self.process.pid if self.process else None),
            )
        except (OSError, ValueError) as exc:
            self.record_crash()
            log(
                "error",
                "child.spawn.error",
                event_type="child.spawn.error",
                child=self.name,
                restarts=self.restarts,
                error=str(exc),
            )

    def ensure(self) -> None:
        if self.process is None:
            self.spawn()
            return
        if self.process.poll() is not None:
            self.record_crash()
            self.spawn()

    def terminate(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
            else:
                log(
                    "info",
                    "child.terminate",
                    event_type="child.terminate",
                    child=self.name,
                )

    def kill(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass
            else:
                log("warning", "child.kill", event_type="child.kill", child=self.name)


def ts_fallback() -> float:  # fallback if util import fails
    return time.time()


def write_heartbeat(loop_iter: int, children: List[ChildSpec]) -> None:
    payload_children = []
    for c in children:
        pid = c.process.pid if c.process and c.process.poll() is None else None
        exited_code = (
            c.process.returncode if c.process and c.process.poll() is not None else None
        )
        payload_children.append(
            {
                "name": c.name,
                "pid": pid,
                "restarts": c.restarts,
                "failed": c.failed,
                "returncode": exited_code,
                "backoff": c.backoff,
            }
        )
    payload = {
        "service": "orchestrator",
        "role": "supervisor",
        "ts": ts() if "ts" in globals() else ts_fallback(),
        "loop_iter": loop_iter,
        "version": 1,
        "mode": "supervise",
        "children": payload_children,
        "all_failed": all(c.failed for c in children),
    }
    atomic_write_json(HEARTBEAT, payload)


def build_children() -> List[ChildSpec]:
    """Build child specifications with optional spawn filtering.

    ORCH_SPAWN (optional env): comma-separated list limiting which children are spawned.
    If unset or empty -> spawn all. Example:
        ORCH_SPAWN=capture,control
    Useful for VS Code multi-config debugging where individual services are
    launched directly under the debugger to avoid duplicate processes.
    """
    spawn_filter_raw = os.getenv("ORCH_SPAWN", "").strip()
    spawn_filter = (
        set(filter(None, [p.strip() for p in spawn_filter_raw.split(",")]))
        if spawn_filter_raw
        else set()
    )

    all_specs = [
        ChildSpec("capture", [str(CAPTURE_EXE)], Path.cwd()),
        ChildSpec(
            "control",
            ["dotnet", "run", "--project", str(CSHARP_CONTROL_PROJ)],
            Path.cwd(),
        ),
        ChildSpec(
            "harvester",
            ["dotnet", "run", "--project", str(CSHARP_HARVESTER_PROJ)],
            Path.cwd(),
        ),
        ChildSpec("session-manager", SESSION_MANAGER_CMD, Path.cwd()),
        ChildSpec("detection", DETECTION_SCRIPT, Path.cwd()),
    ]
    if not spawn_filter:
        return all_specs
    return [c for c in all_specs if c.name in spawn_filter]


def handle_signal(signum, frame):  # type: ignore[override]
    """Signal handler to request graceful shutdown (arguments unused)."""
    _ = signum, frame  # silence unused warnings
    RUNNING["value"] = False


def main(interval: float = 2.0) -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    children = build_children()
    for c in children:
        c.ensure()
    log(
        "info",
        "supervisor.start",
        event_type="supervisor.start",
        children=[c.name for c in children],
    )
    loop_iter = 0
    write_heartbeat(loop_iter, children)
    while RUNNING["value"]:
        time.sleep(interval)
        loop_iter += 1
        for c in children:
            c.ensure()
        write_heartbeat(loop_iter, children)
        if _log_should("debug"):
            log(
                "debug",
                "supervisor.loop",
                event_type="supervisor.loop",
                loop_iter=loop_iter,
            )
    for c in children:
        c.terminate()
    deadline = (ts() if "ts" in globals() else ts_fallback()) + 3.0
    while (ts() if "ts" in globals() else ts_fallback()) < deadline and any(
        c.process and c.process.poll() is None for c in children
    ):
        time.sleep(0.2)
    for c in children:
        c.kill()
    write_heartbeat(loop_iter, children)
    log("info", "supervisor.stop", event_type="supervisor.stop", loop_iter=loop_iter)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
