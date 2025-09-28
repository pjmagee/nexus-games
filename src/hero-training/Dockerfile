# syntax=docker/dockerfile:1.7

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install Python 3.12 and system dependencies required by Ultralytics / OpenCV
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        software-properties-common \
        curl \
        git \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        pkg-config \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        python3.12-dev \
        python3-distutils \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}" \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/workspace/.venv \
    UV_PYTHON=python3.12

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY README.md ./README.md
COPY configs ./configs
COPY hero_training ./hero_training

RUN uv sync --frozen --no-dev

ENV PATH="/workspace/.venv/bin:${PATH}"

# Mount dataset/output directories at runtime (see docker-compose.yml)
VOLUME ["/workspace/training", "/workspace/outputs"]

ENTRYPOINT ["hero-train"]
CMD ["--help"]
