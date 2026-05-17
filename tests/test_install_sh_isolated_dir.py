"""Static guards for install.sh self-contained layout support."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def test_install_script_supports_isolated_dir_layout() -> None:
    text = INSTALL_SH.read_text()

    assert "--isolated-dir" in text
    assert "ISOLATED_LAYOUT=true" in text
    assert 'HERMES_HOME="$INSTALL_DIR/data"' in text
    assert "HERMES_HOME_ARG_EXPLICIT" in text
    assert "export HERMES_HOME" in text


def test_isolated_layout_keeps_common_caches_under_hermes_home() -> None:
    text = INSTALL_SH.read_text()

    assert 'export PLAYWRIGHT_BROWSERS_PATH="$HERMES_HOME/cache/ms-playwright"' in text
    assert 'export UV_CACHE_DIR="${UV_CACHE_DIR:-$HERMES_HOME/cache/uv}"' in text
    assert 'export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HERMES_HOME/cache/pip}"' in text
    assert 'export npm_config_cache="${npm_config_cache:-$HERMES_HOME/cache/npm}"' in text
    assert 'mkdir -p "$HERMES_HOME/home"' in text
