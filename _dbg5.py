
import os, sys, shutil, stat, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
bin_dir = tmp / "bin"; bin_dir.mkdir()
exe = bin_dir / "docker"
exe.write_text("#!/bin/sh\ncase \"$1\" in ps) printf \'%s\' \"$PS_OUTPUT\";; *) exit 0;; esac\n", encoding="utf-8")
exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
os.environ["PATH"] = f"{bin_dir}{os.pathsep}" + os.environ.get("PATH","")
os.environ["PS_OUTPUT"] = "hermes-abc\timg\n"
print("which docker ->", shutil.which("docker"))
from tools.environments.docker import find_docker, _docker_query, _docker_executable
import tools.environments.docker as de
de._docker_executable = None
print("find_docker ->", find_docker())
