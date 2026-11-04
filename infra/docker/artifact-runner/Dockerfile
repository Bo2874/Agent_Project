ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
FROM docker:28.3-cli@sha256:0135662b510037ea581d99c2e5929c5e01185139c0b86986a418bd4da0b98a44 AS docker-cli
FROM ${PYTHON_IMAGE}
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY infra/docker/artifact-runner/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && mkdir -p /var/lib/artifact-runner
COPY artifact_runtime/ /usr/local/lib/python3.11/site-packages/artifact_runtime/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "artifact_runtime.app:application_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
