"""Moss HTTP client and shared credential/config resolution."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.mosi.cn/v1"
MODEL_TTS = "moss-tts-1.5-flash"
MODEL_TTSD = "moss-ttsd-1.0"
MODEL_VOICE_GENERATOR = "moss-voice-generator-1.0"

DOC_VOICES = [
    {
        "voice_id": "c6c0a40a-ea82-4468-9a21-333d3c4a76f6",
        "name": "曼波有口音版",
        "lang": "中文·普通话",
        "desc": "动漫、角色配音·轻快、可爱、搞笑",
    },
    {
        "voice_id": "f80b6698-0066-430b-88a0-f0fb8796db34",
        "name": "明太祖",
        "lang": "中文·普通话",
        "desc": "角色配音、影视·高冷、沉稳、霸总",
    },
    {
        "voice_id": "ddc6e38b-6f55-4415-b21b-a88cad2cc1d9",
        "name": "VOX AKUMA",
        "lang": "英文·英式英语",
        "desc": "播客、社交媒体·慵懒、磁性、专业",
    },
    {
        "voice_id": "7662a8a1-700c-466a-b66b-57ece9e2e231",
        "name": "李白",
        "lang": "中文·普通话",
        "desc": "影视、人物·古风、热血",
    },
    {
        "voice_id": "f9a1416b-d006-4b77-9581-8f0e8ec1e401",
        "name": "旁白Jake",
        "lang": "英文·英式英语",
        "desc": "角色配音、人物·沉稳、磁性、专业",
    },
    {
        "voice_id": "faf7f550-0627-4fc6-8db0-d3bfdad49358",
        "name": "经验女教师",
        "lang": "中文·普通话",
        "desc": "教育、社交媒体·高冷、自然",
    },
    {
        "voice_id": "fe85a513-9bf3-4ef7-aa0b-8b2d11e4db93",
        "name": "少年感人声（男）",
        "lang": "中文·普通话",
        "desc": "有声书、旁白·温柔、磁性、自然",
    },
    {
        "voice_id": "0804710c-8e5e-4b67-acda-5785ef13c309",
        "name": "历史解说男声",
        "lang": "中文·普通话",
        "desc": "有声书、影视解说·沉稳、磁性",
    },
    {
        "voice_id": "9d1e88e9-3b9c-4992-a414-7a1cb3ff7ab5",
        "name": "优雅英国女士",
        "lang": "英文·英式英语",
        "desc": "影视、人物·温柔、磁性、自然",
    },
    {
        "voice_id": "806c9695-6160-404e-8722-4f788d935af3",
        "name": "轻快灵动女声",
        "lang": "中文·普通话",
        "desc": "智能客服、热门玩法·轻快、活泼、自然",
    },
    {
        "voice_id": "2fdf194e-c16e-4587-9027-0d3464e09b4e",
        "name": "诗词朗读",
        "lang": "中文·普通话",
        "desc": "教育、旁白·沉稳、磁性、专业",
    },
    {
        "voice_id": "133bd03b-d717-4a55-8974-7ffc9afc1b51",
        "name": "故宫纪录片",
        "lang": "中文·普通话",
        "desc": "纪录片、旁白·沉稳、磁性、专业",
    },
    {
        "voice_id": "26838557-6890-4505-bc7c-e8198443a141",
        "name": "东北虎哥",
        "lang": "中文·普通话",
        "desc": "娱乐、社交媒体·搞笑、慵懒",
    },
    {
        "voice_id": "19411508-8731-4b68-901d-7e4b8a98e23f",
        "name": "忧伤的秋",
        "lang": "中文·普通话",
        "desc": "影视、人物·温柔、慵懒",
    },
    {
        "voice_id": "944eb93b-3820-49f3-b2c0-4e37a31d1161",
        "name": "三农农业旁白",
        "lang": "中文·普通话",
        "desc": "纪录片、旁白·轻快、磁性、专业",
    },
]


class MossError(RuntimeError):
    """Raised when a Moss request or response is invalid."""


def _load_api_key() -> str:
    """Load a key from ``MOSS_API_KEY`` or the optional ``MOSS_KEY_FILE``."""
    key = os.environ.get("MOSS_API_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("MOSS_KEY_FILE", "").strip()
    if not key_file:
        return ""
    try:
        text = Path(key_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return str(parsed.get("api_key") or "").strip()
    match = re.search(r'"api_key"\s*:\s*"([^"]+)"', text)
    return (match.group(1) if match else text).strip()


def _load_tts_config() -> Dict[str, Any]:
    """Read the live TTS config (monkeypatch-friendly for tests)."""
    try:
        from tools.tts_tool import _load_tts_config

        cfg = _load_tts_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:  # pragma: no cover - config discovery is best-effort
        logger.debug("Could not load tts config: %s", exc)
        return {}


def resolve_moss_api_key(tts_config: Dict[str, Any] | None = None) -> str:
    """Return the Moss API key via the shared provider-secret chain."""
    cfg = tts_config if tts_config is not None else _load_tts_config()
    section = cfg.get("moss") if isinstance(cfg, dict) else {}
    config_value = (
        str(section.get("api_key") or "").strip() if isinstance(section, dict) else ""
    )
    try:
        from tools.tool_backend_helpers import resolve_provider_secret

        return (
            resolve_provider_secret("MOSS_API_KEY", "moss", config_value=config_value)
            or ""
        ).strip()
    except Exception as exc:  # pragma: no cover - config discovery is best-effort
        logger.debug("Moss key resolution failed: %s", exc)
        return config_value


def _resolve_endpoint(cfg: Dict[str, Any]) -> tuple[str, float]:
    section = cfg.get("moss") if isinstance(cfg, dict) else {}
    section = section if isinstance(section, dict) else {}
    base_url = str(section.get("base_url") or DEFAULT_BASE_URL).strip()
    try:
        timeout = float(section.get("timeout") or 60)
    except TypeError, ValueError:
        timeout = 60.0
    return base_url.rstrip("/"), timeout


def build_http_kwargs(tts_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Resolve key, endpoint, timeout and auth headers for Moss HTTP calls."""
    cfg = tts_config if tts_config is not None else _load_tts_config()
    base_url, timeout = _resolve_endpoint(cfg)
    api_key = resolve_moss_api_key(cfg) or _load_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout,
        "headers": headers,
    }


