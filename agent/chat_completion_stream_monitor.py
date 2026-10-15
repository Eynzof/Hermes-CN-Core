"""Display and heartbeat phase of the request-local streaming monitor."""

import contextlib
import os
import time
from types import SimpleNamespace

from agent import chat_completion_wait_notice as wn
from agent.model_metadata import is_local_endpoint


# [CN-fork] P-022 knobs. The stale detector aborts the live transport so the reader unwinds
# and the retry loop reconnects; when the reader is parked anyway (no FIN from the provider, a
# POSIX-only shutdown that cannot wake a Windows recv(), nested thread pools) the monitor must
# stop after a bounded number of aborts instead of re-killing forever.
_DEFAULT_STALE_KILL_GRACE = 10.0
_DEFAULT_STALE_MAX_KILLS = 3


def _stale_kill_grace_seconds() -> float:
    """Seconds between stale aborts (HERMES_STREAM_STALE_KILL_GRACE)."""
    try:
        return max(1.0, float(os.getenv("HERMES_STREAM_STALE_KILL_GRACE", _DEFAULT_STALE_KILL_GRACE)))
    except (TypeError, ValueError):
        return _DEFAULT_STALE_KILL_GRACE


def _max_stale_kills_setting() -> int:
    """Aborts allowed before the wedged attempt is abandoned (HERMES_STREAM_STALE_MAX_KILLS)."""
    try:
        return max(1, int(os.getenv("HERMES_STREAM_STALE_MAX_KILLS", _DEFAULT_STALE_MAX_KILLS)))
    except (TypeError, ValueError):
        return _DEFAULT_STALE_MAX_KILLS


