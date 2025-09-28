---
applyTo: '**'
---

You are creating an auto-spectator for watching 'Heroes of the Storm' `.StormReplay` files.

# Game Capture

src/game-capture:

- C++ project to capture into sessions/current/frames folder
- detect correct processes to capture frames from


# Game controller

src/game-controller:

- C# project which reads detections.json files from sessions/current/frames folder
- deltas and smoothing of input messages to heroes of the storm game Window
- middle mouse button and dragging over mini-map area using detections.json files

# Hero Inference

src/hero-inference:

- uses the trained model to product both .annotated.jpg and .detections.json files
- uses ultralytics, opencv, python loading images from sessions/current/frames

https://docs.ultralytics.com/modes/predict/#inference-sources

# Hero Training

src/hero-training

- trains a yolo model based on an annotated dataset of images with classes
- will train on various things:
- red player, blue player, red tower, blue tower, red nexus, blue nexus
- later enhancements will include camps, bosses, objectives

https://docs.ultralytics.com/modes/train/#usage-examples

- Ultralytics CLI syntax: `yolo detect train data=<dataset.yaml> model=<weights.pt> epochs=<int> imgsz=<int> batch=<int> device=<device>`
- Resume a run from the last checkpoint with `yolo train resume model=path/to/last.pt`
- Dataset YAML must declare `path`, `train`, `val`, (optional `test`), and a `names` map matching Nexus classes
- Preferred checkpoints start from official YOLO11 `.pt` weights before fine-tuning on our dataset


## Core Libraries / Frameworks

- WinRT Graphics: https://learn.microsoft.com/en-us/uwp/api/?view=winrt-26100
- Ultralytics: https://github.com/ultralytics/ultralytics
- OpenCV: https://github.com/opencv/opencv-python
- NVIDIA CUDA Toolkit: https://developer.nvidia.com/cuda-downloads

## Python Env setup

- use Python 3.12
- use UV
- use ruff linter

## .NET Env setup

- MUST use .NET10 SDK
