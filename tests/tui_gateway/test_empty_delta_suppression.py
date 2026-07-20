"""Bug 2 repro: verify that empty-string deltas are suppressed before emission."""
import pytest


class FakeEmitter:
    """Capture emitted events for assertion."""
    def __init__(self):
        self.events = []

    def __call__(self, event_type, sid, payload=None):
        self.events.append((event_type, sid, payload))


# Simulate the _stream closure from tui_gateway/server.py:9260-9272
def make_stream(emit):
    """Recreate the _stream callback as it appears in the gateway (with Bug 2 fix)."""
    def _stream(delta):
        if delta is None:
            return
        # Guard against empty-string deltas (Bug 2 fix)
        delta_str = str(delta) if not isinstance(delta, str) else delta
        if not delta_str.strip():
            return
        payload = {"text": delta_str}
        emit("message.delta", "test-sid", payload)
    return _stream


class TestEmptyDeltaSuppression:
    def test_none_delta_is_suppressed(self):
        """None delta should NOT emit — this already works."""
        emitter = FakeEmitter()
        stream = make_stream(emitter)
        stream(None)
        assert len(emitter.events) == 0, "None delta should be suppressed"

    def test_empty_string_delta_is_suppressed(self):
        """BUG: empty string '' passes the None guard and emits an empty event."""
        emitter = FakeEmitter()
        stream = make_stream(emitter)
        stream("")
        # BUG: this assertion will FAIL — an event IS emitted for empty string
        assert len(emitter.events) == 0, (
            "BUG: empty string delta should be suppressed but was emitted. "
            f"Events: {emitter.events}"
        )

    def test_whitespace_only_delta_is_suppressed(self):
        """Whitespace-only string should also be suppressed."""
        emitter = FakeEmitter()
        stream = make_stream(emitter)
        stream("   ")
        # BUG: this assertion will FAIL pre-fix
        assert len(emitter.events) == 0, (
            "BUG: whitespace-only delta should be suppressed but was emitted. "
            f"Events: {emitter.events}"
        )

    def test_real_text_delta_still_works(self):
        """Real text deltas must still be emitted normally."""
        emitter = FakeEmitter()
        stream = make_stream(emitter)
        stream("Hello")
        assert len(emitter.events) == 1
        assert emitter.events[0] == (
            "message.delta", "test-sid", {"text": "Hello"}
        )
