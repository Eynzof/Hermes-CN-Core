"""An empty successful agent response is still a failed scheduled job."""
from unittest.mock import patch


def test_tick_marks_empty_response_as_error(tmp_path, monkeypatch):
    """When run_job returns success=True but final_response is empty,
    tick() should mark the job as error so last_status != 'ok'.
    (issue #8585)
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from cron.scheduler import tick

    job = {
        "id": "empty-job",
        "name": "empty-test",
        "prompt": "do something",
        "schedule": "every 1h",
        "enabled": True,
        "next_run_at": "2020-01-01T00:00:00",
        "deliver": "local",
        "last_status": None,
    }

    with patch("cron.scheduler._hermes_home", tmp_path), \
         patch("cron.scheduler.get_due_jobs", return_value=[job]), \
         patch("cron.scheduler.claim_job_for_fire", return_value=True), \
         patch("cron.scheduler.mark_job_run") as mock_mark, \
         patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
         patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
         patch("cron.scheduler.run_job", return_value=(True, "output", "", None)):
        tick(verbose=False)

    # Should be called with success=False because final_response is empty
    mock_mark.assert_called_once()
    call_args = mock_mark.call_args
    assert call_args[0][0] == "empty-job"
    assert call_args[0][1] is False  # success should be False
    assert "empty" in call_args[0][2].lower()  # error should mention empty

