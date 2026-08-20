from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import threading
import time
import wave
from typing import Any

import redis
from piper import PiperVoice


QUEUE_NAME = "tts_jobs"
END_MARKER = b"__END__"
LEADING_PADDING_MS = 1000

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
PIPER_DATA_DIR = Path(os.environ.get("PIPER_DATA_DIR", "/data"))
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-lessac-low")
CACHE_TTL_SECONDS = int(os.environ.get("TTS_CACHE_TTL_SECONDS", "3600"))
READY_FILE = Path("/tmp/tts-worker-ready")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("starlight-tts-worker")
SHUTDOWN = threading.Event()


def ensure_voice_model() -> Path:
    """Download the configured Piper voice once and return its ONNX path."""
    PIPER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    model_path = PIPER_DATA_DIR / f"{PIPER_VOICE}.onnx"
    config_path = PIPER_DATA_DIR / f"{PIPER_VOICE}.onnx.json"

    if model_path.is_file() and config_path.is_file():
        return model_path

    LOGGER.info("Downloading Piper voice %s", PIPER_VOICE)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "piper.download_voices",
            "--data-dir",
            str(PIPER_DATA_DIR),
            PIPER_VOICE,
        ],
        check=True,
    )

    if not model_path.is_file() or not config_path.is_file():
        raise FileNotFoundError(
            f"Piper did not download both model files for {PIPER_VOICE}"
        )

    return model_path


def synthesize_ogg(voice: PiperVoice, text: str) -> bytes:
    """Synthesize text once and encode it as a client-compatible Ogg stream."""
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)

    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            # The game client skips the first second of each provider chunk.
            "-af",
            f"adelay={LEADING_PADDING_MS}:all=1",
            "-c:a",
            "libvorbis",
            "-q:a",
            "2",
            "-f",
            "ogg",
            "pipe:1",
        ],
        input=wav_buffer.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg failed to encode TTS audio: {error}")

    return result.stdout


def result_channel(job: dict[str, Any]) -> str:
    """Match the result channel naming used by the game server."""
    effect = int(job.get("e", 0))
    base = f"result:2:{job['id']}"
    return f"{base}:{effect}" if effect else base


def cache_key(job: dict[str, Any]) -> str:
    """Match the cache key naming used by the game server."""
    voice = str(job.get("r", "0"))
    effect = int(job.get("e", 0))
    text = str(job["t"])
    return (
        f"cache:2:{voice}:{effect}:{text}"
        if effect
        else f"cache:2:{voice}:{text}"
    )


def cache_audio(client: redis.Redis, job: dict[str, Any], audio: bytes) -> None:
    """Store the length-prefixed chunk format consumed by TTSClient.GetCache."""
    packed_audio = struct.pack("<I", len(audio)) + audio
    if CACHE_TTL_SECONDS > 0:
        client.set(cache_key(job), packed_audio, ex=CACHE_TTL_SECONDS)
    else:
        client.set(cache_key(job), packed_audio)


def process_job(client: redis.Redis, voice: PiperVoice, raw_job: bytes) -> None:
    """Generate and publish a single independently playable Ogg chunk."""
    job = json.loads(raw_job)
    channel = result_channel(job)

    try:
        text = str(job["t"]).strip()
        if not text:
            raise ValueError("TTS job text is empty")

        queued_at_ms = int(job.get("ts", 0) or 0)
        queue_delay_ms = max(0, int(time.time() * 1000) - queued_at_ms)
        LOGGER.info(
            "Sending provider request %s: queue delay %dms, requested voice %s, "
            "effect %s, exact text=%r",
            job["id"],
            queue_delay_ms,
            job.get("r", "0"),
            job.get("e", 0),
            text,
        )

        started = time.monotonic()
        audio = synthesize_ogg(voice, text)
        cache_audio(client, job, audio)

        # Byte zero is the sequence number expected by the C# subscriber.
        client.publish(channel, b"\x00" + audio)
        LOGGER.info(
            "Generated job %s in %.2fs (%d Ogg bytes; requested voice %s, effect %s)",
            job["id"],
            time.monotonic() - started,
            len(audio),
            job.get("r", "0"),
            job.get("e", 0),
        )
    except Exception:
        LOGGER.exception("Failed to generate TTS job %s", job.get("id", "<unknown>"))
    finally:
        # Always finish the stream so clients do not wait for the server timeout.
        client.publish(channel, END_MARKER)


def connect_redis() -> redis.Redis:
    """Wait for Redis so container startup order and restarts are harmless."""
    while not SHUTDOWN.is_set():
        client = redis.Redis.from_url(REDIS_URL, decode_responses=False)
        try:
            client.ping()
            return client
        except redis.RedisError as error:
            LOGGER.warning("Waiting for Redis at %s: %s", REDIS_URL, error)
            SHUTDOWN.wait(1)

    raise RuntimeError("Worker stopped before Redis became available")


def request_shutdown(_signum: int, _frame: object) -> None:
    SHUTDOWN.set()


def main() -> int:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    READY_FILE.unlink(missing_ok=True)

    model_path = ensure_voice_model()
    LOGGER.info("Loading Piper voice from %s", model_path)
    voice = PiperVoice.load(str(model_path))
    LOGGER.info("TTS worker ready; all requested voices map to %s", PIPER_VOICE)

    client = connect_redis()
    READY_FILE.touch()
    while not SHUTDOWN.is_set():
        try:
            item = client.brpop(QUEUE_NAME, timeout=1)
            if item is not None:
                _, raw_job = item
                process_job(client, voice, raw_job)
        except redis.RedisError:
            LOGGER.exception("Redis connection failed; reconnecting")
            client = connect_redis()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            LOGGER.exception("Discarding invalid TTS job")

    READY_FILE.unlink(missing_ok=True)
    LOGGER.info("TTS worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
