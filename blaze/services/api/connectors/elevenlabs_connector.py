from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass
class ElevenLabsConfig:
    api_key: str
    default_voice_id: str
    stt_model_id: str
    tts_model_id: str


class ElevenLabsConnector:
    def __init__(self, config: ElevenLabsConfig) -> None:
        self.config = config

    def speak(self, text: str, voice_id: str | None = None) -> dict[str, Any]:
        if not text:
            return {"ok": False, "error": "text is required"}

        if not self.config.api_key:
            return {
                "ok": True,
                "mode": "mock",
                "voice_id": voice_id or self.config.default_voice_id or "default",
                "audio_base64": None,
                "text": text,
                "latency_ms": 0,
            }

        active_voice_id = voice_id or self.config.default_voice_id
        if not active_voice_id:
            return {"ok": False, "error": "voice_id is required when ELEVENLABS_DEFAULT_VOICE_ID is empty"}

        payload = {
            "text": text,
            "model_id": self.config.tts_model_id,
        }

        req = request.Request(
            url="https://api.elevenlabs.io/v1/text-to-speech/{voice}".format(voice=active_voice_id),
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "xi-api-key": self.config.api_key,
            },
        )

        start = time.time()
        try:
            with request.urlopen(req, timeout=30) as resp:
                audio = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            return {"ok": False, "error": "ElevenLabs TTS failed: HTTP {code}: {detail}".format(code=exc.code, detail=detail)}
        latency_ms = int((time.time() - start) * 1000)

        return {
            "ok": True,
            "mode": "live",
            "voice_id": active_voice_id,
            "latency_ms": latency_ms,
            "audio_base64": base64.b64encode(audio).decode("ascii"),
        }

    def _multipart_body(
        self, file_bytes: bytes, filename: str, model_id: str
    ) -> tuple[bytes, str]:
        boundary = "----BlazeV4Boundary{token}".format(token=uuid.uuid4().hex)
        chunks = []

        def _append(line: str) -> None:
            chunks.append(line.encode("utf-8"))

        _append("--{b}\r\n".format(b=boundary))
        _append('Content-Disposition: form-data; name="model_id"\r\n\r\n')
        _append("{model}\r\n".format(model=model_id))

        _append("--{b}\r\n".format(b=boundary))
        _append('Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.format(name=filename))
        _append("Content-Type: audio/mpeg\r\n\r\n")
        body = b"".join(chunks) + file_bytes + "\r\n".encode("utf-8")
        body += "--{b}--\r\n".format(b=boundary).encode("utf-8")
        return body, boundary

    def transcribe(self, audio_base64: str | None, text_hint: str | None = None) -> dict[str, Any]:
        if not self.config.api_key:
            return {
                "ok": True,
                "mode": "mock",
                "text": text_hint or "Transcription unavailable (ELEVENLABS_API_KEY missing)",
                "confidence": 0.0,
                "latency_ms": 0,
            }

        if not audio_base64:
            return {
                "ok": True,
                "mode": "fallback",
                "text": text_hint or "No audio payload supplied",
                "confidence": 0.0,
                "latency_ms": 0,
            }

        try:
            audio_bytes = base64.b64decode(audio_base64, validate=True)
        except Exception:
            return {
                "ok": False,
                "mode": "error",
                "error": "audio_base64 is invalid",
            }

        body, boundary = self._multipart_body(audio_bytes, "audio.mp3", self.config.stt_model_id)
        req = request.Request(
            url="https://api.elevenlabs.io/v1/speech-to-text",
            method="POST",
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary={b}".format(b=boundary),
                "xi-api-key": self.config.api_key,
            },
        )

        start = time.time()
        try:
            with request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            latency_ms = int((time.time() - start) * 1000)
            text = (
                payload.get("text")
                or payload.get("transcript")
                or payload.get("result", {}).get("text")
            )
            return {
                "ok": True,
                "mode": "live",
                "text": text or text_hint or "",
                "confidence": float(payload.get("confidence", 0.8)),
                "latency_ms": latency_ms,
                "raw": payload,
            }
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            # Graceful degradation keeps the workflow moving even if provider call fails.
            return {
                "ok": True,
                "mode": "fallback",
                "text": text_hint or "STT failed; falling back to text hint.",
                "confidence": 0.0,
                "latency_ms": int((time.time() - start) * 1000),
                "provider_error": "HTTP {code}: {detail}".format(code=exc.code, detail=detail),
            }
        except Exception as exc:
            return {
                "ok": True,
                "mode": "fallback",
                "text": text_hint or "STT failed; falling back to text hint.",
                "confidence": 0.0,
                "latency_ms": int((time.time() - start) * 1000),
                "provider_error": str(exc),
            }

