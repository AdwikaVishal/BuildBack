# syntax=docker/dockerfile:1
#
# RippleBack container image. One image serves every role; docker-compose (or
# the two commands below) runs the MCP server and the API adapter separately.
#
#   docker run --rm -p 8000:8000 <image> rippleback-mcp       # MCP server
#   docker run --rm -p 8001:8001 <image> rippleback-adapter   # dashboard + REST
#
# The package is installed editable so `adapter._static_dir()` can find the
# bundled frontend build at /app/frontend/dist.

# ── Stage 1: build the React dashboard ────────────────────────────────────────
FROM node:20-alpine AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (best layer caching): pyproject + package sources.
COPY pyproject.toml ./
COPY src ./src
# Editable install keeps __file__ under /app/src so the static-dir lookup works.
RUN pip install -e .

# Data + docs bundled for the demo, plus the built dashboard from stage 1.
COPY samples ./samples
COPY docs ./docs
COPY frontend/dist ./frontend/dist

# MCP server and API adapter.
EXPOSE 8000 8001

# Database lives on a volume so both processes can share it.
VOLUME ["/data"]
ENV RIPPLEBACK_DB_URL=sqlite:////data/rippleback.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=3).status==200 else 1)"

# Default to the dashboard/adapter; override with `rippleback-mcp`.
CMD ["rippleback-adapter"]
