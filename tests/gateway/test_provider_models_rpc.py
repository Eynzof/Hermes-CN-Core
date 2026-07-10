"""Tests for the provider.models gateway RPC and the shared model-id fetch
helper it shares with provider.probe (P-036).

provider.models lets the desktop refresh a provider's model list through the
backend instead of the desktop's SSRF-guarded external_request proxy, so a
self-hosted provider on a private LAN IP (e.g. http://192.168.x.x:11434/v1) is
reachable. It mirrors provider.probe's URL-candidate logic but returns the full
list and tolerates an empty api_key.
"""

import httpx
import pytest

from tui_gateway.server import (
    _build_anthropic_probe_url_candidates,
    _fetch_provider_model_ids,
    handle_request,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _models_payload(*ids: str) -> dict:
    return {"object": "list", "data": [{"id": i} for i in ids]}


def _call_models(monkeypatch, getter, **params) -> dict:
    """Invoke the provider.models RPC with httpx.get patched to *getter*."""
    monkeypatch.setattr(httpx, "get", getter)
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "provider.models",
        "params": params,
    }
    resp = handle_request(req)
    assert resp is not None
    return resp


def test_provider_models_returns_full_list(monkeypatch):
    ids = [f"qwen2.5-coder:{n}b" for n in range(8)]
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: _FakeResponse(200, _models_payload(*ids))
    )
    result = _fetch_provider_model_ids("http://192.168.31.11:11434/v1", "key", 8.0)

    assert result["ok"] is True
    # Full list, not truncated the way provider.probe samples to 5.
    assert result["model_ids"] == ids
    assert result["status_code"] == 200
    assert result["error_kind"] is None


def test_provider_models_rpc_envelope(monkeypatch):
    resp = _call_models(
        monkeypatch,
        lambda url, **kw: _FakeResponse(200, _models_payload("a", "b")),
        provider="custom:ollama",
        base_url="http://192.168.31.11:11434/v1",
        api_key="placeholder",
    )
    result = resp["result"]
    assert result["ok"] is True
    assert result["models"] == ["a", "b"]
    assert result["model_count"] == 2


def test_provider_models_tolerates_empty_api_key(monkeypatch):
    """Local servers (Ollama) need no key — no Authorization header is sent."""
    seen = {}

    def fake_get(url, **kw):
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("m1"))

    resp = _call_models(
        monkeypatch,
        fake_get,
        provider="custom:ollama",
        base_url="http://192.168.31.11:11434/v1",
        api_key="",
    )
    assert resp["result"]["ok"] is True
    assert resp["result"]["models"] == ["m1"]
    assert seen["headers"] == {}  # no bearer token attached


def test_provider_models_sends_bearer_when_key_present(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("m1"))

    _call_models(
        monkeypatch,
        fake_get,
        provider="custom:ollama",
        base_url="http://192.168.31.11:11434/v1",
        api_key="secret",
    )
    assert seen["headers"] == {"Authorization": "Bearer secret"}


def test_provider_models_requires_provider(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(200))
    resp = handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "provider.models", "params": {}}
    )
    assert resp["error"]["code"] == 5043


def test_provider_models_requires_base_url_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(200))
    resp = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "provider.models",
            "params": {"provider": "custom:nope"},
        }
    )
    assert resp["error"]["code"] == 5044


def test_auth_failure_is_terminal(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        return _FakeResponse(401)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = _fetch_provider_model_ids("http://example.com/v1", "bad", 5.0)

    assert result["ok"] is False
    assert result["error_kind"] == "auth"
    assert result["status_code"] == 401
    # 401 short-circuits — does not try the remaining candidate URLs.
    assert calls["n"] == 1


def test_all_404_reports_http_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResponse(404))
    result = _fetch_provider_model_ids("http://example.com", "key", 5.0)
    assert result["ok"] is False
    assert result["error_kind"] == "http"
    assert "404" in (result["error"] or "")


def test_timeout_is_reported(monkeypatch):
    def fake_get(url, **kw):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "get", fake_get)
    result = _fetch_provider_model_ids("http://example.com/v1", "key", 3.0)
    assert result["ok"] is False
    assert result["error_kind"] == "timeout"
    assert result["latency_ms"] == 3000


