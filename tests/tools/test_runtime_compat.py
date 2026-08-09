"""Tests for tools.runtime_compat — frozen portable runtime helpers.

The CN desktop runtime ships as a PyInstaller-frozen executable with no
standalone ``python.exe``. Inside it ``sys.executable`` IS the Hermes CLI
binary itself, so any code that spawns ``sys.executable`` as a *Python
interpreter* (``[sys.executable, script.py]`` / ``-m`` / ``-c``) would run
``hermes <arg>`` and die with argparse's "invalid choice".  These helpers
are the shared chokepoint every such call site routes through.
"""

import textwrap

import pytest


@pytest.fixture
def frozen(monkeypatch):
    """Simulate the PyInstaller-frozen CN portable runtime."""
    import tools.runtime_compat as rc

    monkeypatch.setattr(rc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        rc.sys,
        "executable",
        "C:/Portable/hermes-agent-cn-runtime-win32-x64.exe",
    )
    return rc


@pytest.fixture
def source(monkeypatch):
    """Simulate a normal source/venv checkout."""
    import tools.runtime_compat as rc

    monkeypatch.delattr(rc.sys, "frozen", raising=False)
    monkeypatch.setattr(
        rc.sys,
        "executable",
        "C:/repo/.venv/Scripts/python.exe",
    )
    return rc


def test_is_frozen_runtime_source(source):
    assert source.is_frozen_runtime() is False


def test_is_frozen_runtime_frozen(frozen):
    assert frozen.is_frozen_runtime() is True


def test_hermes_cli_argv_source(source):
    assert source.hermes_cli_argv("serve", "--port", "0") == [
        "C:/repo/.venv/Scripts/python.exe",
        "-m", "hermes_cli.main",
        "serve", "--port", "0",
    ]


def test_hermes_cli_argv_frozen(frozen):
    """Under the frozen runtime sys.executable IS the CLI — no -m prefix."""
    assert frozen.hermes_cli_argv("serve", "--port", "0") == [
        "C:/Portable/hermes-agent-cn-runtime-win32-x64.exe",
        "serve", "--port", "0",
    ]


def test_run_python_script_in_process_captures_stdout_and_stdin(frozen, tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            print(f"argv0={sys.argv[0]!r}")
            print(f"arg={sys.argv[1] if len(sys.argv) > 1 else ''}")
            print(f"stdin={sys.stdin.read()}")
            """
        ),
        encoding="utf-8",
    )
    code, stdout, stderr = frozen.run_python_script_in_process(
        str(script), 10, argv=["--flag"], stdin_text="payload"
    )
    assert code == 0
    assert "argv0=" in stdout and "probe.py" in stdout
    assert "arg=--flag" in stdout
    assert "stdin=payload" in stdout
    assert stderr == ""


def test_run_python_script_in_process_honours_sys_exit(frozen, tmp_path):
    script = tmp_path / "exit.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            print("partial")
            print("oops", file=sys.stderr)
            sys.exit(3)
            """
        ),
        encoding="utf-8",
    )
    code, stdout, stderr = frozen.run_python_script_in_process(str(script), 10)
    assert code == 3
    assert stdout == "partial"
    assert "oops" in stderr


def test_run_python_script_in_process_timeout(frozen, tmp_path):
    script = tmp_path / "hang.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    code, stdout, stderr = frozen.run_python_script_in_process(str(script), 0.2)
    assert code == 1
    assert "timed out" in stderr


def test_run_python_script_in_process_crash(frozen, tmp_path):
    script = tmp_path / "crash.py"
    script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    code, stdout, stderr = frozen.run_python_script_in_process(str(script), 10)
    assert code == 1
    assert "RuntimeError" in stderr and "boom" in stderr
