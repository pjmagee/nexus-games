# Nexus Games Auto-Spectator (Proof of concept)

This is a proof of concept for a system that automatically spectates games in Heroes of the Storm and streams them to Twitch.

## Components Overview

- **game-capture** (C++ / Win32 + Direct3D 11): Captures the Heroes of the Storm game window once per second and writes atomic BMP frame files to `sessions/current/frames`.

- **hero-inference** (Python 3.12): Reads frame BMPs, runs YOLO-based (or synthetic fallback) detection, outputs per-frame `.detections.json` sidecars and annotated JPG overlays. Configuration lives in `src/hero-inference/detection/config/defaults.toml`, and you can override any key at runtime via CLI flags instead of environment variables.
- **hero-training** (Python 3.12): UV-managed Ultralytics CLI for fine-tuning YOLO models using labeled frames under `training/data-sets/`.

- **session-manager** (Python 3.12): Coordinates replay session lifecycle state (queue → active → completed) and supervises heartbeat/state files.

- **game-controller** (C# .NET 9): Consumes detection sidecars to drive automated camera panning via synthesized middle-mouse drags.

- **orchestrator** (Python 3.12): (Planned / Partial) High-level process supervisor to launch and monitor capture, detection, controller, and future pipeline actors.

- **replay-harvestor** (C# .NET 9): (Phase 1 local focus) Scans local replay directories and feeds `.StormReplay` files into the processing queue (remote ingestion deferred).

- **(future) analytics / classifier** (Python / ML): Will provide hero-specific recognition, fight detection, and advanced scene understanding.

## Python environment management

All Python services are managed with [uv](https://github.com/astral-sh/uv). From each project directory (`src/orchestrator`, `src/session-manager`, `src/hero-inference`, `src/hero-training`) run:

```powershell
if (-not (Test-Path .venv)) { uv venv .venv }
uv pip install -e .
```

The VS Code tasks invoke the same commands, so ensure `uv` is installed and on your `PATH` before launching any Python components.

To set everything up at once from the repo root, run the helper script:

```powershell
scripts/bootstrap-python.ps1
```

Pass `-Force` to rebuild environments from scratch.

### hero-inference configuration

`src/hero-inference/detection/config/defaults.toml` holds the baseline paths, logging, and detection thresholds. Edit this file to change the default model, session directory, or crop strategy. Every key can be overridden at runtime without touching the environment:

```powershell
uv run python -m detection.service `
  --run-once `
  --model src/hero-training/outputs/yolov12-long3/weights/best.pt `
  --log-level debug `
  --poll-interval 0.25
```

Common flags:

- `--config <path>` – point at a different TOML file (supports partial overrides).
- `--base-dir <path>` – relocate `sessions/current` and related runtime paths.
- `--device cuda:0` – run the YOLO checkpoint on a specific accelerator.
- `--no-annotate` – skip annotated JPG overlays when you only need JSON sidecars.

The service writes heartbeats and logs under `sessions/current/state` using the configured logging level.

## Dataset Labeling (Label Studio)

We use [Label Studio](https://github.com/HumanSignal/label-studio) for creating YOLO training annotations over the raw frame images under `training/images`.

### Quick Start

1. Ensure Docker Desktop is running.
2. From repo root, start the service:

```bash
docker compose up -d label-studio
```

1. Open [http://localhost:8080](http://localhost:8080) in your browser.
1. Create a project (e.g. "hots-heroes").
1. Add a data import source pointing to the mounted path `/label-studio/import/images` (the container view of `training/images`).
1. Define labeling config (e.g. one RectangleLabels block with classes: `red`, `blue`, `health_bar`, etc.).
1. Annotate images; use Label Studio export (YOLO format) to populate `training/annotations` (uncomment the export volume in `docker-compose.yml` to persist straight into the repo).

### Stopping / Updating

```bash
docker compose stop label-studio
docker compose pull label-studio && docker compose up -d label-studio
```

### Volumes & Persistence

- Named volume `labelstudio_data` keeps the internal DB & media.
- Raw images are mounted read-only to avoid accidental deletion.
- To reset everything: `docker compose down -v` (WARNING: deletes annotation DB).

### Environment Variables

You can add superuser creation by uncommenting the `DJANGO_SUPERUSER_*` vars in `docker-compose.yml` or using an `.env` file.



## Model Lifecycle

1. Capture frames land in `training/images/` via the game capture pipeline.
1. Annotate frames in Label Studio and export YOLOv12 datasets into `training/data-sets/<version>/`.
1. Fine-tune models locally with `hero-training` (see `src/hero-training/README.md`).
1. Promote the best checkpoint into `src/hero-training/outputs/<run>/weights/` (or update `detection/config/defaults.toml` / pass `--model` at launch) so `hero-inference` picks up the new weights.

Trained artifacts larger than a few hundred megabytes should stay out of Git; store promoted weights in an artifact bucket or release package and update environment files accordingly.


## Custom image detection model

This is my custom training set. I've spent a few hours tagging both red and blue team heroes on the mini map.

This model will be used for object detection

[Roboflow Model Example](https://app.roboflow.com/heroes-of-the-storm/heroes-of-the-storm-teqko/models/heroes-of-the-storm-teqko/2)