class StreamingWaitMonitor:
    def _poll_local_load_notice(self, now: float) -> bool:
        """Managed local server: surface a cold model's weight-load progress
        instead of the 60s neutral "waiting on <model>" notice. Polled ~1s only while no
        REAL chunk arrived for 2s+ (never during healthy token flow); in-memory,
        no network. True while loading = heartbeat liveness, skip the rest of
        this iteration (the stale detector's local floor dwarfs any load)."""
        from agent.chat_completion_helpers import _managed_local_load_notice

        m = self._mon
        if now - self.last_chunk_time["t"] < 2.0 or now - m.last_load_poll < 1.0:
            return False
        m.last_load_poll = now
        _load_notice = _managed_local_load_notice(self.agent, self.api_kwargs)
        if _load_notice is not None:
            m.wait_notice_started_ts = None  # The local loader now owns the display.
            m.wait_notice.reset()
            self.agent._emit_wait_notice(_load_notice)
            self.agent._touch_activity("local model loading")
            m.load_notice_shown, m.load_notice_misses, m.last_heartbeat = True, 0, now  # loading IS liveness
            return True
        if m.load_notice_shown:
            # One missed sample is routine (probe timeout under load); clearing on it strobed the line.
            m.load_notice_misses += 1
            if m.load_notice_misses >= 3:
                m.load_notice_shown, m.load_notice_misses = False, 0
                self.agent._emit_wait_notice("")
        return False

    def _heartbeat(self, waiting_secs: int) -> None:
        """Gateway inactivity heartbeat: the start-to-first-chunk gap (thinking,
        local prefill) can exceed the gateway timeout."""
        if waiting_secs >= 60.0:
            # No chunks for 60s+: say WHAT the wait is and WHEN recovery kicks in —
            # once per silence, not every heartbeat (#92550).
            stale = self._stream_stale_timeout
            watchdog = ("stream stale", stale - waiting_secs) if stale is not None and stale != float("inf") else None
            diag = getattr(getattr(self, "clients", None), "diag", None)
            phase = "post_chunk" if isinstance(diag, dict) and diag.get("first_chunk_at") else "first_chunk"
            if not self._mon.wait_notice.should_emit(phase, watchdog):
                self.agent._touch_activity(f"waiting for stream response ({waiting_secs}s, {phase})")
                return
            self._mon.wait_notice_started_ts = self._mon.last_heartbeat
            self.agent._emit_wait_notice(wn.wait_notice_text(
                self.api_kwargs.get('model', 'the provider'), waiting_secs, phase, watchdog))
        else:
            # Chunks are flowing — keep the tracker fresh, leave the display alone.
            self.agent._touch_activity(f"waiting for stream response ({waiting_secs}s, no chunks yet)")

    def _monitor_loop(self) -> None:
        _HEARTBEAT_INTERVAL = 30.0  # seconds between gateway activity touches
        self._mon = SimpleNamespace(
            last_heartbeat=time.time(), last_load_poll=0.0,
            load_notice_shown=False, load_notice_misses=0, wait_notice_started_ts=None,
            wait_notice=wn.WaitNoticeState(),
        )
        _is_local_base = bool(self.agent.base_url) and is_local_endpoint(self.agent.base_url)
        # [CN-fork] P-022 bounded escalation state (see the module helpers above).
        _stale_kill_grace = _stale_kill_grace_seconds()
        _max_stale_kills = _max_stale_kills_setting()
        _stale_kill_count = 0
        _last_stale_kill_at = 0.0
        _chunk_time_at_last_kill = 0.0
        while not self._call_done.is_set():
            self._call_done.wait(timeout=0.3)
            _hb_now = time.time()
            if _is_local_base and self._poll_local_load_notice(_hb_now):
                continue
            # Reasoning callbacks do not clear the classic CLI spinner. The empty
            # protocol payload resets status without adding synthetic reasoning.
            if (self._mon.wait_notice_started_ts is not None
                    and self.last_chunk_time["t"] > self._mon.wait_notice_started_ts):
                self.agent._emit_wait_notice("")
                self._mon.wait_notice_started_ts = None
                self._mon.wait_notice.reset()
            if _hb_now - self._mon.last_heartbeat >= _HEARTBEAT_INTERVAL:
                self._mon.last_heartbeat = _hb_now
                self._heartbeat(int(_hb_now - self.last_chunk_time["t"]))
            _now = time.time()
            _stale_elapsed = _now - self.last_chunk_time["t"]
            # [CN-fork] P-022: abort at most once per grace window, and only count an abort as a
            # fresh attempt when the timer actually moved since the last one (a reconnect resets
            # ``last_chunk_time`` at the start of its new attempt). The merged upstream variant had
            # no grace gate and reset the timestamp inside the kill, so a wedged reader was
            # re-killed every poll tick and the budget below could never escalate.
            if (
                _stale_elapsed > self._stream_stale_timeout
                and (_now - _last_stale_kill_at) >= _stale_kill_grace
            ):
                if _last_stale_kill_at and self.last_chunk_time["t"] > _chunk_time_at_last_kill:
                    # A post-abort retry rearmed the timer: the previous abort worked, so restart
                    # the reconnect budget instead of escalating a connection that recovered.
                    _stale_kill_count = 0
                _stale_kill_count += 1
                _last_stale_kill_at = _now
                _chunk_time_at_last_kill = self.last_chunk_time["t"]
                self._mon.wait_notice_started_ts = None  # Reconnect status has its own owner.
                self._mon.wait_notice.reset()
                self._kill_stale_stream(
                    _stale_elapsed, kill_count=_stale_kill_count, max_kills=_max_stale_kills,
                )
                # Give the aborted attempt a grace window to unwind before judging it. A call
                # that is still running here did NOT unwind: either the provider socket is
                # half-open and the abort could not wake the reader, or the reader is parked in
                # a nested pool. Either way the escalation below is the only bound left.
                if self._call_done.wait(timeout=_stale_kill_grace):
                    continue
                if _stale_kill_count >= _max_stale_kills:
                    self._abandon_wedged_stream(_stale_elapsed, _stale_kill_count)
                    return
            if self.agent._interrupt_requested:
                self._abort_for_interrupt(_stale_elapsed)
                return

    def _abandon_wedged_stream(self, stale_elapsed: float, kill_count: int) -> None:
        """[CN-fork] P-022: stop the unbounded hang with a synthesized timeout.

        The request thread is a daemon (or the inline caller of a nested-pool context) and is
        left behind exactly like the non-streaming stale path does: the turn surfaces an error
        instead of parking forever. A response/error already produced by the attempt wins.
        """
        if self.result["error"] is None and self.result["response"] is None:
            self.result["error"] = TimeoutError(
                f"Streaming API call stalled: no chunks for {int(stale_elapsed)}s across "
                f"{kill_count} reconnect attempts (stale threshold "
                f"{int(self._stream_stale_timeout)}s)."
            )
        with contextlib.suppress(Exception):
            self.agent._emit_status(
                "❌ Provider stopped responding and could not be reconnected after "
                f"{kill_count} attempts — ending this turn. Please try again."
            )
