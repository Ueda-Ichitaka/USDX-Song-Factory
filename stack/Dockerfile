# syntax=docker/dockerfile:1
# Docker image for UltraSinger + batch orchestration stack.
#
# The build arg TORCH_INDEX selects the PyTorch flavor:
#   CPU (default): https://download.pytorch.org/whl/cpu
#   AMD ROCm:      https://download.pytorch.org/whl/rocm6.4
#
# The project pins torch==2.8.0 / torchvision==0.23.0 / torchaudio==2.8.0
# (pyproject.toml). PyTorch is installed FIRST from the selected index, so the
# pins are satisfied without pulling the multi-GB CUDA build from PyPI.
FROM python:3.12-slim-bookworm

ARG TORCH_INDEX="https://download.pytorch.org/whl/cpu"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        git \
        build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

WORKDIR /app/UltraSinger

# PyTorch in the selected flavor (cpu / rocm6.4)
RUN uv pip install --system --no-cache \
        torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
        --index-url ${TORCH_INDEX}

# Project dependencies (pyproject.toml only -> layer-cache friendly)
COPY pyproject.toml ./
RUN mkdir -p src \
    && uv pip install --system --no-cache -e .

# Orchestrator-only dependencies (stack/orchestrator/romanize.py,
# lyrics_fetch.py) - kept out of pyproject.toml so native/non-docker
# UltraSinger installs stay lean.
# cyrtranslit: Cyrillic (Russian/Ukrainian) -> Latin
# pykakasi: Japanese (kana/kanji) -> romaji
# korean-romanizer: Hangul -> Revised Romanization of Korean
# syncedlyrics: free, no-key lyrics lookup (LRCLIB/NetEase/Musixmatch/...)
# lyricsgenius: Genius.com lyrics lookup (needs GENIUS_API_KEY, optional)
RUN uv pip install --system --no-cache \
        cyrtranslit pykakasi korean-romanizer syncedlyrics lyricsgenius

# Application sources
COPY . /app/UltraSinger
COPY stack/orchestrator /app/orchestrator

# Model- and tool-caches live on the mounted volume /data/models so that
# downloaded models (whisper, demucs, wav2vec2 aligners, ...) survive
# container restarts.
ENV HF_HOME=/data/models/huggingface \
    TORCH_HOME=/data/models/torch \
    XDG_CACHE_HOME=/data/models/xdg \
    NLTK_DATA=/data/models/nltk \
    MPLCONFIGDIR=/data/models/mpl

# convenience: `progress` command inside the container
RUN printf '#!/bin/sh\nexec python /app/orchestrator/orchestrator.py progress "$@"\n' \
        > /usr/local/bin/progress \
    && chmod +x /usr/local/bin/progress \
    && useradd --uid 1000 --create-home singer \
    && chown -R 1000:1000 /app

# run as the unprivileged host user (uid 1000) so that output files on the
# mounted volumes are owned by the host user
USER 1000:1000

WORKDIR /app/UltraSinger/src
