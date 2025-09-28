# Nexus Games Auto-Spectator (Proof of concept)

This is a proof of concept for a system that automatically spectates games in Heroes of the Storm and streams them to Twitch.

## Components Overview

### game-capture (C++ / Win32 + Direct3D 11)

Captures Heroes of the Storm once per second and writes BMP frame files to `sessions/current/frames`.

### hero-inference (Python 3.12)

- Reads frame BMPs from game-capture
- runs YOLOv12 trained dataset model
- outputs per-frame `.detections.json` and `.annotated.jpg` files

### hero-training (Python 3.12)

Ultralytics for fine-tuning YOLO models using labeled frames under the `training/` folder.

### session-manager (Python 3.12)

Coordinates replay session lifecycle state (queue → active → completed) and supervises heartbeat/state files.

### game-controller (C# .NET 9)

Consumes detection sidecars to drive automated camera panning via synthesized middle-mouse drags.

### orchestrator (Python 3.12)

High-level process supervisor to launch and monitor capture, detection, controller, and future pipeline actors.

### replay-harvestor (C# .NET 9)

Scans local replay directories and feeds `.StormReplay` files into the processing queue (remote ingestion later).

## Container builds

- Each Linux-compatible component has a Dockerfile and compose wiring.
- Windows-only projects (capture & controller) stay on the host – details below.

```powershell
# Build all containers
docker compose build

# Run inference service with frame/state directories mounted from the repo
docker compose up hero-inference

# Launch the harvester with your replay library exposed (set HOTS_REPLAYS_ROOT first)
$env:HOTS_REPLAYS_ROOT="C:/Users/<you>/OneDrive/Documents/Heroes of the Storm/Accounts"
docker compose up replay-harvestor

# Kick off a training run (requires NVIDIA GPU passthrough)
docker compose --profile training run --rm --gpus all hero-training \
  hero-train --config configs/yolo11n.yaml --data /workspace/training/data.yaml
```

### Hero inference (Linux container)

- `src/hero-inference/Dockerfile` builds a slim Python 3.12 image using uv for dependency resolution.
- Volumes: `sessions/current` (frames & state) and `src/hero-training/outputs` (YOLO weights).
- Override arguments by appending them to the compose command, e.g. `docker compose run hero-inference hero-inference --run-once`.

### Hero training (Linux + NVIDIA GPU)

- `src/hero-training/Dockerfile` targets `nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04` with Python 3.12 from deadsnakes and uv-managed deps.
- Mount `./training` for datasets and `./src/hero-training/outputs` for checkpoints.
- Requires `--gpus all` (or a narrower device request) when running the container.

### Replay harvester

- `src/replay-harvestor/Dockerfile` provides a multi-stage .NET 9 build.
- New environment variables:
  - `HARVEST_SOURCE` (defaults to your OneDrive path)
  - `HARVEST_QUEUE_DIR` (defaults to `replays/queue` relative to the workdir)
  - `HARVEST_QUEUE_CAP` (defaults to `10`)
  - `HARVEST_STATE_ROOT` (defaults to `sessions/current/state`)
- Bind mount your replay library via `HOTS_REPLAYS_ROOT` before starting the compose service.

### Windows-specific components

- `game-capture` relies on Win32/WinRT graphics APIs and cannot run in a Linux container. Keep building via Visual Studio/MSBuild on Windows.
- `game-controller` synthesizes Win32 messages against the Heroes client; Docker lacks the necessary desktop/windowing infrastructure. Continue running it directly on Windows.

## Future planned work

- provide hero-specific recognition
- hero clustering / fights
- camps, bosses, waves
- low health heros

## Python environment management

All Python services are managed with [uv](https://github.com/astral-sh/uv).

### hero-inference configuration

`src/hero-inference/detection/config/defaults.toml` holds the baseline paths, logging, and detection thresholds. Edit this file to change the default model, session directory, or crop strategy. Every key can be overridden at runtime without touching the environment:

```powershell
uv run python -m detection.service `
  --run-once `
  --model src/hero-training/outputs/yolov12-long3/weights/best.pt `
  --log-level debug `
  --poll-interval 0.25
```

## Roboflow with assisted labelling

- <https://app.roboflow.com/heroes-of-the-storm/heroes-of-the-storm-teqko/models>
- Exported dataset to the `/training` folder for local traiing on my RTX4080
