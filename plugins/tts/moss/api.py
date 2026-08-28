"""Moss direct-HTTP layer — transcription, file upload, MOSS-VL.

The external ``moss_tts`` SDK only ships TTS methods (``speech``,
``speakers``, ``voice_generations``, ``create_voice``, ``list_voices``,
``poll_task``, …).  Transcription (``/v1/audio/transcriptions``), file
upload (``/v1/files``) and MOSS-VL (``/v1/responses``) are implemented
here directly over ``requests`` so the plugin is self-contained and does
not depend on the SDK's release cadence.

Key / base_url / timeout resolution is delegated to
:func:`plugins.tts.moss.client.build_http_kwargs` — the single owner for
all three Moss capabilities (TTS, STT, vision).  Every function raises
:class:`MossApiError` on failure; callers convert to the standard
``{success, ...}`` envelopes.

Request-shape constraints honored (from the Moss API docs):

* ``/v1/audio/transcriptions`` — multipart ``file`` (no inline base64),
  ``model`` required, ``diarize`` (diarize-pro only), ``response_format``
  in ``json``/``text``/``diarized_json``, ``keyterms`` (≤20, ≤30 chars
  each, diarize-pro only), ``async=true`` for task mode.
* ``/v1/files`` — multipart ``file`` + ``purpose``; returns ``id``.
* ``/v1/responses`` — JSON with ``input[].content[]``; 1–5 images OR 1
  video, never mixed; per-item URL or ``file_id`` (never both).
* URLs must be public — localhost/loopback/private addresses are banned
  client-side (shared security rule).
"""
from __future__ import annotations

import ipaddress
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

import requests

from plugins.tts.moss.client import build_http_kwargs

logger = logging.getLogger(__name__)

# Doc caps (server-side too; client checks give better errors and avoid
# wasted uploads).
MAX_AUDIO_BYTES = 512 * 1024 * 1024  # 512 MB — transcriptions + files
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 30 MB per image (MOSS-VL)
MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB per video (MOSS-VL)
MAX_IMAGES = 5
MAX_KEYTERMS = 20
MAX_KEYTERM_LENGTH = 30
MAX_OUTPUT_TOKENS = 8192

# Display helpers for error messages.
_MB = 1024 * 1024


class MossApiError(Exception):
    """Raised when a direct Moss HTTP call fails.

    Message is human-readable (includes the platform error body when one
    is present) so tool handlers can surface it directly.
    """


def _mb(value: int) -> str:
    return f"{value / _MB:.0f}MB"


def _is_private_host(host: str) -> bool:
    """True when *host* is loopback / link-local / private / reserved."""
    if not host:
        return True
    host = host.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if host.startswith("[") and host.endswith("]"):  # bracketed IPv6
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — leave domain-name resolution to the server.
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> None:
    """Reject non-http(s) and localhost/loopback/private URLs.

    Raises :class:`MossApiError` when the URL is not a safe public one —
    the shared security rule that also applies server-side.  We check the
    syntactic shape here so user-supplied URLs are rejected before any
    upload/call is attempted.
    """
    if not isinstance(url, str) or not url.strip():
        raise MossApiError("URL is required")
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise MossApiError(f"Invalid URL: {url!r}") from exc
    if parsed.scheme not in ("http", "https"):
        raise MossApiError(
            f"Unsupported URL scheme in {url!r}; only http(s) URLs are allowed"
        )
    if _is_private_host(parsed.hostname or ""):
        raise MossApiError(
            f"URL must be public (not localhost/loopback/private): {url!r}"
        )


def _http_kwargs() -> Dict[str, Any]:
    """Resolve key/base_url/timeout/headers once per call."""
    return build_http_kwargs()


def _require_key(kwargs: Dict[str, Any]) -> None:
    if not kwargs.get("api_key"):
        raise MossApiError(
            "Moss API key is not configured — set MOSS_API_KEY, "
            "`tts.moss.api_key` in config.yaml, or run `hermes auth add moss`."
        )


