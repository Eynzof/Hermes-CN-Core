"""Behaviour contract for the endpoint-reachability gate in model_metadata.

The gate short-circuits the slow local-server HTTP metadata probes when the
model endpoint is unreachable, so ``AIAgent`` construction never blocks tens of
seconds on connect timeouts (reports/perf/root-cause-analysis.md #1-4). These
tests pin the contract:

  * unreachable / empty endpoints resolve to "not reachable" quickly,
  * a mocked HTTP transport is treated as reachable (so the existing
    mocked-transport probe tests keep exercising their logic unchanged),
  * the verdict is cached (positive + negative) to bound the probe rate,
  * a False verdict makes the leaf probes return "nothing" without issuing
    their multi-request HTTP calls.

All synthetic — no live server required.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import agent.model_metadata as mm


@pytest.fixture(autouse=True)
def _clear_reachable_cache():
    mm._reset_endpoint_reachable_cache()
    yield
    mm._reset_endpoint_reachable_cache()


def _fake_client(exc=None):
    """A context-manager httpx client stub whose .head() optionally raises."""
    client = MagicMock()
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)
    if exc is not None:
        client.head.side_effect = exc
    else:
        client.head.return_value = MagicMock(status_code=200)
    return client


def test_empty_url_is_unreachable():
    assert mm._endpoint_reachable("") is False
    assert mm._endpoint_reachable("   ") is False


def test_mocked_transport_is_reachable():
    """A stubbed HTTP transport (any response) counts as reachable, so probe
    unit tests that mock httpx keep running their logic against the mock."""
    with patch("httpx.Client", return_value=_fake_client()):
        assert mm._endpoint_reachable("http://localhost:11434/v1") is True


def test_connection_error_is_unreachable():
    import httpx

    with patch("httpx.Client", return_value=_fake_client(exc=httpx.ConnectError("boom"))):
        assert mm._endpoint_reachable("http://localhost:9/v1") is False


def test_unexpected_error_fails_open():
    """A non-connection error must never REMOVE a probe that might have worked."""
    with patch("httpx.Client", return_value=_fake_client(exc=ValueError("weird"))):
        assert mm._endpoint_reachable("http://localhost:1234/v1") is True


def test_verdict_is_cached_positive_and_negative():
    import httpx

    # Negative verdict cached: only one underlying probe for repeated calls.
    with patch.object(mm, "_probe_endpoint_reachable", return_value=False) as probe:
        assert mm._endpoint_reachable("http://localhost:9/v1") is False
        assert mm._endpoint_reachable("http://localhost:9/v1") is False
        assert probe.call_count == 1

    mm._reset_endpoint_reachable_cache()
    with patch.object(mm, "_probe_endpoint_reachable", return_value=True) as probe:
        assert mm._endpoint_reachable("http://localhost:11434/v1") is True
        assert mm._endpoint_reachable("http://localhost:11434/v1") is True
        assert probe.call_count == 1


def test_detect_local_server_type_short_circuits_when_unreachable():
    """When the gate says unreachable, detect issues NO HTTP probes."""
    with patch.object(mm, "_endpoint_reachable", return_value=False), \
         patch("httpx.Client") as client_cls:
        assert mm.detect_local_server_type("http://localhost:9/v1") is None
        client_cls.assert_not_called()


def test_local_ctx_probe_short_circuits_when_unreachable():
    with patch.object(mm, "_endpoint_reachable", return_value=False):
        assert mm._query_local_context_length_uncached("m", "http://localhost:9/v1") is None
        assert mm._query_ollama_api_show("m", "http://localhost:9/v1") is None


def test_reachable_endpoint_still_probes():
    """A reachable endpoint must let the probe logic run (gate does not strip a
    live server's metadata)."""
    show = MagicMock(status_code=200)
    show.json.return_value = {"model_info": {"llama.context_length": 131072}}
    client = MagicMock()
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = show
    client.head.return_value = MagicMock(status_code=200)

    with patch.object(mm, "_endpoint_reachable", return_value=True), \
         patch("agent.model_metadata.detect_local_server_type", return_value="ollama"), \
         patch("httpx.Client", return_value=client):
        result = mm._query_ollama_api_show("omnicoder-9b", "http://localhost:11434/v1")

    assert result == 131072
