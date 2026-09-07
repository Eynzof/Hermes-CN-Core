from __future__ import annotations

import time
import urllib.request

import orjson
import pytest

from agent.billing_links import build_billing_block
from agent.error_classifier import _BILLING_ERROR_CODES
from hermes_cli import models, runtime_provider
from hermes_cli.wander_broker import (
    WanderBrokerError,
    resolve_wander_runtime_credentials,
)


class _Response:
    def __init__(self, payload: dict):
        self.body = orjson.dumps(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        return self.body[:size]


def _configure_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WANDER_TOKEN_BROKER_URL", "http://127.0.0.1:43123/v1/token")
    monkeypatch.setenv("WANDER_TOKEN_BROKER_SECRET", "s" * 43)


def test_broker_returns_short_lived_token_without_persisting(monkeypatch):
    _configure_broker(monkeypatch)

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        assert request.full_url == "http://127.0.0.1:43123/v1/token"
        assert request.get_header("Authorization") == f"Bearer {'s' * 43}"
        assert timeout == 3.0
        return _Response({
            "access_token": "short-lived-logto-token",
            "expires_at": int(time.time()) + 3600,
            "token_type": "Bearer",
            "inference_base_url": "https://inference-staging.wanderminds.ai",
        })

    monkeypatch.setattr("hermes_cli.wander_broker._open_broker_request", fake_urlopen)
    creds = resolve_wander_runtime_credentials()

    assert creds == {
        "provider": "wander",
        "api_key": "short-lived-logto-token",
        "base_url": "https://inference-staging.wanderminds.ai/v1",
        "expires_at": pytest.approx(int(time.time()) + 3600, abs=1),
        "source": "desktop-token-broker",
    }


def test_broker_rejects_non_loopback_endpoint_before_network(monkeypatch):
    monkeypatch.setenv("WANDER_TOKEN_BROKER_URL", "https://attacker.example/v1/token")
    monkeypatch.setenv("WANDER_TOKEN_BROKER_SECRET", "s" * 43)
    monkeypatch.setattr(
        "hermes_cli.wander_broker._open_broker_request",
        lambda *_args, **_kwargs: pytest.fail("network must not be reached"),
    )

    with pytest.raises(WanderBrokerError, match="本机 managed Core"):
        resolve_wander_runtime_credentials()


def test_broker_rejects_unapproved_inference_host(monkeypatch):
    _configure_broker(monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.wander_broker._open_broker_request",
        lambda *_args, **_kwargs: _Response({
            "access_token": "short-lived-logto-token",
            "expires_at": int(time.time()) + 3600,
            "token_type": "Bearer",
            "inference_base_url": "https://attacker.example/v1",
        }),
    )

    with pytest.raises(WanderBrokerError, match="推理地址无效"):
        resolve_wander_runtime_credentials()


def test_runtime_wander_ignores_persisted_key_and_endpoint(monkeypatch):
    _configure_broker(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.wander_broker.resolve_wander_runtime_credentials",
        lambda: {
            "api_key": "broker-token",
            "base_url": "https://inference-staging.wanderminds.ai/v1",
            "source": "desktop-token-broker",
            "expires_at": int(time.time()) + 3600,
        },
    )

    resolved = runtime_provider.resolve_runtime_provider(
        requested="wander",
        explicit_api_key="must-not-win",
        explicit_base_url="https://attacker.example/v1",
    )

    assert resolved["provider"] == "wander"
    assert resolved["api_key"] == "broker-token"
    assert resolved["base_url"] == "https://inference-staging.wanderminds.ai/v1"
    assert resolved["api_mode"] == "chat_completions"


def test_wander_model_discovery_uses_broker_credentials(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.wander_broker.resolve_wander_runtime_credentials",
        lambda: {"api_key": "broker-token", "base_url": "https://edge.example/v1"},
    )
    monkeypatch.setattr(
        models,
        "fetch_api_models",
        lambda api_key, base_url: (
            ["vendor/model-a"]
            if (api_key, base_url) == ("broker-token", "https://edge.example/v1")
            else []
        ),
    )

    assert models.provider_model_ids("wander", force_refresh=True) == ["vendor/model-a"]


def test_wander_credit_wall_has_portal_recovery_link():
    block = build_billing_block(
        provider="wander",
        base_url="https://inference-staging.wanderminds.ai/v1",
        model="wander-beta",
    )

    assert "insufficient_credit" in _BILLING_ERROR_CODES
    assert block.provider_label == "Wander Portal"
    assert block.billing_url == "https://portal-staging.wanderminds.ai/portal"
    assert block.is_nous is False
