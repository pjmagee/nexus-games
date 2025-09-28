from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import yaml
from ultralytics import YOLO


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, Mapping):
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

            local_candidate = base_dir / candidate
            resolved[key] = (
                str(local_candidate.resolve()) if local_candidate.exists() else value
            )
        else:
            resolved[key] = value
    return resolved


def _parse_overrides(pairs: Iterable[str]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Override '{pair}' must be in key=value format")
        key, raw_value = pair.split("=", 1)
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError as exc:  # pragma: no cover - runtime validation
            raise ValueError(f"Invalid value for '{key}': {raw_value}") from exc
        overrides[key] = value
    return overrides


def _summarise(params: Mapping[str, Any]) -> None:
    print("Ultralytics training arguments:")
    for key in sorted(params.keys()):
        print(f"  {key}: {params[key]}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch Ultralytics YOLO training with a minimal wrapper.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional YAML config mirroring Ultralytics defaults (e.g. configs/yolo11n.yaml).",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Path or hub ID for the dataset YAML.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Pretrained .pt weights or model YAML.",
    )
    parser.add_argument(
        "--epochs", type=int, default=None, help="Number of training epochs."
    )
    parser.add_argument("--imgsz", type=int, default=None, help="Training image size.")
    parser.add_argument("--batch", type=int, default=None, help="Batch size.")
    parser.add_argument(
        "--device",
        default=None,
        help="Torch device string, e.g. '0', '0,1', 'cpu', 'mps'.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Directory for Ultralytics run outputs.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Run name inside the project directory.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume training from the last checkpoint.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the composed Ultralytics call without running.",
    )
    parser.add_argument(
        "--arg",
        dest="extra_args",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Additional Ultralytics key=value overrides (repeatable).",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    params: Dict[str, Any] = {}
    if args.config is not None:
        try:
            params.update(_load_config(args.config.resolve()))
        except (FileNotFoundError, TypeError) as exc:
            parser.error(str(exc))

    cli_overrides = {
        key: value
        for key, value in (
            ("data", args.data),
            ("model", args.model),
            ("epochs", args.epochs),
            ("imgsz", args.imgsz),
            ("batch", args.batch),
            ("device", args.device),
            ("project", args.project),
            ("name", args.name),
        )
        if value is not None
    }
    params.update(cli_overrides)

    if args.extra_args:
        try:
            params.update(_parse_overrides(args.extra_args))
        except ValueError as exc:
            parser.error(str(exc))

    if args.resume:
        params["resume"] = True

    if "model" not in params or "data" not in params:
        parser.error(
            "Both 'model' and 'data' must be provided via config or CLI options."
        )

    _summarise(params)

    if args.dry_run:
        print("Dry run requested; skipping Ultralytics call.")
        return 0

    model_path = str(params.pop("model"))
    print(f"Loading model '{model_path}'")
    yolo_model = YOLO(model_path)

    print("Starting Ultralytics training...")
    results = yolo_model.train(**params)
    print("Training finished.")
    print(f"Results saved to: {results.save_dir}")
    return 0


def run() -> None:
    """Entry point for console script."""

    sys.exit(main())


if __name__ == "__main__":
    run()
