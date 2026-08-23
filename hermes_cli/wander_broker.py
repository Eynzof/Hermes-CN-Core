"""Desktop-owned credential bridge for the managed Wander provider.

The desktop process owns the Wanderminds refresh token in macOS Keychain.  A
managed Core receives only a process-local loopback URL and capability, then
asks that broker for a short-lived Logto access token when it needs to call the
Wander inference edge.  Nothing in this module writes credentials to disk.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

import orjson


DEFAULT_WANDER_INFERENCE_BASE_URL = "https://inference-staging.wanderminds.ai/v1"
WANDER_INFERENCE_HOST = "inference-staging.wanderminds.ai"
WANDER_BROKER_URL_ENV = "WANDER_TOKEN_BROKER_URL"
WANDER_BROKER_SECRET_ENV = "WANDER_TOKEN_BROKER_SECRET"
WANDER_INFERENCE_BASE_URL_ENV = "WANDER_INFERENCE_BASE_URL"
_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(slots=True)
class WanderBrokerError(RuntimeError):
    code: str
    message: str
    retryable: bool = False
    billing_url: str | None = None

    def __str__(self) -> str:
        return self.message


def wander_broker_configured() -> bool:
    """Return whether this Core was launched by the desktop token broker."""
    return bool(
        os.environ.get(WANDER_BROKER_URL_ENV, "").strip()
        and os.environ.get(WANDER_BROKER_SECRET_ENV, "").strip()
    )


def _validated_broker_url() -> str:
    raw = os.environ.get(WANDER_BROKER_URL_ENV, "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise WanderBrokerError(
            "desktop_required", "Wander 桌面令牌代理地址无效"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.path != "/v1/token"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise WanderBrokerError(
            "desktop_required",
            "Wander 仅支持桌面端本机 managed Core",
        )
    return f"http://127.0.0.1:{port}/v1/token"


def _validated_inference_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise WanderBrokerError(
            "relay_unavailable", "Wander 推理地址无效", True
        ) from exc
    allowed_path = parsed.path in {"", "/", "/v1"}
    secure = (
        parsed.scheme == "https"
        and parsed.hostname == WANDER_INFERENCE_HOST
        and parsed.port in {None, 443}
    )
    local_debug = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and port is not None
    )
    if (
        not (secure or local_debug)
        or not allowed_path
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise WanderBrokerError("relay_unavailable", "Wander 推理地址无效", True)
    return value if value.endswith("/v1") else f"{value}/v1"


def _decode_error(body: bytes, *, fallback: str) -> WanderBrokerError:
    try:
        payload = orjson.loads(body)
    except orjson.JSONDecodeError, UnicodeDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return WanderBrokerError(
        code=str(payload.get("code") or "reauth_required"),
        message=str(payload.get("message") or fallback),
        retryable=bool(payload.get("retryable")),
        billing_url=(
            str(payload["billing_url"])
            if isinstance(payload.get("billing_url"), str)
            else None
        ),
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_broker_request(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def resolve_wander_runtime_credentials(*, timeout_seconds: float = 3.0) -> dict:
    """Resolve a short-lived bearer token without persisting it in Core."""
    url = _validated_broker_url()
    capability = os.environ.get(WANDER_BROKER_SECRET_ENV, "").strip()
    if len(capability) < 32:
        raise WanderBrokerError("desktop_required", "Wander 桌面令牌代理未就绪")

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {capability}",
            "Accept": "application/json",
        },
    )
    try:
        with _open_broker_request(request, max(0.5, timeout_seconds)) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_RESPONSE_BYTES + 1)
        raise _decode_error(body, fallback="无法取得 Wanderminds ID 短期令牌") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise WanderBrokerError(
            "relay_unavailable",
            "Wander 桌面令牌代理暂不可用",
            True,
        ) from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise WanderBrokerError("relay_unavailable", "Wander 令牌响应过大", True)

    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WanderBrokerError(
            "relay_unavailable", "Wander 令牌响应无效", True
        ) from exc
    if not isinstance(payload, dict):
        raise WanderBrokerError("relay_unavailable", "Wander 令牌响应无效", True)

    access_token = str(payload.get("access_token") or "").strip()
    token_type = str(payload.get("token_type") or "").strip().lower()
    try:
        expires_at = int(payload.get("expires_at") or 0)
    except TypeError, ValueError:
        expires_at = 0
    if (
        not access_token
        or token_type != "bearer"
        or expires_at <= int(time.time()) + 15
    ):
        raise WanderBrokerError("reauth_required", "Wanderminds ID 登录需要刷新")

    inference_base = str(
        payload.get("inference_base_url")
        or os.environ.get(WANDER_INFERENCE_BASE_URL_ENV)
        or DEFAULT_WANDER_INFERENCE_BASE_URL
    )
    return {
        "provider": "wander",
        "api_key": access_token,
        "base_url": _validated_inference_base_url(inference_base),
        "expires_at": expires_at,
        "source": "desktop-token-broker",
    }
