FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip --timeout 120 --retries 10 install --requirement requirements.txt

COPY worker.py ./

HEALTHCHECK --interval=2s --timeout=2s --start-period=5m --retries=15 \
    CMD test -f /tmp/tts-worker-ready || exit 1

CMD ["python", "-u", "worker.py"]
