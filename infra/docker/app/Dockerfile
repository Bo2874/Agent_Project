ARG PYTHON_IMAGE=python:3.11-slim-bookworm

FROM ${PYTHON_IMAGE} AS dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
COPY pyproject.toml ./
# Resolve only application dependencies. Day 79 introduces a cross-platform dependency lock.
RUN python -c "import pathlib,tomllib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); pathlib.Path('requirements.txt').write_text('\n'.join(p['project']['dependencies'])+'\n')" \
    && /opt/venv/bin/pip install --no-cache-dir --progress-bar off -r requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --no-create-home agent \
    && mkdir -p /app/data/documents \
    && chown -R agent:agent /app
COPY --chown=10001:10001 pyproject.toml alembic.ini ./
COPY --chown=10001:10001 agent_core/ ./agent_core/
COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 frontend/ ./frontend/
COPY --chown=10001:10001 skills/ ./skills/
COPY --chown=10001:10001 scripts/ ./scripts/
COPY --chown=10001:10001 migrations/ ./migrations/
USER 10001:10001

FROM runtime AS app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-m", "scripts.check_container_health", "app"]
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS worker
HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=6 \
    CMD ["python", "-m", "scripts.check_container_health", "worker"]
CMD ["python", "-m", "scripts.run_background_worker"]
