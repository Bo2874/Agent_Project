ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
FROM ${PYTHON_IMAGE} AS code
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY infra/docker/artifact-sandbox/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY artifact_runtime/ /usr/local/lib/python3.11/site-packages/artifact_runtime/
USER 65532:65532
WORKDIR /output
CMD ["python", "-I", "-B", "-m", "artifact_runtime.execute"]
