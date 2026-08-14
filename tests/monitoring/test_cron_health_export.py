from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _metric(snapshot, name):
    return next(metric for metric in snapshot.metrics if metric.name == name)






def test_execution_projection_is_opaque_bounded_and_content_free():
    from agent.monitoring.cron_health import project_execution_event

    event = project_execution_event(
        {
            "id": "execution-private-id",
            "job_id": "Payroll for alice@example.com and token top-secret-token",
            "source": "builtin",
            "status": "failed",
            "claimed_at": "2026-07-24T12:00:00+00:00",
            "started_at": "2026-07-24T12:00:01+00:00",
            "finished_at": "2026-07-24T12:00:03.250000+00:00",
            "error": "Bearer top-secret-token rejected for alice@example.com",
        },
        delivery_outcome="failed",
    ).to_dict()

    assert event["event"] == "cron_execution"
    assert event["status"] == "failed"
    assert event["job_key"].startswith("sha256:")
    assert len(event["job_key"]) == len("sha256:") + 24
    assert event["duration_ms"] == 2250
    assert event["delivery_outcome"] == "failed"
    assert event["error_class"] == "auth_failed"
    assert "job_id" not in event
    assert "error" not in event
    assert "alice@example.com" not in str(event)
    assert "top-secret-token" not in str(event)






@pytest.mark.parametrize("message", ["oauth refresh failed", "tokenizer crashed", "HTTP 4015"])
def test_error_classification_avoids_auth_substring_false_positives(message):
    from agent.monitoring.cron_health import classify_cron_error

    assert classify_cron_error(message) == "unknown"






def test_terminal_execution_emission_flushes_and_failures_are_fail_open(monkeypatch):
    from agent.monitoring import cron_health, emitter

    calls = []

    class FakeEmitter:
        def emit(self, event):
            calls.append(("emit", event.to_dict()["status"]))

        def flush(self, timeout):
            calls.append(("flush", timeout))
            raise RuntimeError("collector unavailable")

    monkeypatch.setattr(emitter, "get_emitter", lambda: FakeEmitter())

    cron_health.emit_execution_state(
        {"job_id": "private", "source": "builtin", "status": "completed"}
    )

    assert calls == [("emit", "completed"), ("flush", 1.0)]






def test_monitoring_docs_distinguish_relay_health_scope_and_terminal_flush():
    from pathlib import Path

    text = Path("docs/observability/monitoring.md").read_text(encoding="utf-8")

    assert "Hermes Agent-owned Relay transport health" in text
    assert "authoritative shared connector/platform state" in text
    assert "up to one second" in text
    assert "terminal" in text
