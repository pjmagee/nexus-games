# Hero Training

CLI utilities for fine-tuning Ultralytics YOLO models on Nexus Games annotation datasets.

## Quick start

1. Ensure [uv](https://github.com/astral-sh/uv) is installed.
1. Create the environment and install dependencies:

```powershell
uv venv .venv
uv pip install -e .
```

1. Launch a dry-run to validate configuration (no training will start):

```powershell
.venv\Scripts\hero-train train --config configs/yolo11n.yaml --dry-run
```

1. Start full training (example uses bundled config):

```powershell
.venv\Scripts\hero-train train --config configs/yolo11n.yaml --device 0
```

## Configuration

Training settings live in YAML files inside `configs/`. The default `yolo11n.yaml` points to the dataset exported from RoboFlow at `training/data-sets/yolov12`.

Each config mirrors the Ultralytics CLI arguments documented in [their train guide](https://docs.ultralytics.com/modes/train/). Important fields:

- `model`: Pretrained checkpoint or model YAML (e.g. `yolo11n.pt`).
- `data`: Dataset YAML with `path`, `train`, `val`, and `names` entries.
- `epochs`, `batch`, `imgsz`, `device`: Passed straight through to `yolo detect train`.
- `resume`: Set `true` to continue from the last checkpoint for the run.

Override any field via CLI options (for example `--epochs 300 --imgsz 640`). For uncommon Ultralytics args add them with repeated `--arg` flags: `--arg lr0=0.01 --arg warmup_epochs=3`.

To call the underlying CLI directly, follow the Ultralytics syntax:

```powershell
yolo detect train data=training/data-sets/yolov12/data.yaml model=yolo11n.pt epochs=100 imgsz=1280 batch=16 device=cuda:0
```

For multi-GPU runs use `--device 0,1` (or `--device -1,-1` for the two most idle GPUs). Resume interrupted runs with either the CLI wrapper:

```powershell
.venv\Scripts\hero-train train --resume --config configs/yolo11n.yaml
```

Or call Ultralytics directly:

```powershell
yolo train resume model=outputs/hero-yolo/weights/last.pt
```

Reference tables for every argument are available in the Ultralytics [configuration docs](https://docs.ultralytics.com/usage/cfg/).

## Outputs

Training runs are stored beneath `outputs/<run-name>` (gitignored). Promote the best checkpoint into `models/` and update the `DETECTION_MODEL_PATH` used by `hero-inference` to deploy.
