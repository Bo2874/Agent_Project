FROM python:3.11.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

COPY --chown=65532:65532 runner.py /opt/sandbox/runner.py

USER 65532:65532
WORKDIR /workspace

ENTRYPOINT ["python", "-I", "-B", "/opt/sandbox/runner.py"]
