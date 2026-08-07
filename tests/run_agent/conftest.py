"""Fast-path fixtures shared across tests/run_agent/.

Many tests in this directory exercise the retry/backoff paths in the
agent loop. Production code uses ``jittered_backoff(base_delay=5.0)``
with a ``while time.time() < sleep_end`` loop — a single retry test
spends 5+ seconds of real wall-clock time on backoff waits.

Mocking ``jittered_backoff`` to return 0.0 collapses the while-loop
to a no-op (``time.time() < time.time() + 0`` is false immediately),
which handles the most common case without touching ``time.sleep``.

We deliberately DO NOT mock ``time.sleep`` here — some tests
(test_interrupt_propagation, test_primary_runtime_restore, etc.) use
the real ``time.sleep`` for threading coordination or assert that it
was called with specific values. Tests that want to additionally
fast-path direct ``time.sleep(N)`` calls in production code should
monkeypatch ``run_agent.time.sleep`` locally (see
``test_anthropic_error_handling.py`` for the pattern).
"""
from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _fast_retry_backoff(monkeypatch):
    """Short-circuit retry backoff for all tests in this directory."""
    try:
        import run_agent
    except ImportError:
        return

    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)
    # The conversation loop was extracted out of run_agent.py into
    # ``agent.conversation_loop``, which imports ``jittered_backoff``
    # directly (``from agent.retry_utils import jittered_backoff``).
    # Patching ``run_agent.jittered_backoff`` alone misses every retry
    # path under the new module — tests that exercise rate-limit /
    # invalid-response / server-error retries burn real wall-clock
    # seconds per retry. Patch both for full coverage.
    try:
        from agent import conversation_loop as _conv_loop
        monkeypatch.setattr(_conv_loop, "jittered_backoff", lambda *a, **k: 0.0)
    except ImportError:
        pass


@pytest.fixture(autouse=True, scope="session")
def _no_real_file_logging():
    """Keep tests in this directory off the real on-disk log pipeline.

    Every ``AIAgent()`` construction calls ``hermes_logging.setup_logging()``,
    which spawns a background ``QueueListener`` thread whose rotating file
    handlers open/write/lock the REAL user-home ``agent.log``/``errors.log``
    for the lifetime of the pytest process.  On Windows, that listener
    thread's concurrent file-handle churn (portalocker-locked rotation on a
    log file shared with any other running Hermes process) races pytest's
    fd-level output capture (``os.dup2`` on fds 1/2 at test boundaries) and
    intermittently makes the terminal writer's ``flush()`` fail with
    ``OSError: [Errno 9] Bad file descriptor`` — pytest then dies with an
    INTERNALERROR after the last test (observed repeatedly as
    "1480 passed, 26 skipped" followed by INTERNALERROR).

    No test in this directory asserts on hermes_logging behavior, so stub
    the two setup entry points to no-ops for the whole session.  Tests for
    hermes_logging itself live outside tests/run_agent and are unaffected.
    Also stop any listener that was already started before this fixture ran
    (e.g. by an import-time agent construction).
    """
    try:
        import hermes_logging as hl
    except ImportError:
        yield
        return

    real_setup = hl.setup_logging
    real_verbose = hl.setup_verbose_logging
    hl.setup_logging = lambda *a, **k: None
    hl.setup_verbose_logging = lambda *a, **k: None
    try:
        hl._stop_queue_listener()
    except Exception:
        pass
    try:
        yield
    finally:
        hl.setup_logging = real_setup
        hl.setup_verbose_logging = real_verbose
        try:
            hl._stop_queue_listener()
        except Exception:
            pass
