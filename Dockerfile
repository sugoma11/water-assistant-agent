# Backend image: the `water-assistant` FastAPI + AG-UI + ADK service.
#
# Multi-stage uv build. The builder resolves the *runtime* dependency set only
# (`--no-dev`), so the heavy prompt-optimization/experiment deps (mlflow, gepa,
# argilla, …) never enter the image. Both stages share the same interpreter path
# (`/usr/local/bin/python3.13`): the uv image is itself built on
# `python:3.13-slim-bookworm`, so the venv copied into the runtime stage stays
# valid.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# git: `ag-ui-adk` is a git dependency (see [tool.uv.sources]).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first (cached across source changes): install into the venv
# from the lockfile without the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Then the project source + editable install.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

# psycopg2-binary and duckdb ship self-contained wheels, so no libpq/system libs
# are needed at runtime.
WORKDIR /app
COPY --from=builder /app /app

# Put the venv on PATH so the `water-assistant` console script resolves.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Bind-all inside the container; the host maps the port (compose). 8080 matches
# WATER_ASSISTANT_PORT's default.
EXPOSE 8080

# `water-assistant` runs uvicorn against the ASGI app (see settings for HOST/PORT
# and the mandatory AGENT_JWT_SECRET / WATER_ASSISTANT_SESSION_DB_URL, D5).
CMD ["water-assistant"]
