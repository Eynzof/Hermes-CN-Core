"""Gateway cron startup/shutdown lifecycle (regression for #165).

The 0.20.0 CN runtimes died on ``gateway run`` with
``NameError: name 'InProcessCronScheduler' is not defined``: the boot path named the
scheduler class without binding it, so no gateway ever reached READY and the desktop
updater kept rolling back (cn.5 ~ cn.9). The boot phases now live in
``gateway/run_bootstrap.py`` and ``start_gateway`` delegates to them, so these tests
EXECUTE the real start/stop helpers with a controlled provider — a scheduler name the
module never bound raises the same NameError here — and pin the cron lifecycle the boot
path owes:

* armed exactly once, on the live shutdown event, with the drain gate and the profile
  set the ticker needs;
* stopped exactly once on teardown, with both cron and housekeeping threads wound down;
* NO ticker at all when startup validation degrades cron (the P-021 path), while
  housekeeping still starts and teardown still completes.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from cron.scheduler_provider import InProcessCronScheduler
from gateway import run as gateway_run
from gateway import run_bootstrap


class _ControlledScheduler(InProcessCronScheduler):
    """The real provider type (so the ``isinstance`` gates behave), with a quiet ticker.

    ``start`` blocks on the shutdown event exactly like the real tick loop, which is what
    makes the teardown assertions meaningful: only ``cron_stop.set()`` + ``stop()`` can
    release it.
    """

    def __init__(self) -> None:
        self.start_events: list[threading.Event] = []
        self.start_kwargs: list[dict] = []
        self.stop_calls = 0
        self.ticking = threading.Event()
        self.exited = threading.Event()

    def start(self, stop_event, **kwargs):
        self.start_events.append(stop_event)
        self.start_kwargs.append(dict(kwargs))
        self.ticking.set()
        stop_event.wait(30)
        self.exited.set()

    def stop(self) -> None:
        self.stop_calls += 1


class _Housekeeping:
    """Stand-in for ``gateway.run._start_gateway_housekeeping`` (records, then blocks)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.entered = threading.Event()
        self.exited = threading.Event()

    def __call__(self, stop_event, **kwargs):
        self.calls.append((stop_event, kwargs))
        self.entered.set()
        stop_event.wait(30)
        self.exited.set()


