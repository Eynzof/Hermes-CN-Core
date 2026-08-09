"""Runtime-compatibility helpers for the PyInstaller-frozen CN portable runtime.

The CN desktop runtime ships as a PyInstaller-frozen executable
(``hermes-agent-cn-runtime-*.exe``) with **no standalone ``python.exe``**.
Inside it ``sys.executable`` IS the Hermes CLI binary itself.  Spawning it as
a Python interpreter — ``[sys.executable, script.py]``,
``[sys.executable, "-m", module]`` or ``[sys.executable, "-c", code]`` —
runs ``hermes <script.py>`` / ``hermes -m module`` / ``hermes -c code`` and
argparse rejects the argument as an invalid subcommand ("invalid choice:
'whitecat_inspect.py'").

Every subprocess site that treats ``sys.executable`` as a Python interpreter
must route through these helpers so the same code works in a source checkout
and in the frozen portable runtime:

* ``hermes_cli_argv(...)`` — re-invoke the Hermes CLI itself.  Under the
  frozen runtime the binary IS the CLI, so the ``-m hermes_cli.main`` prefix
  must be dropped.
* ``run_python_script_in_process(...)`` — execute a trusted Python script
  in-process with the current interpreter (mirrors the cron fix), used when
  there is no standalone python to spawn.
"""

import io
import runpy
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from typing import Optional, Sequence


def is_frozen_runtime() -> bool:
    """True when running inside the PyInstaller-frozen CN portable runtime."""
    return bool(getattr(sys, "frozen", False))


def hermes_cli_argv(*extra_args: str) -> list[str]:
    """Return an argv that re-invokes the Hermes CLI with ``extra_args``.

    Frozen runtime: ``sys.executable`` IS the CLI binary, so the argv is
    simply ``[sys.executable, *extra_args]`` — adding ``-m hermes_cli.main``
    would make argparse reject ``-m`` as an invalid subcommand.

    Source checkout / venv: ``[sys.executable, "-m", "hermes_cli.main", *extra_args]``.
    """
    if is_frozen_runtime():
        return [sys.executable, *extra_args]
    return [sys.executable, "-m", "hermes_cli.main", *extra_args]


def run_python_script_in_process(
    script_path: str,
    timeout: float,
    *,
    argv: Optional[Sequence[str]] = None,
    stdin_text: Optional[str] = None,
) -> tuple[int, str, str]:
    """Execute a Python script in-process, returning ``(exit_code, stdout, stderr)``.

    Used ONLY under the PyInstaller-frozen runtime (see :func:`is_frozen_runtime`),
    where ``sys.executable`` is the Hermes CLI binary and spawning it with a
    script path would run ``hermes <script>.py`` — argparse then rejects the
    path as an invalid subcommand ("invalid choice").

    The trusted script runs in-process with ``runpy.run_path`` so
    ``if __name__ == "__main__":`` and ``sys.argv`` behave exactly like
    ``python script.py``.  ``sys.exit`` codes are honoured, stdout/stderr are
    captured (including a JSON payload on stdin when ``stdin_text`` is given),
    and execution is bounded by ``timeout`` on a best-effort basis: a hung
    script is abandoned (the daemon thread leaks but the caller thread is
    returned), matching the subprocess timeout contract without being able to
    kill the work.
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    state = {"exit_code": 0}

    prior_argv = sys.argv
    sys.argv = [str(script_path), *(list(argv) if argv else [])]
    prior_stdin = sys.stdin
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)

    def _exec() -> None:
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit as exc:
            code = exc.code
            if code is None:
                state["exit_code"] = 0
            elif isinstance(code, int):
                state["exit_code"] = code
            else:
                state["exit_code"] = 1
                err_buf.write(f"{code}\n")
        except BaseException as exc:  # noqa: BLE001 — a script crash is a failure
            state["exit_code"] = 1
            err_buf.write(f"{type(exc).__name__}: {exc}\n")

    thread = threading.Thread(
        target=_exec,
        name="runtime-inprocess-script",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:
        sys.argv = prior_argv
        sys.stdin = prior_stdin
        return 1, "", f"Script execution failed: {exc}"

    thread.join(timeout)
    timed_out = thread.is_alive()
    sys.argv = prior_argv
    sys.stdin = prior_stdin

    stdout = out_buf.getvalue().strip()
    stderr = err_buf.getvalue().strip()
    if timed_out:
        return 1, stdout, f"Script timed out after {timeout}s"
    return state["exit_code"], stdout, stderr
