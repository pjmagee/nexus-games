---
applyTo: '**'
---

You are creating an auto-spectator for watching 'Heroes of the Storm' `.StormReplay` files.

Phase 1 of development (current scope) explicitly DOES NOT integrate with HeroesProfile or remote S3 downloads. Instead, replay discovery harvests any existing `.StormReplay` files found recursively under:

`C:\Users\patri\OneDrive\Documents\Heroes of the Storm\Accounts\**\*.StormReplay`

Discovered replays are COPIED (not moved) into the internal `replays/queue/` folder if they are not already present (duplicate prevention via filename + optional SHA1 hash). External API integration will be added only in a later phase once the local pipeline is stable.

The engine consists of coordinated processes operating over a FILE-BASED event pipeline (1 frame per second) rather than high-frequency IPC.

## Phases

Phase 1 (Core Engine – NO OBS integration, single replay at a time):
    - Load replay file (launch game client with selected replay)
    - Capture screenshots once per second to disk (atomic `.pending` then rename)
    - Detect heroes via YOLOv11 (write detection JSON sidecars)
    - Manipulate in‑game camera (auto spectator) using smoothed detection targets (PyAutoGUI)
    - Detect end of replay (Victory/Defeat text or termination cues) and finalize session

Phase 2 (Automated Cycle & OBS Integration):
    - Loop: load replay -> spectate -> end detection -> clean up -> next
    - OBS scene control (Waiting vs Spectating) and reconciliation
    - Replay queue management refinements & parallelization groundwork

Later Phases (Deferred / Future):
    - HeroesProfile / S3 ingestion pipeline (downloading .StormReplay files)
    - Advanced analytics (team fight detection, highlight clipping)

Phase 1 Core Responsibilities (implemented without OBS):
    - Automate launching of `.StormReplay` files
    - Perform initial zoom-out (improved spectator overview) - once loaded (Ctl+Z)
    - Capture a screenshot once per second and write frames to disk (`sessions/current/frames/NNNNN.jpg` using an atomic `.pending` then rename pattern)
    - Run computer vision / object detection (YOLOv11) over saved frames and write detection JSON sidecars (`NNNNN.detections.json`)
    - Perform camera pan logic using smoothed hero / point-of-interest targets and simulated input (PyAutoGUI)
    - Maintain durable session state JSON and logs for recovery
    - End-of-Match detection and orderly shutdown


## Must requirements:
- Python3 (3.12 minimum)
- Strict typings, no 'Any' etc.
- UV for package management
- Use virtual environment
- Pywin32 and pillow for image capturing and saving
- Ultralytics for YOLOv11 and Hero detection
- OpenCV for image processing and feeding the image to YOLOv11
- Supervision (by RoboFlow) for filtering results from YOLOv11
- PyAutoGUI for controlling the mouse and keyboard based on results from YOLOv11
    

### High level design (file-based, low-frequency ~1 FPS):

- Replay Harvester: scans OneDrive path for `.StormReplay` and copies new ones into `replays/queue/` (Phase 1 local only)
- Session Orchestrator: manages session lifecycle and moves replay `queue/ -> active/ -> completed/`
- Frame Capture: once per second capture to disk using atomic write (`.pending` then `os.replace`)
- Detection Engine: processes new frame files sequentially and emits detection JSON files
- Camera Controller: consumes latest detections, applies smoothing, issues mouse drags/scrolls
- Timer OCR & Load Validator: extracts and validates the in-game match timer as primary "loaded" signal
- End-of-Match Detector: looks for "Victory" / "Defeat" text or other terminal cues
- (Phase 2+) OBS Controller: reconciles desired vs actual scene (Waiting vs Spectating)
- Health / Heartbeats: each actor writes a heartbeat JSON file for supervision
- Logging & Recovery: append-only session log plus `session.json` snapshot (atomic updates)

All high-frequency needs are avoided intentionally; disk I/O at 1 FPS is acceptable and simplifies durability and debugging.


## OBS Scene Transitions (Deferred to Phase 2)

