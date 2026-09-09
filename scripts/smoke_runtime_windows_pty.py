"""Launch the bundled Node through the frozen runtime's real Windows PTY."""
import sys
from pathlib import Path

from hermes_cli.win_pty_bridge import WinPtyBridge

assert sys.platform == "win32" and getattr(sys, "frozen", False)
root = Path(sys.executable).parent
bridge = WinPtyBridge.spawn([
    str(root / "node" / "node.exe"), "-e",
    'console.log("HERMES_PTY_OK"); setTimeout(() => process.exit(0), 1000)',
], cwd=str(root))
output = b""
try:
    while True:
        chunk = bridge.read(timeout=0.2)
        if chunk is None:
            break
        output += chunk
    assert b"HERMES_PTY_OK" in output, repr(output)
    print("Frozen Windows Node PTY OK")
finally:
    bridge.close()
