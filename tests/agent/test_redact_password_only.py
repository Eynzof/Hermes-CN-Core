"""Password-only connection-string redaction regressions."""

import pytest
from agent.redact import redact_sensitive_text


@pytest.fixture(autouse=True)
def _ensure_redaction_enabled(monkeypatch):
    monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)
    monkeypatch.setattr("agent.redact._REDACT_ENABLED", True)


class TestPasswordOnlyConnStrings:
    """A connection string whose user part is empty — ``redis://:pw@host``, the way redis-cli
    and Mongo/AMQP URIs are usually written — printed its password in full. The DB rule
    required a non-empty user, so only the ``user:pass@`` form was masked.
    """

    SECRET = "S3cretPassw0rd"

    @pytest.mark.parametrize("url", [
        "redis://:" + SECRET + "@cache.internal:6379/0",
        "mongodb://:" + SECRET + "@mongo:27017/app",
        "amqp://:" + SECRET + "@rabbit:5672",
    ])
    def test_password_only_connection_strings_are_masked(self, url):
        from agent.redact import redact_for_egress, redact_terminal_output

        for out in (
            redact_sensitive_text(url, force=True),
            redact_for_egress(url),
            redact_terminal_output(url, "redis-cli -u $URL ping"),
        ):
            assert self.SECRET not in out
            assert "@" in out

    def test_user_and_password_connection_strings_still_masked(self):
        out = redact_sensitive_text("postgres://appuser:" + self.SECRET + "@db:5432/prod", force=True)
        assert out == "postgres://appuser:***@db:5432/prod"

    @pytest.mark.parametrize("text", [
        "https://example.com:8443/path",
        "redis://cache.internal:6379/0",
        "COMPASS=north",
    ])
    def test_hosts_ports_and_lookalikes_are_untouched(self, text):
        assert redact_sensitive_text(text, force=True) == text

    def test_repeated_scheme_prefixes_scan_in_linear_time(self):
        import time

        for unit in ("redis://:", "redis://u:"):
            text = unit * (100_000 // len(unit))
            t0 = time.perf_counter()
            redact_sensitive_text(text, force=True)
            assert time.perf_counter() - t0 < 0.5, unit
