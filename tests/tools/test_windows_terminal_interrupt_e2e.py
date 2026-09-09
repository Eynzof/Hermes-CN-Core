"""Native Windows regression for Desktop #464/#574; no platform emulation."""
import json
import sys
import threading
import time

import psutil
import pytest

from tools.environments.local import LocalEnvironment
from tools.interrupt import set_interrupt

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows process tree")


def test_default_terminal_runs_bare_commands_and_interrupts_descendants(tmp_path):
    terminal = LocalEnvironment(cwd=str(tmp_path))
    assert "terminal-native-ok" in terminal.execute("echo terminal-native-ok", timeout=10)["output"]
    pid_file = tmp_path / "children.json"
    script = tmp_path / "long_task.py"
    script.write_text(
        "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(pid_file)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable.replace(chr(92), "/")}" "{script.as_posix()}"'
    if terminal._shell_type in ("pwsh", "powershell"):
        command = "& " + command
    result = {}
    worker = threading.Thread(target=lambda: result.update(terminal.execute(command, timeout=60)), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_file.exists(), result
        pids = json.loads(pid_file.read_text())
        assert all(psutil.pid_exists(pid) for pid in pids)
        set_interrupt(True, thread_id=worker.ident)
        worker.join(timeout=8)
        assert not worker.is_alive(), "terminal kept waiting after interrupt"
        assert all(not psutil.pid_exists(pid) for pid in pids), "descendant survived interruption"
    finally:
        set_interrupt(False, thread_id=worker.ident)
        if pid_file.exists():
            for pid in json.loads(pid_file.read_text()):
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    pass
        terminal.cleanup()