Deferred until Phase 2 to keep Phase 1 focused on core autonomous spectating loop without external tooling integration.
Planned behavior:
- 'Waiting' scene shown when no active spectating session (custom MP4 loop)
- 'Spectating' scene shown when a replay is being processed (game window focused)


## Session Lifecycle & States

States (finite state machine):
`LAUNCHING -> AWAIT_TIMER -> VALIDATING_START -> SPECTATING -> ENDING -> COMPLETED`
Failure branch: `ABORTED` (with error code). A `DEGRADED` flag may be set while still in SPECTATING if some signals are missing (e.g., no hero detections but timer OK).

State transition cues:
- LAUNCHING: Replay passed to game client, waiting for window & initial frame capture.
- AWAIT_TIMER: Searching for on-screen match timer region.
- VALIDATING_START: Timer detected; require monotonic increments (e.g., 3 consecutive frames) and optional early hero detection or fallback threshold time.
- SPECTATING: Normal operation (detections + camera control). May set `degraded=true` if hero_count remains zero beyond grace period.
- ENDING: Victory/Defeat text detected OR replay termination cues.
- COMPLETED: Session finalized; resources released; replay moved to `completed/`.
- ABORTED: Terminal error (missing timer, frozen timer, launch failure, corruption) -> replay optionally quarantined.

Session file operations (atomic):
1. Move replay: `queue/ -> active/` using `os.replace`.
2. Create/update `session.json` (write temp then rename) containing: `state`, `version`, `updated_ts`, `replay`, `timer`, `detection`, `flags`.
3. On completion: move `active/ -> completed/`.

Timer-based load validation:
- Extract mm:ss from fixed ROI via template matching (digits + colon) each frame.
- Maintain `monotonic_streak`; require threshold (>=3) for progression.
- Accept transition to SPECTATING without early hero detection after configurable timeout (e.g., 60s) while marking `degraded`.

Hero detection absence handling:
- Track `zero_hero_seconds` and trigger exploratory camera patterns if threshold exceeded.
- Clear degraded flag once a hero detection occurs.


## Replays (Phase 1 Local Harvest Only)

- No HeroesProfile API or S3 integration in Phase 1.
- Harvest local replays: recursively scan `C:\Users\patri\OneDrive\Documents\Heroes of the Storm\Accounts\**` for `*.StormReplay`.
- Copy unseen files into `replays/queue/` (skip if already present; optional SHA1 hashing for dedupe).
- Enforce queue size cap (e.g., 10) to avoid uncontrolled growth; stop harvesting when cap reached.
- Future Phase: integrate remote API fetching & aging logic (placeholder, not implemented now).

Directory structure (illustrative):
```
replays/
    queue/
    active/
    completed/
    corrupt/ (optional)
sessions/
    current/
        session.json
        logs/session.log
        frames/
            00001.jpg
            00001.detections.json
            ...
        state/
            heartbeat_capture.json
            heartbeat_detector.json
            heartbeat_camera.json
            camera.json
```

Frame & detection file naming:
- Frame capture: write `NNNNN.pending` then atomic rename to `NNNNN.jpg` when complete.
- Detection output: write `NNNNN.detections.json.tmp` then atomic rename.

Heartbeats:
- Each actor updates its heartbeat JSON every ~5 seconds with `{ts, pid, frame}` for supervision.

Logging & Recovery:
- Append events to `session.log` (e.g., `STATE transition`, `TIMER first_detected`, `HERO first_detection`).
- On crash: highest sequential frame index and `session.json` enable resumption.

End-of-Match Detection:
- Primary: OCR / template match for `Victory` / `Defeat`.
- Secondary: Replay window closure or stagnation heuristics.

Camera Control Smoothing:
- EMA-based smoothing with stale detection fallback; limited move rate to avoid jitter.

Degraded Mode:
- Signaled when critical but non-fatal signals (hero detections) absent beyond grace period while timer remains healthy.

Future Phase Placeholders (not implemented yet):
- HeroesProfile/S3 ingestion pipeline.
- Parallel multi-session scheduling.
- Real-time streaming overlays.