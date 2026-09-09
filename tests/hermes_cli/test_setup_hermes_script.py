from pathlib import Path
import os
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "setup-hermes.sh"


@pytest.mark.skipif(sys.platform == "win32", reason="`bash -n` shell-syntax validation is POSIX-only")
def test_setup_hermes_script_is_valid_shell():
    result = subprocess.run(["bash", "-n", str(SETUP_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_hermes_script_has_termux_path():
    content = SETUP_SCRIPT.read_text(encoding="utf-8", errors="replace")

    assert "is_termux()" in content
    assert ".[termux]" in content
    assert "constraints-termux.txt" in content
    assert "$PREFIX/bin" in content


@pytest.mark.skipif(sys.platform == "win32", reason="setup-hermes.sh is a POSIX shell script")
def test_setup_hermes_script_requests_python_314(tmp_path):
    script = tmp_path / "setup-hermes.sh"
    script.write_text(SETUP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    fake_python = fake_bin / "python314"
    fake_python.write_text(
        '#!/bin/sh\necho "Python 3.14.7"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_LOG"
case "$1" in
  --version)
    echo "uv 0.0.0-test"
    exit 0
    ;;
  python)
    if [ "$2" = "find" ]; then
      printf '%s\n' "$FAKE_PYTHON"
      exit 0
    fi
    ;;
  venv)
    mkdir -p "$PWD/venv/bin"
    : > "$PWD/venv/bin/python"
    chmod +x "$PWD/venv/bin/python"
    exit 0
    ;;
  sync|pip)
    exit 1
    ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HOME": str(tmp_path / "home"),
            "HERMES_HOME": str(tmp_path / "hermes-home"),
            "UV_LOG": str(uv_log),
            "FAKE_PYTHON": str(fake_python),
        }
    )
    (tmp_path / "home").mkdir()

    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert "python find 3.14" in calls
    assert "venv venv --python 3.14" in calls


@pytest.mark.skipif(sys.platform == "win32", reason="setup-hermes.sh is a POSIX shell script")
def test_setup_hermes_termux_rejects_python_313(tmp_path):
    script = tmp_path / "setup-hermes.sh"
    script.write_text(SETUP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        f"""#!{sys.executable}
import os
import sys
import types

if len(sys.argv) >= 2 and sys.argv[1] == "-c":
    real_sys = sys.modules["sys"]
    fake_sys = types.ModuleType("sys")
    fake_sys.version_info = (3, 13, 9)
    sys.modules["sys"] = fake_sys
    try:
        exec(sys.argv[2], {{}})
    finally:
        sys.modules["sys"] = real_sys
elif len(sys.argv) >= 2 and sys.argv[1] == "--version":
    print("Python 3.13.9")
else:
    raise SystemExit(17)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HOME": str(tmp_path / "home"),
            "HERMES_HOME": str(tmp_path / "hermes-home"),
            "TERMUX_VERSION": "test",
            "PREFIX": str(tmp_path / "termux-prefix"),
        }
    )
    (tmp_path / "home").mkdir()

    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Termux Python must be 3.14+" in result.stdout
