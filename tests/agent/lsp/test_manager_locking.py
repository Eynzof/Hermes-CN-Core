"""Concurrency invariants for the LSP service state lock."""
from __future__ import annotations

import asyncio

import agent.lsp.manager as manager
from agent.lsp.manager import LSPService
from agent.lsp.servers import ServerDef


def test_reused_multiroot_client_attaches_outside_state_lock(monkeypatch):
    """Awaitable multi-root attachment must never run while ``_state_lock`` is held.

    A synchronous ``threading.Lock`` held across an await can deadlock the single
    LSP event-loop thread as soon as another coroutine tries to acquire that lock.
    """
    service = LSPService(
        enabled=False,
        wait_mode="document",
        wait_timeout=0.1,
        install_strategy="manual",
        idle_timeout=0,
    )
    root = "/repo/worktree-b"
    server = ServerDef(
        server_id="pyright",
        extensions=(".py",),
        resolve_root=lambda _file_path, _workspace_root: root,
        build_spawn=lambda _root, _ctx: None,
        multi_root=True,
    )

    class StubClient:
        is_running = True

        async def add_workspace_folder(self, attached_root: str) -> None:
            assert attached_root == root
            lock_was_free = service._state_lock.acquire(blocking=False)
            if lock_was_free:
                service._state_lock.release()
            assert lock_was_free, (
                "_state_lock must be released before awaiting workspace attachment"
            )

    client = StubClient()
    service._clients[(server.server_id, "")] = client  # multi-root client key
    service._last_used[(server.server_id, "")] = 0.0

    monkeypatch.setattr(manager, "find_server_for_file", lambda _path: server)
    monkeypatch.setattr(
        manager,
        "resolve_workspace_for_file",
        lambda _path: ("/repo", True),
    )
    monkeypatch.setattr(manager.eventlog, "log_active", lambda *_args, **_kwargs: None)

    result = asyncio.run(service._get_or_spawn("/repo/worktree-b/example.py"))

    assert result is client


def test_delta_baseline_is_capped_by_write_recency(monkeypatch, tmp_path):
    """Driven through the production entry points (``snapshot_baseline`` before a write, ``_apply_delta``
    rolling the baseline forward after one): baselines beyond _DELTA_BASELINE_CAP evict the path
    written longest ago, and re-touching a path refreshes it instead of aging it out.

    The keys are real absolute paths under ``tmp_path``: the service stores
    ``os.path.abspath(file_path)``, which is only an identity for an already-absolute native path
    (on Windows ``/repo/f0.py`` becomes ``C:\\repo\\f0.py``, so a POSIX-literal key would probe
    nothing and pass vacuously).
    """
    repo = tmp_path / "repo"
    base = str(repo)
    f0, f1, new = str(repo / "f0.py"), str(repo / "f1.py"), str(repo / "new.py")
    service = LSPService(enabled=False, wait_mode="document", wait_timeout=0.1,
                         install_strategy="manual", idle_timeout=0)
    monkeypatch.setattr(service, "enabled_for", lambda _p: True)
    server_diags: list = []

    class _Loop:  # stands in for the (unstarted) background loop; returns the server's current diagnostics
        def run(self, coro, timeout=None):
            coro.close()
            return list(server_diags)

    monkeypatch.setattr(service, "_loop", _Loop())

    class _LockedDict(dict):  # every mutation of the shared baseline dict must run under _state_lock
        def __setitem__(self, k, v):
            assert service._state_lock.locked()
            super().__setitem__(k, v)

        def __delitem__(self, k):
            assert service._state_lock.locked()
            super().__delitem__(k)

    service._delta_baseline = _LockedDict()
    cap = manager._DELTA_BASELINE_CAP
    for i in range(cap):
        service.snapshot_baseline(str(repo / f"f{i}.py"))
    server_diags.append({"message": "rewritten"})
    assert service._apply_delta(f0, [{"message": "rewritten"}], None) == [{"message": "rewritten"}]
    server_diags.clear()
    service.snapshot_baseline(new)
    assert len(service._delta_baseline) == cap
    assert f1 not in service._delta_baseline
    assert service._delta_baseline[f0] == [{"message": "rewritten"}]
    assert new in service._delta_baseline
