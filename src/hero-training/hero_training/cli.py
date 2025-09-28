from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table
from ultralytics import YOLO

console = Console()
app = typer.Typer(add_completion=False, help="Hero model training utilities")


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise TypeError(
            f"Expected mapping in config file {path}, received {type(raw).__name__}"
        )

    base_dir = path.parent
    resolved: Dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"data", "project", "model"} and isinstance(value, str):
            candidate = Path(value)
            if candidate.is_absolute():
                resolved[key] = str(candidate)
                continue

            if len(candidate.parts) > 1:
                resolved[key] = str((base_dir / candidate).resolve())
                continue

            # bare filenames (e.g. "yolo11n.pt") should pass through unless a local copy exists
            local_candidate = base_dir / candidate
            resolved[key] = (
                str(local_candidate.resolve()) if local_candidate.exists() else value
            )
        else:
            resolved[key] = value
    return resolved


def _parse_overrides(pairs: List[str]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"Override '{pair}' must be in key=value format")
        key, raw_value = pair.split("=", 1)
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError as exc:  # pragma: no cover - runtime validation
            raise typer.BadParameter(f"Invalid value for '{key}': {raw_value}") from exc
        overrides[key] = value
    return overrides


def _summarise(params: Dict[str, Any]) -> None:
    table = Table(title="Ultralytics Train Call", show_lines=False)
    table.add_column("Argument", justify="left")
    table.add_column("Value", justify="left")
    for key in sorted(params.keys()):
        display = params[key]
        table.add_row(key, str(display))
    console.print(table)


@app.command()
def train(
    config: Optional[Path] = typer.Option(
        None,
        dir_okay=False,
        readable=True,
        help="Optional YAML config mirroring Ultralytics defaults (e.g. configs/yolo11n.yaml).",
    ),
    data: Optional[str] = typer.Option(
        None, help="Path or hub ID for the dataset YAML."
    ),
    model: Optional[str] = typer.Option(
        None, help="Pretrained .pt weights or model YAML."
    ),
    epochs: Optional[int] = typer.Option(None, help="Number of training epochs."),
    imgsz: Optional[int] = typer.Option(None, help="Training image size."),
    batch: Optional[int] = typer.Option(None, help="Batch size."),
    device: Optional[str] = typer.Option(
        None, help="Torch device string, e.g. '0', '0,1', 'cpu', 'mps'."
    ),
    project: Optional[str] = typer.Option(
        None, help="Directory for Ultralytics run outputs."
    ),
    name: Optional[str] = typer.Option(
        None, help="Run name inside the project directory."
    ),
    resume: bool = typer.Option(
        False, help="Resume training from the last checkpoint."
    ),
    dry_run: bool = typer.Option(
        False, help="Print the composed Ultralytics call without running."
    ),
    arg: List[str] = typer.Option(
        [],
        "--arg",
        help="Additional Ultralytics key=value overrides (e.g. --arg lr0=0.01).",
    ),
) -> None:
    """Launch Ultralytics YOLO training with arguments matching the official CLI."""

    params: Dict[str, Any] = {}
    if config is not None:
        params.update(_load_config(config.resolve()))

    overrides = {
        key: value
        for key, value in (
            ("data", data),
            ("model", model),
            ("epochs", epochs),
            ("imgsz", imgsz),
            ("batch", batch),
            ("device", device),
            ("project", project),
            ("name", name),
        )
        if value is not None
    }
    params.update(overrides)
    params.update(_parse_overrides(arg))

    if resume:
        params["resume"] = True

    if "model" not in params or "data" not in params:
        raise typer.BadParameter(
            "Both 'model' and 'data' must be provided via config or CLI options."
        )

    _summarise(params)

    if dry_run:
        console.log("Dry run requested; skipping Ultralytics call.")
        return

    console.log(f"Loading model '{params['model']}'")
    model_path = str(params.pop("model"))
    yolo_model = YOLO(model_path)

    console.log("Starting Ultralytics training...")
    results = yolo_model.train(**params)
    console.log("Training finished.")
    console.log(f"Results saved to: {results.save_dir}")


def run() -> None:
    """Entry point for console script."""

    app()


if __name__ == "__main__":
    run()
