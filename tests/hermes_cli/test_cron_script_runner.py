"""Tests for the frozen-runtime cron script entry point."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli._cron_script_runner import (
    INTERNAL_CRON_SCRIPT_ARG,
    dispatch_frozen_cron_script,
    run_cron_script,
)


def _scripts_home(tmp_path: Path) -> tuple[Path, Path]:
    hermes_home = tmp_path / "hermes-home"
    scripts_dir = hermes_home / "scripts"
    scripts_dir.mkdir(parents=True)
    return hermes_home, scripts_dir


def test_run_cron_script_matches_python_script_context(tmp_path, monkeypatch, capsys):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    (scripts_dir / "sibling.py").write_text('VALUE = "sibling-ok"\n', encoding="utf-8")
    script = scripts_dir / "probe.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            from sibling import VALUE

            print(__name__)
            print(sys.argv)
            print(VALUE)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(sys, "argv", ["runtime", INTERNAL_CRON_SCRIPT_ARG, str(script)])

    assert run_cron_script(str(script)) == 0

    output = capsys.readouterr().out
    assert "__main__" in output
    assert repr([str(script.resolve())]) in output
    assert "sibling-ok" in output


def test_run_cron_script_blocks_path_outside_scripts(tmp_path, monkeypatch, capsys):
    hermes_home, _ = _scripts_home(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text('print("must not run")\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert run_cron_script(str(outside)) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "outside the scripts directory" in captured.err


def test_run_cron_script_blocks_symlink_escape(tmp_path, monkeypatch, capsys):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text('print("must not run")\n', encoding="utf-8")
    link = scripts_dir / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert run_cron_script(str(link)) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "outside the scripts directory" in captured.err


def test_dispatch_requires_frozen_runtime(tmp_path, monkeypatch):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    script = scripts_dir / "probe.py"
    script.write_text('raise AssertionError("must not run")\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert dispatch_frozen_cron_script([INTERNAL_CRON_SCRIPT_ARG, str(script)]) is None


def test_dispatch_rejects_missing_script_argument(monkeypatch, capsys):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert dispatch_frozen_cron_script([INTERNAL_CRON_SCRIPT_ARG]) == 2

    assert "expected exactly one script path" in capsys.readouterr().err


def test_frozen_entry_runs_before_dotenv_loading(tmp_path):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    script = scripts_dir / "env_probe.py"
    script.write_text(
        'import os\nprint(os.environ.get("OPENAI_API_KEY", "ABSENT"))\n',
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text(
        "OPENAI_API_KEY=must-not-be-reloaded\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(repo_root)
    bootstrap = (
        "import sys; "
        "sys.frozen = True; "
        f"sys.argv = ['runtime', {INTERNAL_CRON_SCRIPT_ARG!r}, {str(script)!r}]; "
        "import hermes_cli.main"
    )

    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ABSENT"


def _patch_frozen_scheduler(monkeypatch, hermes_home: Path, runtime: Path, output: str):
    from cron import scheduler
    from tools.environments import local

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(scheduler.sys, "executable", str(runtime))
    monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(local, "build_subprocess_env", lambda: {})
    return scheduler, captured


def test_frozen_scheduler_routes_python_script_through_internal_runner(
    tmp_path, monkeypatch
):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    script = scripts_dir / "probe.py"
    script.write_text('print("ok")\n', encoding="utf-8")
    runtime = tmp_path / "hermes-agent-cn-runtime.exe"
    runtime.write_text("", encoding="utf-8")
    scheduler, captured = _patch_frozen_scheduler(
        monkeypatch, hermes_home, runtime, "ok\n"
    )

    success, output = scheduler._run_job_script("probe.py")

    assert success is True
    assert output == "ok"
    assert captured["argv"] == [
        str(runtime),
        INTERNAL_CRON_SCRIPT_ARG,
        str(script.resolve()),
    ]
    assert captured["kwargs"]["env"]["HERMES_HOME"] == str(hermes_home.resolve())


def test_frozen_prerun_script_output_is_injected(tmp_path, monkeypatch):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    script = scripts_dir / "data.py"
    script.write_text('print("new PR: #123 fix typo")\n', encoding="utf-8")
    runtime = tmp_path / "hermes-agent-cn-runtime.exe"
    runtime.write_text("", encoding="utf-8")
    scheduler, captured = _patch_frozen_scheduler(
        monkeypatch, hermes_home, runtime, "new PR: #123 fix typo\n"
    )

    prompt = scheduler._build_job_prompt(
        {"prompt": "Report notable changes.", "script": "data.py"}
    )

    assert "new PR: #123 fix typo" in prompt
    assert "Report notable changes." in prompt
    assert captured["argv"] == [
        str(runtime),
        INTERNAL_CRON_SCRIPT_ARG,
        str(script.resolve()),
    ]


def test_frozen_no_agent_job_delivers_script_output(tmp_path, monkeypatch):
    hermes_home, scripts_dir = _scripts_home(tmp_path)
    script = scripts_dir / "alert.py"
    script.write_text('print("RAM 92% on host")\n', encoding="utf-8")
    runtime = tmp_path / "hermes-agent-cn-runtime.exe"
    runtime.write_text("", encoding="utf-8")
    scheduler, captured = _patch_frozen_scheduler(
        monkeypatch, hermes_home, runtime, "RAM 92% on host\n"
    )

    success, doc, final_response, error = scheduler.run_job(
        {
            "id": "frozen-no-agent",
            "name": "Runtime watchdog",
            "script": "alert.py",
            "no_agent": True,
        }
    )

    assert success is True
    assert error is None
    assert final_response == "RAM 92% on host"
    assert "RAM 92% on host" in doc
    assert captured["argv"] == [
        str(runtime),
        INTERNAL_CRON_SCRIPT_ARG,
        str(script.resolve()),
    ]
