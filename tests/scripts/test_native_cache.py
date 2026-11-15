"""Native cache aliases survive real uv builds and disappear on every exit."""
from __future__ import annotations

from contextlib import nullcontext
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading

import pytest

from scripts.bundles.native_cache import native_wheel_cache


@pytest.mark.platforms("not windows")
def test_native_cache_keeps_the_original_path_off_windows(tmp_path):
    cache = tmp_path / "cache"
    with native_wheel_cache(cache) as selected:
        assert selected == cache
        assert not cache.exists()


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("abort", [False, True])
def test_windows_cache_alias_builds_and_replays_after_removal(tmp_path, abort):
    from hermes_platform.resolver import locate_command
    from scripts.bundles.native import stage_uv_cache
    from tests.scripts.test_bundle_native import SELF_BUILD_BACKEND

    uv = locate_command("uv").command
    assert uv, "the native build lane must provide uv"
    package = tmp_path / "package"
    package.mkdir()
    witness = tmp_path / "backend-cwd.txt"
    (package / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="selfbuild"\nbackend-path=["."]\n',
        encoding="utf-8",
    )
    (package / "selfbuild.py").write_text(
        SELF_BUILD_BACKEND + f"\nfrom pathlib import Path\nPath({str(witness)!r}).write_text(os.getcwd(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    archive = tmp_path / "cache_proof-1.0.0.tar.gz"
    with tarfile.open(archive, "w:gz") as source:
        source.add(package, arcname="cache_proof-1.0.0")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/{archive.name}"
    cache = tmp_path / "persistent-cache"
    env = {**os.environ, "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never"}

    def run(*arguments, selected_cache=cache):
        result = subprocess.run(
            [*uv, *arguments], env={**env, "UV_CACHE_DIR": str(selected_cache)},
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    try:
        with pytest.raises(RuntimeError, match="after build") if abort else nullcontext():
            with native_wheel_cache(cache) as alias:
                assert alias.drive and alias == Path(alias.anchor)
                assert Path(run("cache", "dir", selected_cache=alias).stdout.strip()) == alias
                run("pip", "install", "--python", sys.executable, "--target", str(tmp_path / "first"),
                    "--no-deps", "--no-index", url, selected_cache=alias)
                assert Path(witness.read_text(encoding="utf-8-sig")).drive == alias.drive
                if abort:
                    raise RuntimeError("after build")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert not alias.exists()
    assert (tmp_path / "first/cache_proof/__init__.py").read_text(encoding="utf-8-sig").startswith("VALUE =")
    shipped = tmp_path / "shipped-cache"
    stage_uv_cache(cache, shipped)
    replay = run("pip", "install", "--python", sys.executable, "--target", str(tmp_path / "second"),
                 "--no-deps", "--no-index", "--offline", url, selected_cache=shipped)
    assert "Building" not in replay.stderr, replay.stderr
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import cache_proof; print(cache_proof.VALUE)",
         str(tmp_path / "second")], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and result.stdout.strip() == "installed from cached wheel", result.stderr