def _normalize_model(model: str | None, default: str) -> str:
    value = str(model or "").strip()
    aliases = {
        "moss-tts": MODEL_TTS,
        "moss-ttsd": MODEL_TTSD,
        "moss-voice-generator": MODEL_VOICE_GENERATOR,
    }
    return aliases.get(value, value or default)


def _pause_text(text: str, pause: float | None, model: str) -> str:
    if pause is None:
        return text
    if model != MODEL_TTS:
        raise MossError("pause is only supported by moss-tts-1.5-flash")
    value = float(pause)
    if not 0.1 <= value <= 10.0:
        raise MossError("pause must be between 0.1 and 10.0 seconds")
    formatted = f"{value:.3f}".rstrip("0").rstrip(".")
    if "." not in formatted:
        formatted += ".0"
    return f"{text} [pause {formatted}s]"


class MossClient:
    """Small requests-based client for the documented Moss v1 audio API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = str(api_key or _load_api_key()).strip()
        if not self.api_key:
            raise ValueError("No Moss API key configured")
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - best-effort error parsing
            return (response.text or response.reason or "")[:500]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            return str(data.get("message") or error or data)
        return str(data)

    @classmethod
    def _raise_for(cls, response: requests.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        raise MossError(
            f"Moss API request failed (HTTP {response.status_code}): "
            f"{cls._error_detail(response)}"
        )

    @staticmethod
    def _parse_json(response: requests.Response) -> dict:
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - requests exposes varied decoders
            raise MossError("Moss API returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise MossError(f"Moss API returned an unexpected payload: {data!r}")
        return data

    def _post_json(
        self, path: str, payload: Dict[str, Any], *, expect_audio: bool
    ) -> bytes | dict:
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    key: value for key, value in payload.items() if value is not None
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MossError(f"Moss API request failed: {exc}") from exc
        self._raise_for(response)
        return response.content if expect_audio else self._parse_json(response)

    def speech(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        response_format: str = "mp3",
        delivery_method: str = "audio",
        async_mode: bool = False,
        webhook_url: str | None = None,
        pause: float | None = None,
    ) -> bytes | dict:
        model_id = _normalize_model(model, MODEL_TTS)
        input_text = str(text or "").strip()
        if not input_text:
            raise MossError("text is required")
        voice = str(voice_id or "").strip()
        if not voice:
            raise MossError("voice_id is required")
        payload = {
            "model": model_id,
            "input": _pause_text(input_text, pause, model_id),
            "voice_id": voice,
            "response_format": response_format,
            "delivery_method": delivery_method,
            "async": True if async_mode else None,
            "webhook_url": webhook_url if async_mode else None,
        }
        return self._post_json(
            "/audio/speech",
            payload,
            expect_audio=delivery_method == "audio" and not async_mode,
        )

    def speakers(
        self,
        speakers: list,
        segments: list,
        *,
        model: str | None = None,
        response_format: str = "mp3",
        delivery_method: str = "audio",
        async_mode: bool = False,
        webhook_url: str | None = None,
    ) -> bytes | dict:
        payload = {
            "model": _normalize_model(model, MODEL_TTSD),
            "speakers": speakers,
            "segments": segments,
            "response_format": response_format,
            "delivery_method": delivery_method,
            "async": True if async_mode else None,
            "webhook_url": webhook_url if async_mode else None,
        }
        return self._post_json(
            "/audio/speech/speakers",
            payload,
            expect_audio=delivery_method == "audio" and not async_mode,
        )

    def voice_generations(
        self,
        instruction: str,
        input_text: str,
        *,
        model: str | None = None,
        response_format: str = "mp3",
        delivery_method: str = "audio",
        async_mode: bool = False,
        webhook_url: str | None = None,
    ) -> bytes | dict:
        payload = {
            "model": _normalize_model(model, MODEL_VOICE_GENERATOR),
            "input": input_text,
            "instruction": instruction,
            "response_format": response_format,
            "delivery_method": delivery_method,
            "async": True if async_mode else None,
            "webhook_url": webhook_url if async_mode else None,
        }
        return self._post_json(
            "/audio/voice/generations",
            payload,
            expect_audio=delivery_method == "audio" and not async_mode,
        )

    def create_voice(
        self,
        audio_sample_path: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        path = Path(audio_sample_path).expanduser()
        if not path.is_file():
            raise MossError(f"Audio sample not found: {path}")
        data = {
            key: value
            for key, value in {"name": name, "description": description}.items()
            if value
        }
        try:
            with path.open("rb") as handle:
                response = requests.post(
                    f"{self.base_url}/audio/voices",
                    headers=self.headers,
                    data=data,
                    files={"audio_sample": (path.name, handle)},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise MossError(f"Moss API request failed: {exc}") from exc
        self._raise_for(response)
        return self._parse_json(response)

    def list_voices(self) -> list[dict]:
        try:
            response = requests.get(
                f"{self.base_url}/audio/voices",
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MossError(f"Moss API request failed: {exc}") from exc
        self._raise_for(response)
        data = self._parse_json(response).get("data")
        if not isinstance(data, list):
            raise MossError("Moss voice-list response is missing data[]")
        return [item for item in data if isinstance(item, dict)]

    def poll_task(self, task_id: str, timeout: float = 180.0) -> dict:
        task = str(task_id or "").strip()
        if not task:
            raise MossError("task_id is required")
        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            try:
                response = requests.get(
                    f"{self.base_url}/audio/tasks/{task}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise MossError(f"Moss API request failed: {exc}") from exc
            self._raise_for(response)
            data = self._parse_json(response)
            status = str(data.get("status") or "").upper()
            if status in {"SUCCESS", "FAILED", "CANCELLED", "ERROR"}:
                return data
            if time.monotonic() >= deadline:
                raise MossError(f"Moss task {task} did not finish within {timeout}s")
            delay = float(data.get("retry_after") or 2.0)
            time.sleep(min(max(delay, 0.1), max(deadline - time.monotonic(), 0.1)))

    def speech_stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        response_format: str = "pcm",
        stream_format: str = "sse",
        pause: float | None = None,
    ) -> Iterator[dict]:
        model_id = _normalize_model(model, MODEL_TTS)
        if model_id != MODEL_TTS:
            raise MossError("streaming requires model moss-tts-1.5-flash")
        input_text = str(text or "").strip()
        if not input_text:
            raise MossError("text is required")
        payload = {
            "model": model_id,
            "input": _pause_text(input_text, pause, model_id),
            "voice_id": str(voice_id or "").strip() or None,
            "stream": True,
            "response_format": response_format,
            "stream_format": stream_format,
        }
        try:
            response = requests.post(
                f"{self.base_url}/audio/speech",
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    key: value for key, value in payload.items() if value is not None
                },
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise MossError(f"Moss API request failed: {exc}") from exc
        self._raise_for(response)
        try:
            for raw in response.iter_lines(decode_unicode=True):
                line = str(raw or "").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise MossError(f"Invalid Moss SSE event: {body[:200]!r}") from exc
                if isinstance(event, dict):
                    yield event
        finally:
            response.close()

    @staticmethod
    def save_audio(data: bytes | bytearray, output_path: str) -> str:
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        return str(path)

    def download(self, url: str, output_path: str) -> str:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MossError("Moss result URL must be an http(s) URL")
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost":
            raise MossError("Moss result URL must be public")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise MossError("Moss result URL must be public")
        try:
            response = requests.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise MossError(f"Moss audio download failed: {exc}") from exc
        self._raise_for(response)
        return self.save_audio(response.content, output_path)


def build_client(tts_config: Dict[str, Any] | None = None) -> MossClient:
    """Build the repository-owned Moss client from live configuration."""
    cfg = tts_config if tts_config is not None else _load_tts_config()
    base_url, timeout = _resolve_endpoint(cfg)
    api_key = resolve_moss_api_key(cfg) or _load_api_key()
    return MossClient(api_key=api_key, base_url=base_url, timeout=timeout)


def is_mp3(path: str | Path) -> bool:
    data = Path(path).read_bytes()[:3]
    return data == b"ID3" or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}


def is_wav(path: str | Path) -> bool:
    data = Path(path).read_bytes()[:12]
    return len(data) == 12 and data[:4] == b"RIFF" and data[8:] == b"WAVE"
