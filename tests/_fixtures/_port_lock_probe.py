"""Ask a fresh process whether a real port coordination lock is available."""

import subprocess
import sys
from pathlib import Path

from hermes_cli._subprocess_compat import windows_hide_flags

_CLAIM = """
import sys
from hermes_cli.port_lock import try_claim_port
lock = try_claim_port(int(sys.argv[1]), sys.argv[2])
print("claimed" if lock is not None else "blocked", flush=True)
if lock is not None:
    lock.release()
"""


def child_can_claim_port(home: Path, port: int) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", _CLAIM, str(port), str(home)],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
        creationflags=windows_hide_flags(),
    )
    verdict = result.stdout.strip()
    assert verdict in {"claimed", "blocked"}, result.stdout
    return verdict == "claimed"
