FROM docker:28.3-cli@sha256:0135662b510037ea581d99c2e5929c5e01185139c0b86986a418bd4da0b98a44 AS docker-cli
# Debian UNO and Python must use the same interpreter. One image, two trusted roles.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-uno \
       libreoffice-calc libreoffice-writer poppler-utils fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/venv \
    && mkdir -p /var/lib/artifact-runner
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 HOME=/tmp \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 BLIS_NUM_THREADS=1 MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache
COPY infra/docker/python-runtime/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY artifact_runtime/ /opt/venv/lib/python3.11/site-packages/artifact_runtime/
USER 65532:65532
WORKDIR /output
CMD ["python", "-I", "-B", "-m", "artifact_runtime.execute"]
