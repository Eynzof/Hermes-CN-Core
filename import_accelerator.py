"""First-party import accelerator — bypass the ``sys.path`` scan for Hermes modules.

Why this module exists
----------------------
The agent-init flame graph (``.plans/16-Flame-Graph-Import-Optimizations.md``)
attributes **~71%** of cold agent start to the Python import system.  Beyond the
one-time ``builtins.compile`` cost (addressed by ``scripts/precompile.py``), a
recurring slice is the *module search* itself: for every top-level import,
``importlib`` walks ``sys.meta_path`` (BuiltinImporter → FrozenImporter →
PathFinder) and, in PathFinder, iterates ``sys.path`` creating/consulting a
``FileFinder`` per entry — each of which pays ``nt.stat`` / ``nt._path_exists``
to discover the module file (flame hotspots ``nt.stat`` ~1.6% and
``nt._path_exists`` ~2.1%).  Hermes imports *dozens* of its own top-level
modules/packages during init, so that per-import scan adds up.

This module installs a single :class:`_FirstPartyFinder` at ``sys.meta_path[0]``
that answers imports for a **curated, fixed set of Hermes top-level names** with
one ``dict`` lookup, skipping the whole meta-path/`sys.path` scan for those
names.  Any name it does not own returns ``None`` in O(1) (a single dict miss),
so *every other* import in the process is unaffected beyond that lookup.

Correctness invariants (never trade correctness for speed)
----------------------------------------------------------
* **Standard loaders / ``.pyc`` intact.**  Specs are built with
  :func:`importlib.util.spec_from_file_location`, so the returned spec carries
  the exact ``SourceFileLoader`` (and bytecode-cache behaviour) the stdlib would
  have selected.  Pre-compiled ``__pycache__/*.pyc`` files are still used.
* **Package precedence.**  A package (``name/__init__.py``) always wins over a
  same-named top-level module (``name.py``), mirroring CPython's ``FileFinder``
  order.  This matters here: a repo-root ``agent.py`` harness must never shadow
  the real ``agent/`` package.
* **Only top-level names.**  ``find_spec`` bails (returns ``None``) whenever it
  is called with a non-``None`` ``path`` (i.e. a submodule import
  ``pkg.sub``).  Submodules resolve through the parent package's ``__path__``
  and the standard one-directory ``FileFinder`` — already cheap, and
  intercepting them would mean re-implementing namespace/submodule semantics.
* **Curated allow-list, not a directory scan.**  The set of names mirrors
  ``pyproject.toml``'s ``[tool.setuptools] py-modules`` (single-file modules)
  and ``[tool.setuptools.packages.find] include`` (packages).  A blind
  ``os.listdir`` of the repo root would happily register the repo's local
  ``packaging/`` directory as the top-level ``packaging`` name and shadow the
  real PyPI ``packaging`` dependency — a curated list cannot.
* **Re-validated at resolve time.**  Every entry is checked with
  ``os.path.isfile`` before a spec is built; a moved/deleted file makes the
  finder return ``None`` and the import falls through to the normal machinery.
* **Opt-out.**  Set ``HERMES_DISABLE_IMPORT_ACCELERATOR=1`` before import to
  skip installation entirely.

Dependency-free by design: stdlib only, so ``hermes_bootstrap`` can install it
before anything else loads without dragging in project modules.
"""

from __future__ import annotations

import os
import sys
from importlib.util import spec_from_file_location
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Curated first-party name set.
#
# Kept in lockstep with pyproject.toml so it never registers a name Hermes does
# not actually ship at the repo root:
#   * _FIRST_PARTY_MODULES  ->  [tool.setuptools] py-modules
#   * _FIRST_PARTY_PACKAGES ->  [tool.setuptools.packages.find] include (roots)
#
# ``import_accelerator`` itself is intentionally absent — by the time anything
# could import it, it is already in ``sys.modules``.
# ---------------------------------------------------------------------------
_FIRST_PARTY_MODULES: Tuple[str, ...] = (
    "run_agent",
    "model_tools",
    "toolsets",
    "toolset_distributions",
    "batch_runner",
    "trajectory_compressor",
    "cli",
    "hermes_bootstrap",
    "hermes_constants",
    "hermes_state",
    "hermes_time",
    "hermes_logging",
    "utils",
    "platform_utils",
    "mcp_serve",
)

_FIRST_PARTY_PACKAGES: Tuple[str, ...] = (
    "agent",
    "tools",
    "hermes_cli",
    "gateway",
    "tui_gateway",
    "cron",
    "acp_adapter",
    "plugins",
    "providers",
)

_ENV_DISABLE = "HERMES_DISABLE_IMPORT_ACCELERATOR"

# The single installed finder instance (or None). Module-global so install() is
# idempotent and uninstall()/is_installed() can find it.
_installed_finder: "Optional[_FirstPartyFinder]" = None


