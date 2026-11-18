# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.12-slim AS builder

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

ARG UV_EXTRAS=""

WORKDIR /app

# Dependency layer: cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project $UV_EXTRAS

# Application layer: code, versioned rules and prompts travel with the image.
COPY src ./src
COPY rules ./rules
COPY prompts ./prompts
RUN uv sync --frozen --no-dev $UV_EXTRAS


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 1001 dataops \
    && useradd --system --uid 1001 --gid dataops --create-home dataops

WORKDIR /app
COPY --from=builder --chown=dataops:dataops /app /app

USER dataops

# Cloud Run overrides command/args per workload:
#   job:       command ["dataops"], args ["run-daily"]
#   dashboard: command ["streamlit"], args ["run", "src/ai_dataops/dashboard/app.py", ...]
CMD ["dataops", "version"]