def _raise_for(resp: requests.Response) -> None:
    """Raise :class:`MossApiError` with the platform error body on non-2xx."""
    if 200 <= resp.status_code < 300:
        return
    detail = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or err)
            else:
                detail = str(data.get("message") or data.get("error") or data)
        else:
            detail = str(data)
    except Exception:  # noqa: BLE001 — best-effort body parsing
        detail = (resp.text or "")[:500]
    if not detail:
        detail = resp.reason or ""
    path = _short_path(getattr(getattr(resp, "request", None), "url", ""))
    raise MossApiError(
        f"Moss API {path or 'request'} failed (HTTP {resp.status_code}): {detail}"
    )


def _short_path(url: str) -> str:
    """Return a compact ``method path`` label for error messages."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        path = parsed.path or ""
    except ValueError:
        return url
    return path if len(path) <= 60 else path[-60:]


def _parse_json(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — non-JSON body
        raise MossApiError(
            f"Moss API returned non-JSON: {(resp.text or '')[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise MossApiError(f"Moss API returned an unexpected payload: {data!r}")
    return data


def _validate_local_file(path: Path, *, max_bytes: int, what: str) -> None:
    if not path.is_file():
        raise MossApiError(f"{what} file not found: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise MossApiError(
            f"{what} file too large: {_mb(size)} (max {_mb(max_bytes)})"
        )


# ---------------------------------------------------------------------------
# /v1/files
# ---------------------------------------------------------------------------


def upload_file(local_path: str, purpose: str = "audio") -> str:
    """Upload a local file to ``/v1/files``; returns the ``file_id``.

    Streams the file (never slurps it into memory — the API accepts up to
    512 MB).  ``purpose`` is one of ``audio``/``image``/``video``.
    """
    path = Path(local_path)
    _validate_local_file(path, max_bytes=MAX_AUDIO_BYTES, what="Upload")
    kwargs = _http_kwargs()
    _require_key(kwargs)
    url = f"{kwargs['base_url']}/files"
    with path.open("rb") as fh:
        resp = requests.post(
            url,
            headers=kwargs["headers"],
            data={"purpose": purpose},
            files={"file": (path.name, fh)},
            timeout=kwargs["timeout"],
        )
    _raise_for(resp)
    data = _parse_json(resp)
    file_id = str(data.get("id") or data.get("file_id") or "").strip()
    if not file_id:
        raise MossApiError(f"Moss upload_file response missing id: {data!r}")
    return file_id


# ---------------------------------------------------------------------------
# /v1/audio/transcriptions
# ---------------------------------------------------------------------------


def _looks_like_url(value: str) -> bool:
    """True when *value* is a URL (scheme-prefixed), not a local path."""
    return urlparse(value).scheme in ("http", "https")


def _transcribe_json(
    payload: Dict[str, Any],
    *,
    diarize: bool,
    response_format: str,
    keyterms: Optional[Sequence[str]],
    async_mode: bool,
) -> dict:
    """POST /v1/audio/transcriptions as JSON (``file_id`` / ``url`` input)."""
    body: Dict[str, Any] = dict(payload)
    if response_format:
        body["response_format"] = response_format
    if diarize:
        body["diarize"] = True
    if async_mode:
        body["async"] = True
    kt = [str(k).strip() for k in (keyterms or ()) if str(k).strip()]
    if kt:
        if len(kt) > MAX_KEYTERMS:
            raise MossApiError(f"Too many keyterms: {len(kt)} (max {MAX_KEYTERMS})")
        for k in kt:
            if len(k) > MAX_KEYTERM_LENGTH:
                raise MossApiError(
                    f"keyterm too long: {len(k)} chars (max {MAX_KEYTERM_LENGTH})"
                )
        body["keyterms"] = kt

    kwargs = _http_kwargs()
    _require_key(kwargs)
    url = f"{kwargs['base_url']}/audio/transcriptions"
    resp = requests.post(
        url,
        headers={**kwargs["headers"], "Content-Type": "application/json"},
        json=body,
        timeout=kwargs["timeout"],
    )
    _raise_for(resp)
    return _parse_json(resp)


def transcribe_audio(
    audio_path: str,
    *,
    model: str,
    diarize: bool = False,
    response_format: str = "json",
    keyterms: Optional[Sequence[str]] = None,
    async_mode: bool = False,
) -> dict:
    """Transcribe a local file, ``file_id:...`` handle, or public URL.

    Local paths → multipart ``file`` upload; ``file_id:<id>`` → JSON
    ``file_id``; http(s) URLs → JSON ``url`` (validated public).  This is
    the tool-level entry (the ABC provider only ever receives local file
    paths from the dispatcher).
    """
    stripped = str(audio_path or "").strip()
    if not stripped:
        raise MossApiError("audio_path is required")
    if stripped.startswith("file_id:"):
        file_id = stripped[len("file_id:"):].strip()
        if not file_id:
            raise MossApiError("file_id is empty")
        return _transcribe_json(
            {"model": model, "file_id": file_id},
            diarize=diarize, response_format=response_format,
            keyterms=keyterms, async_mode=async_mode,
        )
    if _looks_like_url(stripped):
        validate_public_url(stripped)
        return _transcribe_json(
            {"model": model, "url": stripped},
            diarize=diarize, response_format=response_format,
            keyterms=keyterms, async_mode=async_mode,
        )
    return transcribe(
        stripped,
        model=model, diarize=diarize, response_format=response_format,
        keyterms=keyterms, async_mode=async_mode,
    )


def _keyterms_form(keyterms: Optional[Sequence[str]]) -> List[tuple[str, str]]:
    """Normalize + validate keyterms into multipart form pairs.

    Moss accepts at most 20 keyterms of at most 30 chars each; non-empty
    keyterms are only valid on the diarize-pro model (callers drop them
    otherwise).  Sent as repeated form fields (the OpenAI-compatible
    multipart convention).
    """
    if not keyterms:
        return []
    pairs: List[tuple[str, str]] = []
    seen: set[str] = set()
    for i, kt in enumerate(keyterms):
        text = str(kt or "").strip()
        if not text:
            continue
        if len(text) > MAX_KEYTERM_LENGTH:
            raise MossApiError(
                f"keyterms[{i}] is {len(text)} chars, exceeding the "
                f"Moss cap of {MAX_KEYTERM_LENGTH} chars per keyterm"
            )
        if text not in seen:
            seen.add(text)
            pairs.append(("keyterms", text))
    if len(pairs) > MAX_KEYTERMS:
        raise MossApiError(
            f"Too many keyterms: {len(pairs)} (max {MAX_KEYTERMS})"
        )
    return pairs


def transcribe(
    file_path: str,
    *,
    model: str,
    diarize: bool = False,
    response_format: str = "json",
    keyterms: Optional[Sequence[str]] = None,
    async_mode: bool = False,
) -> dict:
    """POST /v1/audio/transcriptions (multipart file); returns the JSON dict.

    ``diarize=True`` with ``model="moss-transcribe-diarize-pro"`` returns
    ``segments[]`` (``start``/``end``/``text``/``speaker``).  ``async_mode``
    returns the ``{id, task_id, status, ...}`` task envelope.
    """
    path = Path(file_path)
    _validate_local_file(path, max_bytes=MAX_AUDIO_BYTES, what="Audio")
    kwargs = _http_kwargs()
    _require_key(kwargs)

    form: List[tuple[str, str]] = [("model", str(model).strip())]
    if response_format:
        form.append(("response_format", str(response_format).strip()))
    if diarize:
        form.append(("diarize", "true"))
    if async_mode:
        form.append(("async", "true"))
    form.extend(_keyterms_form(keyterms))

    url = f"{kwargs['base_url']}/audio/transcriptions"
    with path.open("rb") as fh:
        resp = requests.post(
            url,
            headers=kwargs["headers"],
            data=form,
            files={"file": (path.name, fh)},
            timeout=kwargs["timeout"],
        )
    _raise_for(resp)
    if response_format == "text":
        # ``response_format=text`` returns a plain-text body (verified
        # against the live API), not JSON — normalize it into the same
        # ``{text: ...}`` envelope the JSON formats return.
        body = (resp.text or "").strip()
        return {"text": body}
    return _parse_json(resp)


# ---------------------------------------------------------------------------
# /v1/responses (MOSS-VL)
# ---------------------------------------------------------------------------


def _media_content(
    image_urls: Sequence[str],
    image_file_ids: Sequence[str],
    video_url: Optional[str],
    video_file_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Build the ``content[]`` items for a MOSS-VL ``input`` block.

    Enforces the doc contract: 1–5 images OR 1 video (never mixed); per
    item either a URL or a ``file_id`` (never both).
    """
    items: List[Dict[str, Any]] = []
    n_images = len(image_urls) + len(image_file_ids)
    n_videos = int(bool(video_url)) + int(bool(video_file_id))

    if n_images == 0 and n_videos == 0:
        raise MossApiError("Moss vision requires at least one image or one video")
    if n_images > 0 and n_videos > 0:
        raise MossApiError(
            "Moss vision does not support mixing images and video in one request"
        )
    if n_images > MAX_IMAGES:
        raise MossApiError(
            f"Moss vision supports at most {MAX_IMAGES} images per request "
            f"(got {n_images})"
        )
    if n_videos > 1:
        raise MossApiError("Moss vision supports at most 1 video per request")

    for url in image_urls:
        validate_public_url(url)
        items.append({"type": "input_image", "image_url": url.strip()})
    for fid in image_file_ids:
        items.append({"type": "input_image", "file_id": fid})
    if video_url:
        validate_public_url(video_url)
        items.append({"type": "input_video", "video_url": video_url.strip()})
    if video_file_id:
        items.append({"type": "input_video", "file_id": video_file_id})
    return items