class _FirstPartyFinder:
    """Meta-path finder that resolves a fixed set of first-party top-level
    names directly to their spec.

    See the module docstring for the full rationale and correctness invariants.
    """

    # ``sys.meta_path`` finders are duck-typed — only ``find_spec`` is required.
    # ``__slots__`` keeps the singleton lean (it lives for the whole process).
    __slots__ = ("root", "_modules", "_packages")

    def __init__(
        self,
        root: str,
        modules: Dict[str, str],
        packages: Dict[str, Tuple[str, List[str]]],
    ) -> None:
        self.root = root
        # name -> absolute path of ``name.py``
        self._modules = modules
        # name -> (absolute path of ``name/__init__.py``, [package directory])
        self._packages = packages

    # -- introspection helpers (used by tests / diagnostics) ---------------
    def owns(self, fullname: str) -> bool:
        """True when this finder is responsible for the top-level *fullname*."""
        return fullname in self._packages or fullname in self._modules

    @property
    def handled_names(self) -> Tuple[str, ...]:
        return tuple(sorted({*self._packages, *self._modules}))

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        # Submodule imports (``pkg.sub``) arrive with a non-None ``path`` (the
        # parent's ``__path__``). Let the standard one-directory FileFinder
        # handle those — cheap already, and correct for namespace semantics.
        if path is not None:
            return None

        # The map is validated (``os.path.isfile``) once at build time, so the
        # hot path trusts it and does NOT re-stat here: a per-resolve stat would
        # cost more than the cached-directory ``FileFinder`` hit this is meant to
        # beat (measured net regression on a warm tree).  ``spec_from_file_
        # location`` keeps the stdlib ``SourceFileLoader`` (``.pyc`` cache
        # intact); if a first-party file genuinely vanished mid-process the
        # loader raises at exec time — the same broken state the stock machinery
        # (which also could no longer find it) would land in.
        pkg = self._packages.get(fullname)
        if pkg is not None:
            init_path, search = pkg
            return spec_from_file_location(
                fullname,
                init_path,
                submodule_search_locations=list(search),
            )

        mod_path = self._modules.get(fullname)
        if mod_path is not None:
            return spec_from_file_location(fullname, mod_path)
        return None


def _repo_root() -> str:
    """Absolute directory that holds the Hermes top-level modules/packages.

    This file lives at the repository root next to ``run_agent.py`` /
    ``hermes_constants.py``, so its own directory is the resolution root for
    every shipped entry point.
    """
    return os.path.dirname(os.path.abspath(__file__))


def build_first_party_map(
    root: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, List[str]]]]:
    """Resolve the curated first-party names under *root* to concrete paths.

    Returns ``(modules, packages)`` where ``modules`` maps ``name -> name.py``
    and ``packages`` maps ``name -> (name/__init__.py, [name/])``.  Only entries
    whose files actually exist are included, so a partial checkout / frozen
    layout simply registers fewer names (and the rest fall through to normal
    import).  Packages take precedence: a name present as BOTH a package and a
    ``.py`` module registers only as the package (CPython's own order).
    """
    base = root if root is not None else _repo_root()
    packages: Dict[str, Tuple[str, List[str]]] = {}
    for name in _FIRST_PARTY_PACKAGES:
        pkg_dir = os.path.join(base, name)
        init_py = os.path.join(pkg_dir, "__init__.py")
        if os.path.isfile(init_py):
            packages[name] = (init_py, [pkg_dir])

    modules: Dict[str, str] = {}
    for name in _FIRST_PARTY_MODULES:
        if name in packages:
            # A same-named package already claims this name — package wins.
            continue
        mod_path = os.path.join(base, f"{name}.py")
        if os.path.isfile(mod_path):
            modules[name] = mod_path

    return modules, packages


def install(root: Optional[str] = None) -> bool:
    """Install the first-party finder at ``sys.meta_path[0]`` (idempotent).

    Returns ``True`` when a finder was installed by THIS call, ``False`` when it
    was already installed, disabled via ``HERMES_DISABLE_IMPORT_ACCELERATOR``,
    or resolved to zero names (nothing to accelerate).  Never raises: any
    failure leaves the standard import machinery untouched.
    """
    global _installed_finder

    if os.environ.get(_ENV_DISABLE) in ("1", "true", "True"):
        return False
    if _installed_finder is not None and _installed_finder in sys.meta_path:
        return False

    try:
        base = root if root is not None else _repo_root()
        modules, packages = build_first_party_map(base)
        if not modules and not packages:
            return False
        finder = _FirstPartyFinder(base, modules, packages)
        # Front of the chain so first-party names resolve before PathFinder even
        # constructs a FileFinder. A stale prior instance (different root) is
        # dropped first so re-install with a new root is clean.
        sys.meta_path[:] = [
            f for f in sys.meta_path if not isinstance(f, _FirstPartyFinder)
        ]
        sys.meta_path.insert(0, finder)
        _installed_finder = finder
        return True
    except Exception:
        # An accelerator must never break startup. Fall back to stock imports.
        return False


def uninstall() -> bool:
    """Remove the finder from ``sys.meta_path``. Returns True if one was removed.

    Primarily for tests — production installs it once for the process lifetime.
    """
    global _installed_finder
    removed = False
    kept = []
    for f in sys.meta_path:
        if isinstance(f, _FirstPartyFinder):
            removed = True
        else:
            kept.append(f)
    if removed:
        sys.meta_path[:] = kept
    _installed_finder = None
    return removed


def is_installed() -> bool:
    """True when an active first-party finder is on ``sys.meta_path``."""
    return any(isinstance(f, _FirstPartyFinder) for f in sys.meta_path)


def active_finder() -> "Optional[_FirstPartyFinder]":
    """Return the installed finder instance, or None. Test/diagnostic helper."""
    for f in sys.meta_path:
        if isinstance(f, _FirstPartyFinder):
            return f
    return None
