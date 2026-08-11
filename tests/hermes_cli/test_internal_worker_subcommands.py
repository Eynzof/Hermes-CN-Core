"""Tests for the hidden internal worker subcommands (frozen portable runtime).

The PyInstaller-frozen CN portable runtime has no standalone ``python.exe`` —
``sys.executable`` IS the Hermes CLI binary.  tui_gateway workers and the
gateway restart/update helpers are therefore dispatched through hidden CLI
subcommands (``__slash-worker``, ``__compute-host``, ``__gateway-restart-watch``,
``__update-gateway-helper``) instead of ``python -m <module>`` / ``-c`` spawns.

These tests assert the subcommands exist on the real parser and that the
``_BUILTIN_SUBCOMMANDS`` fast-path knows about them (so plugin discovery is
skipped).
"""

import importlib

import pytest


@pytest.fixture
def hermes_main():
    return importlib.import_module("hermes_cli.main")


@pytest.fixture
def subparsers():
    from hermes_cli._parser import build_top_level_parser

    _parser, subparsers, _chat_parser = build_top_level_parser()
    hermes_main = importlib.import_module("hermes_cli.main")
    hermes_main._register_internal_worker_subcommands(subparsers)
    return subparsers


def test_hidden_internal_subcommands_registered(subparsers):
    choices = set(subparsers.choices.keys())
    for name in (
        "__slash-worker",
        "__compute-host",
        "__gateway-restart-watch",
        "__update-gateway-helper",
    ):
        assert name in choices, f"missing hidden subcommand {name!r}"


def test_hidden_internal_subcommands_in_builtin_fast_path(hermes_main):
    for name in (
        "__slash-worker",
        "__compute-host",
        "__gateway-restart-watch",
        "__update-gateway-helper",
    ):
        assert name in hermes_main._BUILTIN_SUBCOMMANDS, (
            f"{name!r} must be in _BUILTIN_SUBCOMMANDS so the plugin-discovery "
            "fast path does not run for it"
        )


def test_slash_worker_subcommand_parses_args(subparsers):
    p = subparsers.choices["__slash-worker"]
    args = p.parse_args(["--session-key", "k1", "--model", "m1"])
    assert args.session_key == "k1"
    assert args.model == "m1"


def test_gateway_restart_watch_parses_remainder(subparsers):
    p = subparsers.choices["__gateway-restart-watch"]
    args = p.parse_args(["--deadline", "5", "1234", "--", "gateway", "run"])
    assert args.pid == "1234"
    assert args.deadline == "5"
    assert args.cmd == ["gateway", "run"]


def test_update_gateway_helper_parses_remainder(subparsers):
    p = subparsers.choices["__update-gateway-helper"]
    args = p.parse_args(
        ["/tmp/out.txt", "/tmp/exit.txt", "--", "hermes", "update", "--gateway"]
    )
    assert args.output_path == "/tmp/out.txt"
    assert args.exit_code_path == "/tmp/exit.txt"
    assert args.cmd == ["hermes", "update", "--gateway"]