def test_probe_still_samples_five_and_counts_full(monkeypatch):
    """Regression: provider.probe keeps its truncated sample + full count."""
    ids = [f"model-{n}" for n in range(9)]
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: _FakeResponse(200, _models_payload(*ids))
    )
    resp = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "provider.probe",
            "params": {
                "provider": "custom:ollama",
                "base_url": "http://192.168.31.11:11434/v1",
                "api_key": "key",
            },
        }
    )
    result = resp["result"]
    assert result["ok"] is True
    assert result["model_count"] == 9
    assert result["sample_models"] == ids[:5]


# ---------------------------------------------------------------------------
# api_mode="anthropic_messages" — P-046. Claude Code relays speak the
# Anthropic protocol: bare-host base URLs (the SDK appends /v1/messages) and
# x-api-key auth. The OpenAI-style probe misreported valid keys there.
# ---------------------------------------------------------------------------


def test_anthropic_candidates_bare_host():
    assert _build_anthropic_probe_url_candidates("https://www.packyapi.com") == [
        "https://www.packyapi.com/v1/models",
    ]


def test_anthropic_candidates_nested_path_falls_back_to_host_root():
    assert _build_anthropic_probe_url_candidates(
        "https://api.aicodemirror.com/api/claudecode"
    ) == [
        "https://api.aicodemirror.com/api/claudecode/v1/models",
        "https://api.aicodemirror.com/v1/models",
    ]


def test_anthropic_candidates_v1_suffix_not_doubled():
    assert _build_anthropic_probe_url_candidates("https://relay.example/v1") == [
        "https://relay.example/v1/models",
    ]


def test_anthropic_mode_sends_native_auth_headers(monkeypatch):
    """anthropic_messages probes with x-api-key + anthropic-version, no Bearer."""
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("claude-sonnet-5"))

    monkeypatch.setattr(httpx, "get", fake_get)
    result = _fetch_provider_model_ids(
        "https://www.packyapi.com", "sk-relay", 5.0, api_mode="anthropic_messages"
    )

    assert result["ok"] is True
    assert seen["url"] == "https://www.packyapi.com/v1/models"
    assert seen["headers"] == {
        "x-api-key": "sk-relay",
        "anthropic-version": "2023-06-01",
    }


def test_anthropic_mode_empty_key_sends_no_headers(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("m"))

    monkeypatch.setattr(httpx, "get", fake_get)
    result = _fetch_provider_model_ids(
        "https://relay.example", "", 5.0, api_mode="anthropic_messages"
    )
    assert result["ok"] is True
    assert seen["headers"] == {}


def test_probe_rpc_forwards_api_mode(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("claude-opus-4-8"))

    monkeypatch.setattr(httpx, "get", fake_get)
    resp = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "provider.probe",
            "params": {
                "provider": "packycode",
                "base_url": "https://www.packyapi.com",
                "api_key": "sk-relay",
                "api_mode": "anthropic_messages",
            },
        }
    )
    assert resp["result"]["ok"] is True
    assert seen["headers"]["x-api-key"] == "sk-relay"
    assert "Authorization" not in seen["headers"]


def test_models_rpc_forwards_api_mode(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("claude-opus-4-8", "claude-sonnet-5"))

    resp = _call_models(
        monkeypatch,
        fake_get,
        provider="packycode",
        base_url="https://www.packyapi.com",
        api_key="sk-relay",
        api_mode="anthropic_messages",
    )
    assert resp["result"]["models"] == ["claude-opus-4-8", "claude-sonnet-5"]
    assert seen["headers"]["anthropic-version"] == "2023-06-01"


def test_unknown_api_mode_falls_back_to_openai_style(monkeypatch):
    """A stale/unknown api_mode must not change existing OpenAI-style behavior."""
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        return _FakeResponse(200, _models_payload("m1"))

    _call_models(
        monkeypatch,
        fake_get,
        provider="custom:ollama",
        base_url="http://192.168.31.11:11434/v1",
        api_key="secret",
        api_mode="codex_responses",
    )
    assert seen["headers"] == {"Authorization": "Bearer secret"}
    assert seen["url"] == "http://192.168.31.11:11434/v1/models"
