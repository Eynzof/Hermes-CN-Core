"""Invariants for what is eager vs lazy in the root ``package.json``.

The root ``package.json`` is installed by ``hermes update`` on every user,
including users who never opted into a given browser backend. Anything
listed in ``dependencies`` therefore runs its npm postinstall script for
everyone — including binary-fetching backends, on every update.

The contract:

* ``agent-browser`` is NOT eager either (upstream #43564). It is the default
  Chromium-driving backend, but it resolves lazily through npx on first use,
  so its (small) install no longer runs for every user on every update. What
  this file pins is that the lazy path stays wired: the dependency check in
  ``hermes_cli/dep_ensure.py`` must accept npx resolution.

* ``@askjo/camofox-browser`` is NOT eager. It is an explicit opt-in
  alternative browser backend, selected by the user via
  ``hermes tools`` → Browser Automation → Camofox, and only used at
  runtime when ``CAMOFOX_URL`` is set. Its postinstall fetches a ~300MB
  Firefox-fork binary, which silently blocked ``hermes update`` for
  multi-minute stretches on slow / network-restricted connections
  (notably users in China running through a VPN). The package is
  installed on demand by ``tools_config.py`` ``post_setup_key ==
  "camofox"`` when the user actually selects Camofox.

If a future PR re-adds Camofox (or any other binary-postinstall package)
to root ``dependencies``, this test fails — read the lazy-install
guidance in the ``hermes-agent-dev`` skill before changing the
expectations.
"""
from __future__ import annotations


import orjson
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _root_package_json() -> dict:
    with (REPO_ROOT / "package.json").open("r", encoding="utf-8", errors="replace") as fh:
        return orjson.loads(fh.read())


def test_camofox_is_not_in_root_dependencies() -> None:
    """Camofox must be opt-in, installed lazily by its post_setup handler."""
    deps = _root_package_json().get("dependencies", {})
    assert "@askjo/camofox-browser" not in deps, (
        "Camofox is a ~300MB binary-postinstall backend that must stay "
        "out of root package.json dependencies. It belongs in the "
        "Camofox post_setup handler in hermes_cli/tools_config.py so it "
        "only installs when the user explicitly selects Camofox via "
        "`hermes tools` → Browser Automation → Camofox."
    )


def test_agent_browser_is_lazy_not_eager(monkeypatch) -> None:
    """agent-browser deliberately left root ``dependencies`` (#43564).

    It is still the default Chromium-driving backend, but it now resolves
    lazily through npx (``hermes_cli/dep_ensure.py::_has_npx_agent_browser`` →
    ``tools.browser_tool_install._find_agent_browser``) instead of being
    installed eagerly for every user by ``hermes setup`` / ``hermes update``.
    The eager root dependency was removed upstream on purpose; this test pins
    the new contract in both halves — no eager dependency, and the dependency
    check must still report the browser engine as satisfied when resolution
    falls through to npx (otherwise a default install is told it is missing).
    """
    deps = _root_package_json().get("dependencies", {})
    assert "agent-browser" not in deps, (
        "agent-browser is no longer an eager root dependency (upstream #43564). "
        "It resolves lazily via npx; re-adding it to root package.json "
        "dependencies reinstates a binary install for every user on every "
        "update. Update this test and hermes_cli/doctor_tools.py together if "
        "that decision is ever reversed."
    )

    from hermes_cli import dep_ensure
    from tools import browser_tool, browser_tool_install

    monkeypatch.setattr(
        browser_tool_install,
        "_find_agent_browser",
        lambda *a, **k: browser_tool.NPX_AGENT_BROWSER_SENTINEL,
    )
    # termux carve-out is host-dependent; the lazy-npx contract is not.
    monkeypatch.setattr(
        browser_tool_install, "_requires_real_termux_browser_install", lambda _cmd: False
    )
    assert dep_ensure._has_npx_agent_browser() is True


def test_root_lockfile_has_no_camofox_entries() -> None:
    """Regenerated lockfiles should not contain Camofox tree entries."""
    lock_path = REPO_ROOT / "package-lock.json"
    if not lock_path.exists():
        # Some CI matrix shards skip lockfile materialization.
        return
    text = lock_path.read_text(encoding="utf-8", errors="replace")
    assert "@askjo/camofox-browser" not in text, (
        "package-lock.json still references @askjo/camofox-browser. "
        "Regenerate the lockfile after removing the dep: "
        "`rm package-lock.json && npm install --package-lock-only "
        "--ignore-scripts --no-fund --no-audit`."
    )
    assert "camoufox-js" not in text, (
        "package-lock.json still references camoufox-js (transitive of "
        "@askjo/camofox-browser). Regenerate the lockfile."
    )
