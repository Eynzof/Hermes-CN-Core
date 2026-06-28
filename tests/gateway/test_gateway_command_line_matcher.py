"""Tests for the strict gateway command-line matcher.

Regression guard for the Windows ``hermes gateway restart`` silent-outage bug:
the previous loose substring match (``"... gateway" in cmdline``) false-matched
``gateway status``/``dashboard`` siblings and unrelated processes such as
``python -m tui_gateway``, which let ``restart()`` race a still-draining old
process and ``status``/``start`` report false positives.
"""

from __future__ import annotations

import pytest

from gateway.status import (
    looks_like_gateway_command_line as matches,
    looks_like_gateway_runtime_command_line as matches_runtime,
)


ACCEPT = [
    "pythonw.exe -m hermes_cli.main gateway run",
    r"C:\Users\me\hermes\venv\Scripts\pythonw.exe -m hermes_cli.main gateway run",
    "python -m hermes_cli.main --profile work gateway run",
    "python -m hermes_cli.main gateway run --replace",
    "python -m hermes_cli/main.py gateway run",
    "python gateway/run.py",
    "hermes-gateway.exe",
    "hermes gateway",          # bare `hermes gateway` defaults to run
    "hermes gateway run",
    # profile selector AFTER the `gateway` token (argv is profile-position
    # agnostic — _apply_profile_override strips --profile/-p anywhere)
    "hermes gateway --profile work run",
    "python -m hermes_cli.main gateway -p work run",
    "hermes gateway --profile=work run",
    # a profile literally NAMED "gateway"
    "hermes -p gateway gateway run",
    "python -m hermes_cli.main --profile gateway gateway run",
    # quoted Windows paths with spaces (shlex-aware tokenization)
    r'"C:\Program Files\Hermes\hermes-gateway.exe"',
    r'"C:\Program Files\Hermes\gateway\run.py" run',
    r'"C:\Program Files\Py\pythonw.exe" -m hermes_cli.main gateway run',
    # [CN-fork] P-034: the frozen desktop runtime binary is a full hermes CLI
    # (basename hermes-agent-cn-runtime-<os>-<arch>, see Desktop runtime.rs).
    "/opt/versions/0.16.0-cn.9/hermes-agent-cn-runtime-darwin-arm64 gateway run",
    "/opt/versions/0.16.0-cn.9/hermes-agent-cn-runtime-darwin-arm64 gateway run --replace",
    r'"C:\Program Files\Hermes\hermes-agent-cn-runtime-win32-x64.exe" gateway run --replace',
    "hermes-agent-cn-runtime-linux-x64 gateway",          # bare → defaults to run
]

REJECT = [
    "python -m tui_gateway",                              # unrelated module
    "python -m hermes_cli.main gateway status",           # other subcommand
    "python -m hermes_cli.main gateway restart",
    "python -m hermes_cli.main gateway stop",
    "python -m hermes_cli.main --profile x dashboard",    # non-gateway subcommand
    "some random python -m mygateway thing",
    # [CN-fork] P-034: management subcommands of the frozen binary must NOT read
    # as a live `run` (the basename is added to has_gateway_entry only, not the
    # unconditional gateway-dedicated-entrypoint scan).
    "/opt/versions/0.16.0-cn.9/hermes-agent-cn-runtime-darwin-arm64 gateway status",
    r'"C:\Program Files\Hermes\hermes-agent-cn-runtime-win32-x64.exe" gateway stop',
    "",
    None,
]


@pytest.mark.parametrize("cmd", ACCEPT)
def test_accepts_real_gateway_run(cmd):
    assert matches(cmd) is True


@pytest.mark.parametrize("cmd", REJECT)
def test_rejects_non_gateway_run(cmd):
    assert matches(cmd) is False


def test_runtime_matcher_accepts_no_supervisor_restart_process():
    assert matches("python -m hermes_cli.main gateway restart") is False
    assert matches_runtime("python -m hermes_cli.main gateway restart") is True
    assert matches_runtime("python -m hermes_cli.main gateway status") is False


def test_frozen_cn_runtime_recognized_as_gateway():
    """[CN-fork] P-034: the desktop's frozen PyInstaller binary is a hermes CLI.

    Regression guard for the multi-gateway WeChat "Session expired" outage
    (#42): the recognizer must see ``hermes-agent-cn-runtime-* gateway run`` as
    a live gateway (or get_running_pid / --replace / scoped-lock staleness all
    stop working), while keeping the binary's management subcommands distinct.
    """
    run = "/opt/0.16.0-cn.9/hermes-agent-cn-runtime-darwin-arm64 gateway run --replace"
    assert matches(run) is True
    assert matches_runtime(run) is True

    # Management subcommands of the SAME binary stay distinct — not a live run.
    status = "/opt/0.16.0-cn.9/hermes-agent-cn-runtime-darwin-arm64 gateway status"
    assert matches(status) is False
    assert matches_runtime(status) is False

    # `restart` is the no-supervisor runtime host: not a `run`, but can host it.
    restart = r"C:\Hermes\hermes-agent-cn-runtime-win32-x64.exe gateway restart"
    assert matches(restart) is False
    assert matches_runtime(restart) is True