def _runner(**overrides) -> SimpleNamespace:
    """The subset of ``GatewayRunner`` the cron phases + exit verdict read."""
    fields = dict(
        config=SimpleNamespace(multiplex_profiles=False),
        adapters={},
        _profile_adapters=None,
        _primary_profile_name="default",
        _draining=False,
        _external_drain_active=False,
        should_exit_with_failure=False,
        exit_reason=None,
        exit_code=None,
        _restart_requested=False,
        _restart_via_service=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def cron_phases(monkeypatch, tmp_path):
    """Patch the seams production reads: provider resolution and the housekeeping thread."""
    provider = _ControlledScheduler()
    housekeeping = _Housekeeping()
    resolved: list[int] = []

    def _resolve():
        resolved.append(1)
        return provider

    monkeypatch.setattr("cron.scheduler_provider.resolve_cron_scheduler", _resolve)
    monkeypatch.setattr(gateway_run, "_cron_tick_profile_homes", lambda _cfg: [("default", tmp_path)])
    monkeypatch.setattr(gateway_run, "_start_gateway_housekeeping", housekeeping)
    return SimpleNamespace(provider=provider, housekeeping=housekeeping, resolved=resolved)


def _start(runner):
    async def _go():
        return run_bootstrap._start_gateway_start_cron_and_housekeeping(runner)

    return asyncio.run(_go())


async def _shutdown(runner, cron_stop, provider, cron_thread, housekeeping_thread):
    watcher_stop = threading.Event()
    watcher = threading.Thread(target=watcher_stop.wait, args=(30,), daemon=True)
    watcher.start()
    return await run_bootstrap._start_gateway_shutdown_tail(
        runner, None, cron_stop, provider, cron_thread, housekeeping_thread,
        watcher_stop, watcher, [False],
    )


def _patch_teardown(monkeypatch):
    monkeypatch.setattr(
        gateway_run, "_shutdown_mcp_servers_nonblocking",
        lambda **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(
        "hermes_cli.nous_auth_keepalive.stop_nous_auth_keepalive", lambda: None)


def test_gateway_boot_arms_the_scheduler_and_shutdown_stops_it(monkeypatch, tmp_path, cron_phases):
    """The #165 failure mode: boot must bind the scheduler class it arms.

    Executes the real start helper (a missing ``InProcessCronScheduler`` binding raises
    ``NameError`` right here, as it did in the shipped 0.20.0 runtimes), then the real
    shutdown tail, and asserts the cron lifecycle end to end.
    """
    monkeypatch.setattr(run_bootstrap, "_validate_cron_startup", lambda: True)
    _patch_teardown(monkeypatch)
    provider = cron_phases.provider
    runner = _runner()

    cron_stop, cron_provider, cron_thread, housekeeping_thread = _start(runner)

    assert cron_provider is provider
    assert isinstance(cron_stop, threading.Event) and not cron_stop.is_set()
    assert cron_thread is not None
    assert provider.ticking.wait(5), "the ticker never started"
    assert provider.start_events == [cron_stop], (
        "the ticker must be armed on the gateway's own shutdown event")
    start_kwargs = provider.start_kwargs[0]
    assert start_kwargs["adapters"] is runner.adapters
    # The live event loop is handed to the ticker so cron delivery can reach the adapters.
    assert isinstance(start_kwargs["loop"], asyncio.AbstractEventLoop)
    assert start_kwargs["default_profile"] == "default"
    assert callable(start_kwargs["profile_homes"])
    assert start_kwargs["profile_homes"]() == [("default", tmp_path)]
    # The drain gate stands down while the gateway drains, and only then.
    assert start_kwargs["can_dispatch"]() is True
    runner._draining = True
    assert start_kwargs["can_dispatch"]() is False
    runner._draining = False
    runner._external_drain_active = True
    assert start_kwargs["can_dispatch"]() is False
    runner._external_drain_active = False

    # Housekeeping shares the cron shutdown event and knows the live ticker it supervises.
    assert cron_phases.housekeeping.entered.wait(5)
    hk_stop, hk_kwargs = cron_phases.housekeeping.calls[0]
    assert hk_stop is cron_stop
    assert hk_kwargs["cron_provider"] is provider
    assert hk_kwargs["cron_thread"] is cron_thread

    verdict = asyncio.run(
        _shutdown(runner, cron_stop, provider, cron_thread, housekeeping_thread))

    assert verdict is True
    assert cron_stop.is_set()
    assert provider.stop_calls == 1, "shutdown must stop the provider exactly once"
    assert provider.exited.wait(5), "the tick loop never observed the shutdown event"
    assert housekeeping_thread is not cron_thread
    assert cron_phases.housekeeping.exited.wait(5)
    assert not cron_thread.is_alive()
    assert not housekeeping_thread.is_alive()


def test_degraded_cron_startup_starts_housekeeping_without_a_ticker(monkeypatch, cron_phases):
    """#806/#165 sibling path: a corrupt store must degrade cron, not crash the gateway."""
    monkeypatch.setattr(run_bootstrap, "_validate_cron_startup", lambda: False)
    _patch_teardown(monkeypatch)
    provider = cron_phases.provider
    runner = _runner()

    cron_stop, cron_provider, cron_thread, housekeeping_thread = _start(runner)

    assert cron_provider is None and cron_thread is None
    assert cron_phases.resolved == [], "a failed validation must not even resolve a provider"
    assert not provider.ticking.is_set()
    assert cron_phases.housekeeping.entered.wait(5)
    hk_stop, hk_kwargs = cron_phases.housekeeping.calls[0]
    assert hk_stop is cron_stop
    assert hk_kwargs["cron_provider"] is None and hk_kwargs["cron_thread"] is None

    verdict = asyncio.run(
        _shutdown(runner, cron_stop, cron_provider, cron_thread, housekeeping_thread))

    assert verdict is True
    assert cron_stop.is_set()
    assert provider.stop_calls == 0
    assert cron_phases.housekeeping.exited.wait(5)
    assert not housekeeping_thread.is_alive()


def test_corrupt_jobs_file_is_what_degrades_cron(tmp_path, monkeypatch):
    """The degraded arm is reached from a real unparseable store, not only from a stubbed seam."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", jobs_file)

    assert run_bootstrap._validate_cron_startup() is False


def test_valid_store_keeps_cron_armed(tmp_path, monkeypatch):
    """Guard against the degrade path swallowing every start (no gateway ever ticks)."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text('{"jobs": []}', encoding="utf-8")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", jobs_file)

    assert run_bootstrap._validate_cron_startup() is True


def test_run_facade_delegates_boot_to_the_bootstrap_module(monkeypatch):
    """``gateway.run.start_gateway`` is a facade: the boot that arms cron lives in run_bootstrap.

    #165 was reported against ``gateway/run.py``'s ``start_gateway``; the phase moved out from
    under it. Driving the facade with a spy proves the delegation is what actually runs (a
    re-inlined boot would answer without the bootstrap), and that the verdict propagates.
    """
    seen: dict = {}

    async def _bootstrap(config=None, replace=False, verbosity=0, force=False, idle_exit=False):
        seen.update(config=config, replace=replace, verbosity=verbosity,
                    force=force, idle_exit=idle_exit)
        return False

    monkeypatch.setattr(run_bootstrap, "start_gateway", _bootstrap)
    config = SimpleNamespace(multiplex_profiles=False)

    verdict = asyncio.run(gateway_run.start_gateway(
        config=config, replace=True, verbosity=2, force=True, idle_exit=True))

    assert verdict is False
    assert seen == {"config": config, "replace": True, "verbosity": 2,
                    "force": True, "idle_exit": True}
