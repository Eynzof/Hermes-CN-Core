"""VS Code image-support probe (agent/terminal_image.py::probe_vscode_images) over a real PTY.

The far end of the PTY plays the terminal: it answers the probe the way VS Code's xterm.js was
measured to answer with ``terminal.integrated.enableImages`` on (sixel reply + DA1) and off
(DA1 only), in the probe, drain or prompt phase, then types ``hi``. After the probe the child reads its tty and feeds
it to prompt_toolkit's parser with the CLI's input tables, as the prompt would: ``typed`` is what
would land in the user's prompt.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.platforms("posix")

_REPO = str(Path(__file__).resolve().parents[2])
_CHILD = textwrap.dedent("""
    import os, select, sys, termios, tty
    from agent import terminal_image
    from hermes_cli.pt_input_extras import install_ignored_terminal_sequences
    from prompt_toolkit.input.vt100_parser import Vt100Parser
    from prompt_toolkit.keys import Keys
    real_select = select.select
    phase = sys.argv[1]
    waits = 0
    def probe_select(readers, writers, errors, timeout):
        global waits
        waits += 1
        # Select's empty result is the timeout event. Advance to the intended
        # phase explicitly, so runner scheduling cannot move an 80 ms reply
        # beyond a 100 ms deadline and change the scenario being tested.
        if phase == "prompt" or (phase == "drain" and waits == 1):
            return [], [], []
        print("REPLY_READY", flush=True)
        return real_select(readers, writers, errors, 5)
    select.select = probe_select
    try:
        result = terminal_image.probe_vscode_images()
    finally:
        select.select = real_select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    print("PROMPT_READY", flush=True)
    leaked = b""
    while select.select([fd], [], [], 5)[0]:
        leaked += os.read(fd, 256)
        if leaked.endswith(b"hi"):
            break
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    install_ignored_terminal_sequences()
    presses = []
    parser = Vt100Parser(presses.append)
    parser.feed(leaked.decode())
    parser.flush()
    typed = "".join(p.data for p in presses if p.key is not Keys.Ignore)
    print("RESULT", result, terminal_image.supports_iterm_images(),
          terminal_image.resolve_inline_mode("auto", overlays=True), "typed=%r" % typed, flush=True)
""")

_ON = b"\x1b[?1;0;4096S\x1b[?62;4;9;22c"
_OFF = b"\x1b[?1;2c"


def _run(reply: bytes, phase: str) -> str:
    import pty

    master, slave = pty.openpty()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SSH_", "KITTY_", "ITERM_", "WEZTERM_", "KONSOLE_", "TMUX"))}
    env.update(TERM_PROGRAM="vscode", TERM="xterm-256color", PYTHONPATH=_REPO)
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, phase], stdin=slave, stdout=slave,
                            stderr=subprocess.PIPE, env=env, cwd=_REPO)
    os.close(slave)
    out = b""
    answered = False
    typed = False
    try:
        while select.select([master], [], [], 15)[0]:
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: the child closed its end
                break
            if not data:
                break
            out += data
            if not answered and b"REPLY_READY" in out:
                os.write(master, reply)
                answered = True
            if not typed and b"PROMPT_READY" in out:
                os.write(master, (reply if phase == "prompt" else b"") + b"hi")
                typed = True
    finally:
        os.close(master)
        proc.wait(timeout=15)
    assert proc.returncode == 0 and proc.stderr is not None, proc.stderr and proc.stderr.read()
    text = out.decode(errors="replace")
    return text[text.index("RESULT"):].splitlines()[0].strip() if "RESULT" in text else text


@pytest.mark.parametrize(
    ("reply", "phase", "expected"),
    [
        (_ON, "probe", "RESULT True True iterm typed='hi'"),  # enableImages on
        (_OFF, "probe", "RESULT False False unicode typed='hi'"),  # enableImages off: DA1 only
        (_ON, "drain", "RESULT True True iterm typed='hi'"),
        # Past every wait: the probe misses it, and the prompt's parser drops it.
        (_ON, "prompt", "RESULT False False unicode typed='hi'"),
        (_OFF, "prompt", "RESULT False False unicode typed='hi'"),
    ],
)
def test_vscode_probe_reads_its_reply_and_leaves_nothing_for_the_prompt(reply, phase, expected):
    assert _run(reply, phase) == expected