def understand(
    input_text: str,
    *,
    image_urls: Sequence[str] = (),
    image_file_ids: Sequence[str] = (),
    video_url: Optional[str] = None,
    video_file_id: Optional[str] = None,
    model: str = "moss-vl-1.0",
    max_output_tokens: Optional[int] = None,
) -> dict:
    """POST /v1/responses with a text instruction + media; returns the dict.

    The caller extracts ``output[].content[].text`` and inspects
    ``status`` / ``incomplete_details`` (the tool layer adds the
    ``max_output_tokens`` truncation warning).
    """
    if not isinstance(input_text, str) or not input_text.strip():
        raise MossApiError("instruction is required for Moss vision")
    content = _media_content(image_urls, image_file_ids, video_url, video_file_id)
    payload: Dict[str, Any] = {
        "model": str(model).strip() or "moss-vl-1.0",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": input_text.strip()}, *content],
            }
        ],
    }
    if max_output_tokens is not None:
        try:
            tokens = int(max_output_tokens)
        except (TypeError, ValueError) as exc:
            raise MossApiError(f"max_output_tokens must be an integer: {max_output_tokens!r}") from exc
        if tokens < 1 or tokens > MAX_OUTPUT_TOKENS:
            raise MossApiError(
                f"max_output_tokens must be in 1..{MAX_OUTPUT_TOKENS} "
                f"(got {tokens})"
            )
        payload["max_output_tokens"] = tokens

    kwargs = _http_kwargs()
    _require_key(kwargs)
    url = f"{kwargs['base_url']}/responses"
    resp = requests.post(
        url,
        headers={**kwargs["headers"], "Content-Type": "application/json"},
        json=payload,
        timeout=kwargs["timeout"],
    )
    _raise_for(resp)
    return _parse_json(resp)


def extract_vision_text(data: dict) -> str:
    """Return the concatenated text from a MOSS-VL response.

    MOSS-VL returns ``output[].content[].text``; this helper walks that
    shape defensively so tool/providers share one parser.
    """
    parts: List[str] = []
    for out in data.get("output") or []:
        if not isinstance(out, dict):
            continue
        for item in out.get("content") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)
