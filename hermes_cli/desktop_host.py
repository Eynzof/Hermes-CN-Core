#!/usr/bin/env python3
"""Desktop-only entry point for the managed Dashboard process.

The Windows release packages this module with PyInstaller's ``--windowed``
bootloader so starting the Dashboard does not create a console process.  It is
deliberately narrower than the public ``hermes`` CLI: only the host and port
needed by the Desktop shell are accepted, and the existing Dashboard command
implementation remains the single source of runtime behaviour.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any


DashboardEntrypoint = Callable[[argparse.Namespace], Any]


def _ensure_output_streams() -> None:
    """Give a windowed PyInstaller process writable stdout/stderr streams."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-core-host",
        description="Run the Hermes managed Dashboard for Hermes Desktop.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9120)
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--smoke-check",
        action="store_true",
        help="Verify that the embedded Dashboard entry point can be imported.",
    )
    return parser


def _load_dashboard_entrypoint() -> DashboardEntrypoint:
    from hermes_cli.main import cmd_dashboard

    return cmd_dashboard


def _dashboard_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        status=False,
        stop=False,
        headless_backend=False,
        host=args.host,
        port=args.port,
        no_open=True,
        isolated=True,
        open_profile="",
        insecure=False,
        skip_build=False,
    )


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dashboard_entrypoint: DashboardEntrypoint | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    entrypoint = dashboard_entrypoint or _load_dashboard_entrypoint()
    if args.smoke_check:
        return 0

    env = os.environ if environ is None else environ
    if env.get("HERMES_DESKTOP") != "1" or env.get("HERMES_DESKTOP_MANAGED") != "1":
        raise RuntimeError(
            "hermes-core-host may only be started by Hermes Desktop's managed runtime"
        )

    entrypoint(_dashboard_args(args))
    return 0


def main() -> int:
    _ensure_output_streams()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
