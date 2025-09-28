# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libjpeg-dev \
        libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY detection ./detection
COPY README.md ./README.md

# Install project dependencies into a local virtual environment
RUN uv sync --frozen --no-dev

ENV PATH="/workspace/.venv/bin:${PATH}"

# Copy remaining project files (e.g. tests) last to leverage Docker layer caching
COPY . .

# Default entrypoint runs the packaged CLI; override CMD for custom arguments
ENTRYPOINT ["hero-inference"]
CMD ["--help"]
