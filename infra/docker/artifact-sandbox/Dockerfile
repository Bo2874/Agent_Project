ARG PYTHON_IMAGE=python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
FROM ${PYTHON_IMAGE} AS code
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY infra/docker/artifact-sandbox/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY artifact_runtime/ /usr/local/lib/python3.11/site-packages/artifact_runtime/
USER 65532:65532
WORKDIR /output
CMD ["python", "-I", "-B", "-m", "artifact_runtime.execute"]

# Debian's UNO binding must use the matching system interpreter.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS render
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-uno \
       libreoffice-calc libreoffice-writer poppler-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 HOME=/tmp
COPY infra/docker/artifact-sandbox/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY artifact_runtime/ /opt/venv/lib/python3.11/site-packages/artifact_runtime/
USER 65532:65532
WORKDIR /output
CMD ["python", "-I", "-B", "-m", "artifact_runtime.execute"]
