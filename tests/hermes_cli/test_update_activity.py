import json
import os
import time
import subprocess
import sys

import pytest

from hermes_cli.update_activity import UpdateActivity, initialize_update_activity, track_update_activity


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DESKTOP_UPDATE_REGISTRY", str(tmp_path))
    initialize_update_activity()
    return tmp_path / "activity.json"


def test_tracks_nested_work_and_releases_on_error(registry):
    with UpdateActivity("cron"):
        with pytest.raises(ValueError):
            with UpdateActivity("conversation", "session-a"):
                assert [e["kind"] for e in json.loads(registry.read_text())["entries"]] == ["cron", "conversation"]
                raise ValueError("model failed")
        assert len(json.loads(registry.read_text())["entries"]) == 1
    assert json.loads(registry.read_text())["entries"] == []


def test_maintenance_refuses_new_work_without_losing_registry(registry):
    data = json.loads(registry.read_text())
    data["maintenance"] = {"pid": os.getpid(), "started": None, "expires": time.time() + 60}
    registry.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="更新正在生效"):
        with UpdateActivity("conversation"):
            pytest.fail("work must not start")
    assert json.loads(registry.read_text())["entries"] == []


def test_expired_gate_does_not_leave_runtime_blocked(registry):
    registry.write_text(json.dumps({"entries": [], "maintenance": {"pid": os.getpid(), "started": None, "expires": time.time() - 1}}))
    with UpdateActivity("conversation"):
        data = json.loads(registry.read_text())
        assert data["maintenance"] is None
        assert len(data["entries"]) == 1


def test_non_desktop_execution_is_unchanged(monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP_UPDATE_REGISTRY", raising=False)

    @track_update_activity("conversation")
    def run(value):
        return value * 2

    assert run(7) == 14


def test_another_process_is_visible_and_removed_after_exit(registry):
    child = subprocess.Popen([sys.executable, "-c", "from hermes_cli.update_activity import UpdateActivity; import sys; activity=UpdateActivity('conversation', 'child'); activity.__enter__(); print('ready', flush=True); sys.stdin.read()"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        initialize_update_activity()
        assert any(e["pid"] == child.pid for e in json.loads(registry.read_text())["entries"])
        child.communicate("", timeout=10)
        initialize_update_activity()
        assert json.loads(registry.read_text())["entries"] == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_cron_queue_remains_protected_until_release(registry):
    from cron.scheduler import try_register_running_job, release_running_job
    assert try_register_running_job("update-test-queued")
    try:
        assert any(e["sessionId"] == "update-test-queued" for e in json.loads(registry.read_text())["entries"])
    finally:
        release_running_job("update-test-queued")
    assert json.loads(registry.read_text())["entries"] == []


def test_tick_does_not_claim_or_advance_jobs_during_update(registry, monkeypatch):
    from cron import scheduler
    registry.write_text(json.dumps({"entries": [], "maintenance": {"pid": os.getpid(), "started": None, "expires": time.time() + 60}}))
    monkeypatch.setattr(scheduler, "get_due_jobs", lambda: pytest.fail("must not claim due jobs"))
    assert scheduler.tick() == 0
