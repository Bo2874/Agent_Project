ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
# Check freshness and install the locked runtime closure without building the local package.
RUN uv sync --locked --no-dev --no-install-project --no-build --no-cache \
    && uv pip check --python /opt/venv/bin/python

FROM dependencies AS ingestion-dependencies
RUN uv sync --locked --extra ingestion --no-dev --no-install-project --no-build --no-cache \
    && uv pip check --python /opt/venv/bin/python

# CI uses the same lock and interpreter; test dependencies never enter the runtime image.
FROM dependencies AS test
RUN uv sync --locked --extra dev --no-install-project --no-build --no-cache
WORKDIR /app
COPY . .
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q"]

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --no-create-home agent \
    && mkdir -p /app/data/documents /app/data/models \
    && chown -R agent:agent /app
COPY --chown=10001:10001 pyproject.toml uv.lock .python-version alembic.ini ./
COPY --chown=10001:10001 agent_core/ ./agent_core/
COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 frontend/ ./frontend/
COPY --chown=10001:10001 skills/ ./skills/
COPY --chown=10001:10001 plugins/manifests/ ./plugins/manifests/
COPY --chown=10001:10001 plugins/stdio/ ./plugins/stdio/
COPY --chown=10001:10001 plugins/http/ ./plugins/http/
COPY --chown=10001:10001 plugins/packages.json ./plugins/packages.json
COPY --chown=10001:10001 plugins/packages/ ./plugins/packages/
COPY --chown=10001:10001 scripts/ ./scripts/
COPY --chown=10001:10001 migrations/ ./migrations/
USER 10001:10001

FROM runtime AS app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-m", "scripts.check_container_health", "app"]
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS worker
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ingestion-dependencies /opt/venv /opt/venv
USER 10001:10001
HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=6 \
    CMD ["python", "-m", "scripts.check_container_health", "worker"]
CMD ["python", "-m", "scripts.run_background_worker"]
