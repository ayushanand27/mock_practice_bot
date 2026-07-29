"""Sarvam AI TTS / STT via REST (api.sarvam.ai).

Uses:
  - POST /text-to-speech  (Bulbul) — speak interview questions
  - POST /speech-to-text  (Saaras/Saarika) — transcribe voice answers

Auth header: api-subscription-key (not Bearer).
"""

from __future__ import annotations

import base64
import logging
import os
from io import BytesIO

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sarvam.ai"
TTS_URL = f"{BASE_URL}/text-to-speech"
STT_URL = f"{BASE_URL}/speech-to-text"


def is_configured() -> bool:
    return bool(os.getenv("SARVAM_API_KEY", "").strip())


def _api_key() -> str:
    key = os.getenv("SARVAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SARVAM_API_KEY is not set")
    return key


def _decode_audio(data: dict) -> bytes | None:
    audios = data.get("audios") or data.get("audio")
    if isinstance(audios, list) and audios:
        return base64.b64decode(audios[0])
    if isinstance(audios, str):
        return base64.b64decode(audios)
    return None


async def text_to_speech(text: str, language_code: str = "en-IN") -> bytes | None:
    """Convert text to audio bytes. Returns None if Sarvam is unavailable."""
    if not is_configured():
        return None

    clipped = text[:2400]
    speaker = os.getenv("SARVAM_SPEAKER", "shubh")
    preferred = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3").strip() or "bulbul:v3"

    # Try preferred model first, then common fallbacks (v3 text body vs v2 inputs body).
    attempts: list[dict] = [
        {
            "text": clipped,
            "target_language_code": language_code,
            "speaker": speaker,
            "model": preferred,
        },
        {
            "inputs": [clipped],
            "target_language_code": language_code,
            "speaker": "anushka" if preferred.startswith("bulbul:v3") else speaker,
            "model": "bulbul:v2",
            "enable_preprocessing": True,
        },
    ]

    headers = {"api-subscription-key": _api_key(), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            last_err = None
            for body in attempts:
                resp = await client.post(TTS_URL, headers=headers, json=body)
                if resp.status_code >= 400:
                    last_err = f"{resp.status_code} {resp.text[:300]}"
                    logger.warning("Sarvam TTS attempt failed: %s", last_err)
                    continue
                audio = _decode_audio(resp.json())
                if audio:
                    return audio
                logger.warning("Sarvam TTS unexpected response for model=%s", body.get("model"))
            if last_err:
                logger.error("Sarvam TTS failed: %s", last_err)
    except Exception as exc:
        logger.exception("Sarvam TTS error: %s", exc)
    return None


async def speech_to_text(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transcribe audio bytes. Returns None if Sarvam is unavailable or fails."""
    if not is_configured():
        return None

    headers = {"api-subscription-key": _api_key()}
    attempts = [
        {
            "model": os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
            "mode": "transcribe",
            "language_code": os.getenv("SARVAM_STT_LANG", "unknown"),
        },
        {
            "model": "saarika:v2.5",
            "language_code": os.getenv("SARVAM_STT_LANG", "en-IN"),
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            for data in attempts:
                files = {"file": (filename, BytesIO(audio_bytes), "application/octet-stream")}
                resp = await client.post(STT_URL, headers=headers, files=files, data=data)
                if resp.status_code >= 400:
                    logger.warning(
                        "Sarvam STT attempt failed (%s): %s %s",
                        data.get("model"),
                        resp.status_code,
                        resp.text[:300],
                    )
                    continue
                result = resp.json()
                transcript = (
                    result.get("transcript")
                    or result.get("text")
                    or result.get("transcription")
                )
                if transcript:
                    return transcript
            logger.error("Sarvam STT failed for all model attempts")
    except Exception as exc:
        logger.exception("Sarvam STT error: %s", exc)
    return None
