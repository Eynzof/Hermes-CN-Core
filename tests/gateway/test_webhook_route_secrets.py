"""Webhook routes require usable secrets."""
import asyncio
import pytest
from gateway.platforms.webhook import _is_usable_secret
from tests.gateway.test_webhook_adapter import _make_adapter

class TestBlankRouteSecretFailsClosed:
    @pytest.mark.parametrize("secret", ["   ", "\t", "\n", 123])
    def test_connect_rejects_an_unusable_route_secret(self, secret):
        adapter = _make_adapter(routes={"hook": {"secret": secret}})
        with pytest.raises(ValueError, match="missing, blank, or not a string"):
            asyncio.run(adapter.connect())
        # The hot-reload path applies the same guard to agent-created dynamic routes.
        assert adapter._dynamic_route_allowed("hook", {"secret": secret}) is False



@pytest.mark.parametrize(
    "value,usable",
    [("s3cret", True), ("x", True), (" x ", True),
     ("", False), ("   ", False), ("\t\n", False),
     (None, False), (b"bytes", False), (123, False)],
)
def test_is_usable_secret_classifies_configured_secrets(value, usable):
    assert _is_usable_secret(value) is usable
