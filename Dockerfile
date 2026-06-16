# syntax=docker/dockerfile:1

# ---- build: resolve dependencies into a venv from the lockfile -------------
FROM python:3.13-slim-bookworm AS build

# uv gives a fast, uv.lock-reproducible install. Installed via pip so the
# build only pulls from the base image + PyPI (no extra registry).
RUN pip install --no-cache-dir uv

# Use the slim image's own Python (the repo's pyproject prefers uv-managed
# Python, which is wrong inside a container) and never download one.
ENV UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency layer: re-runs only when the lockfile or manifest changes.
# --no-install-project: package=false, so there is nothing to build — we copy
# the app source into the runtime image directly.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ---------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# Run as an unprivileged user; libgit2 ships inside the pygit2 wheel, so no
# system git/libgit2 packages are needed.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY scripts ./scripts

# SQLite DB + cloned repos live here; mount a volume to persist them.
RUN mkdir -p /app/data && chown -R app:app /app
USER app
VOLUME ["/app/data"]

EXPOSE 8000

# Bind to 0.0.0.0 so the port is reachable when published.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
