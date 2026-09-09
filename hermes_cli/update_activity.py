"""Live work admission for a desktop-owned runtime, shared across child processes.

Only enabled by the owning desktop. Conversation history and idle sessions are
not evidence of running work. The desktop takes the same file lock before it
claims the short maintenance window used to activate an update.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import time
import uuid

from hermes_cli.active_sessions import _FileLock, _pid_liveness, _process_start_time


def _alive(entry: dict) -> bool:
    return _pid_liveness(entry.get("pid"), entry.get("started")) is not False


def _read(path: Path) -> dict:
    if not path.exists():
        return {"entries": [], "maintenance": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    state["entries"] = [entry for entry in state["entries"] if _alive(entry)]
    gate = state.get("maintenance")
    if gate and (not _alive(gate) or gate["expires"] <= time.time()):
        state["maintenance"] = None
    return state


def _write(path: Path, state: dict) -> None:
    pending = path.with_suffix(f".{os.getpid()}.tmp")
    pending.write_text(json.dumps(state), encoding="utf-8")
    os.replace(pending, path)


class UpdateInProgress(RuntimeError):
    pass


class UpdateActivity:
    def __init__(self, kind: str, session_id: str = ""):
        root = os.environ.get("HERMES_DESKTOP_UPDATE_REGISTRY", "").strip()
        self.root = Path(root) if root else None
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.session_id = session_id

    def __enter__(self):
        if self.root is None:
            return self
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "activity.json"
        with _FileLock(self.root / "activity.lock"):
            state = _read(path)
            if state.get("maintenance"):
                raise UpdateInProgress("软件更新正在生效，请稍后再开始任务。")
            state["entries"].append({
                "id": self.id, "kind": self.kind, "sessionId": self.session_id,
                "pid": os.getpid(), "started": _process_start_time(os.getpid()),
            })
            _write(path, state)
        return self

    def __exit__(self, *_):
        if self.root is None:
            return
        path = self.root / "activity.json"
        with _FileLock(self.root / "activity.lock"):
            state = _read(path)
            state["entries"] = [e for e in state["entries"] if e["id"] != self.id]
            _write(path, state)


def track_update_activity(kind: str, *, skip_during_update: bool = False):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not os.environ.get("HERMES_DESKTOP_UPDATE_REGISTRY"):
                return function(*args, **kwargs)
            owner = args[0] if args else None
            session_id = str(getattr(owner, "session_id", "") or "")
            activity = UpdateActivity(kind, session_id)
            try:
                activity.__enter__()
            except UpdateInProgress:
                if skip_during_update:
                    return 0
                raise
            try:
                return function(*args, **kwargs)
            finally:
                activity.__exit__()
        return wrapped
    return decorate


def initialize_update_activity() -> None:
    root = os.environ.get("HERMES_DESKTOP_UPDATE_REGISTRY", "").strip()
    if not root:
        return
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    with _FileLock(directory / "activity.lock"):
        path = directory / "activity.json"
        _write(path, _read(path))
