# Nexus Games Auto-Spectator (Proof of concept)

This is a proof of concept for a system that automatically spectates games in Heroes of the Storm and streams them to Twitch.

## Components Overview

- **game-capture** (C++ / Win32 + Direct3D 11): Captures the Heroes of the Storm game window once per second and writes atomic BMP frame files to `sessions/current/frames`.

- **hero-detection** (Python 3.12): Reads frame BMPs, runs YOLO-based (or synthetic fallback) detection, outputs per-frame `.detections.json` sidecars and annotated JPG overlays.

- **session-manager** (Python 3.12): Coordinates replay session lifecycle state (queue → active → completed) and supervises heartbeat/state files.

- **game-controller** (C# .NET 9): Consumes detection sidecars to drive automated camera panning via synthesized middle-mouse drags.

- **orchestrator** (Python 3.12): (Planned / Partial) High-level process supervisor to launch and monitor capture, detection, controller, and future pipeline actors.

- **replay-harvestor** (C# .NET 9): (Phase 1 local focus) Scans local replay directories and feeds `.StormReplay` files into the processing queue (remote ingestion deferred).

- **(future) analytics / classifier** (Python / ML): Will provide hero-specific recognition, fight detection, and advanced scene understanding.

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



## Custom image detection model

This is my custom training set. I've spent a few hours tagging both red and blue team heroes on the mini map.

This model will be used for object detection

[Roboflow Model Example](https://app.roboflow.com/heroes-of-the-storm/heroes-of-the-storm-teqko/models/heroes-of-the-storm-teqko/2)

