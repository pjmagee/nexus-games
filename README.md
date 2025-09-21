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


## Custom image detection model

This is my custom training set. I've spent a few hours tagging both red and blue team heroes on the mini map.

This model will be used for object detection

https://app.roboflow.com/heroes-of-the-storm/heroes-of-the-storm-teqko/models/heroes-of-the-storm-teqko/2

