"""Central registry for all hermes-agent tools: each tool file calls ``registry.register()``
at import to declare schema, handler, toolset membership and availability check;
``model_tools.py`` queries the registry instead of keeping parallel data structures.
Cycle-safe import chain: this module imports nothing from model_tools or tool files;
tools/*.py import it at module level; model_tools.py imports both; run_agent/cli import
model_tools."""

import ast
import xxhash
import functools
import importlib
import orjson
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from hermes_constants import hermes_home_key

logger = logging.getLogger(__name__)

# Cap on a tool error body; only trims runaway interpolated exceptions (static msgs are ~115 chars).
_MAX_TOOL_ERROR_CHARS = 2048
_TOOL_ERROR_TRUNCATION_MARKER = "… [truncated]"
# Logs keep more of the body than the model sees, but still a bounded amount.
_MAX_LOGGED_ERROR_CHARS = 8192


def _bound_error_text(text: str) -> str:
    """Bound an error body destined for model context; logs keep a longer prefix."""
    if len(text) <= _MAX_TOOL_ERROR_CHARS:
        return text
    logger.debug(
        "tool error body truncated for context (%d chars): %s",
        len(text), text[:_MAX_LOGGED_ERROR_CHARS])
    return text[:_MAX_TOOL_ERROR_CHARS] + _TOOL_ERROR_TRUNCATION_MARKER


def _bound_json_error_result(result: str) -> str:
    """Trim an oversized ``error`` field in a JSON string result: handlers that
    ``json.dumps({"error": str(exc)})`` directly bypass ``tool_error``'s cap, so this runs
    at the dispatch boundary to stop unbounded errors stacking across retries."""
    if len(result) <= _MAX_TOOL_ERROR_CHARS or '"error"' not in result:
        return result
    try:
        payload = json.loads(result)
    except ValueError:
        return result
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, str) or len(error) <= _MAX_TOOL_ERROR_CHARS:
        return result
    payload["error"] = _bound_error_text(error)
    return json.dumps(payload, ensure_ascii=False)


def _is_registry_register_call(node: ast.AST) -> bool:
    """True when *node* is a ``registry.register(...)`` call expression."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute) and func.attr == "register"
        and isinstance(func.value, ast.Name) and func.value.id == "registry")


def _module_registers_tools(module_path: Path) -> bool:
    """True when the module body (or a module-level ``for``) calls ``registry.register(...)``.
    Only module-body statements count, so helpers registering inside a function are skipped;
    a text prefilter avoids ``ast.parse`` for files lacking both words."""
    try:
        source = module_path.read_text(encoding="utf-8", errors="replace")
        # Fast substring pre-filter: a module that never even mentions
        # ``registry.register`` cannot contain the call, so skip the
        # (relatively expensive) ``ast.parse`` entirely. On this tree most
        # helper modules do not self-register, so this roughly halves the
        # discovery/index scan cost.
        if "registry.register" not in source:
            return False
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False
    # Table-driven modules register several tools from one loop, still at import time.
    return any(
        _is_registry_register_call(stmt)
        or (isinstance(stmt, ast.For) and any(_is_registry_register_call(s) for s in stmt.body))
        for stmt in tree.body)


def _tool_module_candidates(tools_path: Path) -> List[Path]:
    """Flat ``tools/*.py`` modules plus the package entry point ``tools/<pkg>/tool.py``, in one
    sorted list. Only ``tool.py`` is scanned in a package, so every other file in it is a library
    by construction. Sorted after merging: ``register()`` lets a same-name, same-toolset duplicate
    overwrite silently, so the import order must not depend on file depth."""
    candidates = list(tools_path.glob("*.py")) + list(tools_path.glob("*/tool.py"))
    return sorted(candidates)


def discover_builtin_tools(tools_dir: Optional[Path] = None) -> List[str]:
    """Import built-in self-registering tool modules and return their module names. The
    per-file AST scan costs ~145 ms over ~100 files, so verdicts are memoized on disk keyed
    by ``(mtime_ns, size)``; a mismatch or corrupt cache re-scans that file. The write is
    best-effort and atomic, so concurrent processes race harmlessly."""
    tools_path = (Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent).resolve()
    cache = _load_discovery_cache()
    fresh_cache: Dict[str, list] = {}
    cache_dirty = False
    module_names: List[str] = []
    for path in _tool_module_candidates(tools_path):
        if path.name in {"__init__.py", "registry.py", "mcp_tool.py"}:
            continue
        rel_parts = path.relative_to(tools_path).with_suffix("").parts
        if len(rel_parts) > 1 and not (path.parent / "__init__.py").exists():
            # setuptools' package finder drops a directory without __init__.py, so this tool would
            # register from a checkout and vanish from an installed wheel.
            logger.warning("Skipping %s: package %s has no __init__.py", path, path.parent.name)
            continue
        abs_path = str(path.resolve())
        try:
            st = path.stat()
            stat_key = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
        cached = cache.get(abs_path)
        if isinstance(cached, (list, tuple)) and len(cached) == 3 and tuple(cached[:2]) == stat_key:
            registers = bool(cached[2])
        else:
            registers = _module_registers_tools(path)
            cache_dirty = True
        fresh_cache[abs_path] = [stat_key[0], stat_key[1], registers]
        if registers:
            module_names.append(".".join(("tools", *rel_parts)))

    # Drop entries for files that no longer exist; rewrite only when changed.
    if cache_dirty or set(fresh_cache) != set(cache):
        _save_discovery_cache(fresh_cache)
    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:
            logger.warning("Could not import tool module %s: %s", mod_name, e)
    return imported



# ---------------------------------------------------------------------------
# Lazy tool index (deferred module imports)
#
# Importing every tool module at startup is the single largest chunk of the
# ``from run_agent import AIAgent`` import cascade (~700 ms on Windows/py3.14:
# ~185 ms of AST discovery + ~340 ms of module bodies, dominated by
# ``browser_tool`` pulling in its heavy deps). Almost none of it is needed for
# a given agent, whose toolset selection usually touches a handful of modules.
#
# Instead of importing eagerly, we build a *metadata index* by statically
# parsing each tool file's top-level ``registry.register(...)`` calls and
# pulling out the literal ``name`` and ``toolset`` (module-level string
# constants are resolved too). That yields ``tool name -> module`` and
# ``toolset -> modules`` maps with **no imports**. The real module import is
# deferred until a tool from it is first requested (get_definitions / dispatch
# / get_entry) or an all-tools query runs. The index is cached on disk keyed by
# a fingerprint of the tool files, so warm process starts skip even the scan.
# ---------------------------------------------------------------------------

_TOOL_INDEX_VERSION = 2
_SKIP_TOOL_FILES = {"__init__.py", "registry.py", "mcp_tool.py"}


def _empty_tool_index() -> dict:
    return {
        "version": _TOOL_INDEX_VERSION,
        "tool_to_module": {},
        "tool_to_toolset": {},
        "module_to_tools": {},
        "toolset_to_modules": {},
        "opaque_modules": [],
        "modules": [],
    }


def _iter_tool_files(tools_path: Path) -> List[Path]:
    """Candidate tool modules: the same set ``discover_builtin_tools`` scans
    (flat ``tools/*.py`` plus package entry points ``tools/<pkg>/tool.py``), so
    a tool registered from a package can never fall out of the lazy index."""
    try:
        return _tool_module_candidates(tools_path)
    except OSError:
        return []


def _module_string_constants(tree: ast.Module) -> Dict[str, str]:
    """Collect top-level ``NAME = "literal"`` string constants so a
    ``toolset=_CONST`` reference can be resolved without importing the module.
    """
    consts: Dict[str, str] = {}
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    consts[tgt.id] = stmt.value.value
    return consts


def _module_literal_tables(tree: ast.Module) -> Dict[str, ast.AST]:
    """Top-level ``NAME = (...)``/``NAME = [...]`` literal collections.

    Lets a module-level ``for _name, … in _TOOLS:`` registration table be walked
    without executing the module (see :func:`_iter_static_loop_bindings`).
    """
    tables: Dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, (ast.Tuple, ast.List)):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    tables[tgt.id] = stmt.value
    return tables


def _iter_static_loop_bindings(loop: ast.For, tables: Dict[str, ast.AST]) -> List[Dict[str, ast.AST]]:
    """One ``{loop variable: element node}`` binding per statically-known iteration of a
    module-level ``for`` whose iterable is a literal collection (inline or a top-level
    ``NAME = (...)`` constant).

    Modules that register from a table (``for _name, _schema, … in _TOOLS:``) are common —
    the merged tree has several. Without this the lazy index cannot see a single one of
    their tools, so the whole toolset silently disappears from every schema. Bindings are
    best-effort: a non-literal element simply leaves that variable unresolvable, and the
    caller's opaque fallback still imports the module.
    """
    iterable = loop.iter
    if isinstance(iterable, ast.Name):
        iterable = tables.get(iterable.id)
    if not isinstance(iterable, (ast.Tuple, ast.List)):
        return []
    target = loop.target
    if not isinstance(target, (ast.Tuple, ast.List)):
        return [{target.id: element} for element in iterable.elts] if isinstance(target, ast.Name) else []
    elts = target.elts
    # ``*_rest`` in the target makes the row length variable: bind only the fixed
    # positions BEFORE the star (notably the tool ``name``) and leave the rest alone.
    star = next((i for i, t in enumerate(elts) if isinstance(t, ast.Starred)), len(elts))
    bindings: List[Dict[str, ast.AST]] = []
    for element in iterable.elts:
        if not isinstance(element, (ast.Tuple, ast.List)):
            continue
        bindings.append({
            elts[i].id: element.elts[i]
            for i in range(min(star, len(element.elts)))
            if isinstance(elts[i], ast.Name)
        })
    return bindings


def _module_register_calls(tree: ast.Module, tables: Dict[str, ast.AST]) -> List[tuple]:
    """Every module-level ``registry.register(...)`` call plus its static loop bindings.

    Returns ``(call_node, {loop variable: element node})`` pairs: direct calls get an empty
    binding map, and calls inside a module-level ``for`` get one entry per iteration. Calls
    nested deeper (inside a function, or a ``for`` inside an ``if``) are deliberately not
    returned — those are handled by ``_module_registers_tools``' discovery path, and a
    module-body scan must never pick up a helper's own registration.
    """
    found: List[tuple] = []
    for stmt in tree.body:
        if _is_registry_register_call(stmt):
            found.append((stmt.value, {}))
        elif isinstance(stmt, ast.For):
            for binding in _iter_static_loop_bindings(stmt, tables):
                for sub in stmt.body:
                    if _is_registry_register_call(sub):
                        found.append((sub.value, binding))
    return found


def _resolve_register_arg(node: ast.AST, consts: Dict[str, str], bound: Optional[Dict[str, ast.AST]] = None):
    """Return the string value of a ``register()`` argument node, resolving a module-level
    string constant or a statically-bound ``for`` loop variable, or None when it is not
    statically known."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if bound and node.id in bound:
            return _resolve_register_arg(bound[node.id], consts)
        return consts.get(node.id)
    return None


def _tool_module_name(tools_path: Path, path: Path) -> str:
    """Dotted module name for a candidate path (``tools/foo.py`` ->
    ``tools.foo``, ``tools/<pkg>/tool.py`` -> ``tools.<pkg>.tool``)."""
    rel = path.relative_to(tools_path).with_suffix("")
    return "tools." + ".".join(rel.parts)


def build_tool_index(tools_dir: Optional[Path] = None) -> dict:
    """Statically scan tool modules and return a lazy-import metadata index.

    Never imports the modules. A module whose ``register()`` *name* argument is
    not a static string literal is recorded in ``opaque_modules`` so callers
    can fall back to importing it when a name lookup misses (correctness is
    never sacrificed for laziness).
    """
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    index = _empty_tool_index()
    for path in _iter_tool_files(tools_path):
        if path.name in _SKIP_TOOL_FILES:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "registry.register" not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        modname = _tool_module_name(tools_path, path)
        consts = _module_string_constants(tree)
        tables = _module_literal_tables(tree)
        registers_here = False
        opaque = False
        for call, bound in _module_register_calls(tree, tables):
            registers_here = True
            name = None
            toolset = None
            for kw in call.keywords:
                if kw.arg == "name":
                    name = _resolve_register_arg(kw.value, consts, bound)
                elif kw.arg == "toolset":
                    toolset = _resolve_register_arg(kw.value, consts, bound)
            if name is None and call.args:
                name = _resolve_register_arg(call.args[0], consts, bound)
            if toolset is None and len(call.args) >= 2:
                toolset = _resolve_register_arg(call.args[1], consts, bound)
            if isinstance(name, str):
                index["tool_to_module"][name] = modname
                index["module_to_tools"].setdefault(modname, []).append(name)
                if isinstance(toolset, str):
                    index["tool_to_toolset"][name] = toolset
                    mods = index["toolset_to_modules"].setdefault(toolset, [])
                    if modname not in mods:
                        mods.append(modname)
            else:
                # Non-literal tool name (e.g. ``name=_schema["name"]`` over a table):
                # cannot map statically. Import eagerly during discovery so the tool
                # never silently disappears.
                opaque = True
                if isinstance(toolset, str):
                    # The toolset IS known, so a toolset-scoped query can still reach
                    # the module without paying for the whole opaque set.
                    mods = index["toolset_to_modules"].setdefault(toolset, [])
                    if modname not in mods:
                        mods.append(modname)
        if registers_here:
            index["modules"].append(modname)
        if opaque and modname not in index["opaque_modules"]:
            index["opaque_modules"].append(modname)
    return index


def _tool_index_fingerprint(tools_path: Path) -> Optional[str]:
    """Cheap content fingerprint of the tool tree (name + mtime + size per
    file). Any add/remove/edit of a tool file changes it, so a cached index
    can never go stale."""
    parts: List[str] = []
    for path in _iter_tool_files(tools_path):
        if path.name in _SKIP_TOOL_FILES:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
    if not parts:
        return None
    payload = "|".join(parts).encode("utf-8")
    return f"{_TOOL_INDEX_VERSION}:{xxhash.xxh64(payload).hexdigest()}"


def _tool_index_cache_file() -> Optional[Path]:
    """Return the on-disk cache path (profile-aware) or None when unavailable.

    Purely an optimization: any failure here just means the index is rebuilt
    in-memory. Set ``HERMES_DISABLE_TOOL_INDEX_CACHE`` to opt out entirely.
    """
    if os.environ.get("HERMES_DISABLE_TOOL_INDEX_CACHE"):
        return None
    try:
        from hermes_constants import get_hermes_home

        cache_dir = Path(get_hermes_home()) / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "tool_index.json"
    except Exception:
        return None


def _discovery_cache_path() -> Optional[Path]:
    """Path of the tool-discovery verdict cache, or None if unresolvable."""
    try:
        # Deferred import keeps tools/registry.py a no-deps leaf at import time.
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()) / "cache" / "tool_discovery_cache.json"
    except Exception:
        return None



def load_or_build_tool_index(tools_dir: Optional[Path] = None) -> dict:
    """Return the lazy tool index, served from the on-disk cache when the
    fingerprint matches, otherwise scanned fresh and written back.

    Never raises: on any cache/scan error it falls back to an in-memory build
    (or an empty index as the last resort).
    """
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    try:
        fingerprint = _tool_index_fingerprint(tools_path)
    except Exception:
        fingerprint = None

    cache_file = _tool_index_cache_file() if fingerprint else None
    if cache_file is not None:
        try:
            cached = orjson.loads(cache_file.read_text(encoding="utf-8", errors="replace"))
            if (
                cached.get("fingerprint") == fingerprint
                and isinstance(cached.get("index"), dict)
                and cached["index"].get("version") == _TOOL_INDEX_VERSION
            ):
                return cached["index"]
        except (OSError, ValueError, TypeError):
            pass

    try:
        index = build_tool_index(tools_path)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Tool index scan failed: %s", e)
        return _empty_tool_index()

    if cache_file is not None and fingerprint is not None:
        tmp: Optional[Path] = None
        try:
            tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
            tmp.write_text(
                orjson.dumps({"fingerprint": fingerprint, "index": index}).decode('utf-8'),
                encoding="utf-8",
            )
            os.replace(tmp, cache_file)
        except OSError:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return index


def _load_discovery_cache() -> Dict[str, list]:
    """Read the discovery cache; any error → empty dict (full scan)."""
    path = _discovery_cache_path()
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_discovery_cache(cache: Dict[str, list]) -> None:
    """Best-effort atomic write of the discovery cache. Never raises."""
    path = _discovery_cache_path()
    if path is None:
        return
    try:
        from utils import atomic_json_write  # stdlib+yaml only; no cycle
        from hermes_constants import mkdir_under_hermes_home
        mkdir_under_hermes_home(path.parent)
        atomic_json_write(path, cache, indent=0)
    except Exception as e:
        logger.debug("Could not write tool discovery cache %s: %s", path, e)


@dataclass(eq=False, slots=True)
class ToolEntry:
    """Metadata for one registered tool (identity semantics: restore/CAS paths compare ``is``)."""

    name: str
    toolset: str
    schema: dict
    handler: Callable
    check_fn: Optional[Callable]
    requires_env: list
    is_async: bool
    description: str
    emoji: str
    max_result_size_chars: int | float | None = None
    # Zero-arg callable whose dict is shallow-merged onto the schema at every get_definitions()
    # — for fields tracking runtime config (delegate_task's description reflects limits).
    dynamic_schema_overrides: Optional[Callable] = None
    # Lazily-computed cache of orjson.dumps(schema).decode('utf-8'). Populated on first
    # get_schema_json() request, never at register() time (that would add a
    # json.dumps per tool to the import cascade the lazy design avoids).
    # Bound to this entry, so a re-register() (fresh entry + generation bump)
    # invalidates it for free.
    _schema_json: Optional[str] = None


class _PluginOverridePolicy:
    """Identity-bearing authorization record for one plugin generation."""

    __slots__ = ("allowed",)

    def __init__(self, allowed: bool) -> None:
        self.allowed = bool(allowed)


_OVERRIDE_DENIED_MSG = (
    "Plugin module {owner!r} cannot override built-in tool {name!r} "
    "without operator opt-in (allow_tool_override).")


# ---- check_fn TTL cache ----------------------------------------------------
# check_fns probe external state (Docker, Modal SDK, playwright) that changes on human
# timescales, so results are cached ~30 s: env-var flips via ``hermes tools`` still land
# within a turn or two. Transient-failure suppression: a flapping probe (``docker version``
# timing out under load) would silently strip a whole toolset from the agent being built —
# most visibly a subagent reporting "Tool read_file does not exist" — so a failure within a
# short grace window of the last success serves the last-good True WITHOUT caching it; a
# failure persisting past the window is honored so a dead backend stops advertising tools.

_CHECK_FN_TTL_SECONDS = 30.0
# Grace window after a success in which a failure counts as a flake; kept short
# so a genuinely-down backend is reflected within a couple of turns.
_CHECK_FN_FAILURE_GRACE_SECONDS = 60.0
_CHECK_FN_CACHE_MAX = 512
_check_fn_cache: Dict[tuple[Callable, Optional[str]], tuple[float, bool]] = {}
_check_fn_last_good: Dict[tuple[Callable, Optional[str]], float] = {}
_check_fn_ever_good: Set[tuple[Callable, Optional[str]]] = set()  # probes that admitted tools this process
_check_fn_core_drop_warned: Set[tuple[Callable, Optional[str]]] = set()  # once-per-process WARNING gate
_check_fn_cache_lock = threading.Lock()
CHECK_FN_CACHE_BYPASS = ""
_NO_CACHE_CHECK_FNS: Set[Callable] = set()
_BROWSER_IDENTITY_KEYS = (
    "HERMES_SESSION_ID",
    "HERMES_BROWSER_CONTROL_PRINCIPAL",
    "HERMES_BROWSER_CONTROL_TRANSPORT_FAMILY")


def no_cache_check_fn(fn: Callable) -> Callable:
    """Mark a local, config-backed availability check as uncached."""
    _NO_CACHE_CHECK_FNS.add(fn)
    return fn


def _fn_label(fn: Callable) -> object:
    return getattr(fn, "__qualname__", fn)


def _prune_check_fn_caches(now: float) -> None:
    """Expire stale entries and cap profile-dimensional cache growth. Caller holds the lock."""
    for cache, ttl, stamp in (
        (_check_fn_cache, _CHECK_FN_TTL_SECONDS, lambda v: v[0]),
        (_check_fn_last_good, _CHECK_FN_FAILURE_GRACE_SECONDS, lambda v: v)):
        for key, value in list(cache.items()):
            if now - stamp(value) >= ttl:
                cache.pop(key, None)
        while len(cache) >= _CHECK_FN_CACHE_MAX:
            cache.pop(next(iter(cache)))


def check_fn_cache_scope() -> Optional[str]:
    """Return the active profile key when availability is profile-scoped. Browser-controller
    availability is request-bound (changes on every attach/detach), so a fully bound
    browser-control request bypasses this cache AND model_tools' outer definition cache (same
    sentinel) — one Browser session's live tools must not leak into another. Single-profile
    processes keep the process-wide cache; a multiplex gateway installs a Hermes-home override
    per profile turn, so the canonical profile key is the boundary."""
    try:
        from gateway.session_context import get_session_env
        if all(str(get_session_env(k, "") or "").strip() for k in _BROWSER_IDENTITY_KEYS):
            # api_server binds a server-derived principal + transport family on EVERY request, so
            # identity-present != controller-attached; only bypass when the extension-control
            # feature is actually on (#79047).
            from gateway.browser_control_broker import browser_control_enabled
            if browser_control_enabled():
                return CHECK_FN_CACHE_BYPASS
    except Exception:
        pass
    try:
        from agent.secret_scope import serves_routed_profile
        from hermes_constants import get_hermes_home_override
        if not serves_routed_profile():
            return None
        override = get_hermes_home_override()
        return str(Path(override).expanduser().resolve()) if override else CHECK_FN_CACHE_BYPASS
    except Exception:
        # Fail closed: bypass both cache layers rather than aliasing requests
        # whose multiplex profile identity could not be resolved.
        return CHECK_FN_CACHE_BYPASS


def _run_check_fn_uncached(fn: Callable) -> bool:
    """Run an availability check without cache/grace handling."""
    from agent.secret_scope import UnscopedSecretError, current_secret_scope
    try:
        return bool(fn())
    except UnscopedSecretError:
        # The verdict comes from the LIVE scope at the catch site, not from which registry branch
        # ran the probe: ``no_cache_check_fn`` probes skip the cache-scope lookup entirely, so a
        # branch-derived hint misreported every boot-time uncached probe as a lost scope (#110635).
        if current_secret_scope() is None:
            # Expected fail-closed probe: with multiplexing on, boot-time check_fns run before any
            # profile secret scope exists, so get_secret raises by design. No traceback, so this
            # cannot be mistaken for a crashed check_fn (#100697).
            logger.debug(
                "check_fn %s hit the multiplex fail-closed path with no "
                "profile secret scope active; dependent tools re-probe on the first scoped turn",
                _fn_label(fn))
        else:
            # The caller IS scoped but the read still failed closed: the probe dropped the scope on
            # the way to get_secret (a bare thread/executor hop) — a spawn-site bug, kept loud.
            logger.warning(
                "check_fn %s raised UnscopedSecretError while the profile cache "
                "scope was resolved; dependent tools will be unavailable this turn",
                _fn_label(fn), exc_info=True)
    except Exception:
        logger.warning(
            "check_fn %s raised; dependent tools will be unavailable this turn",
            _fn_label(fn), exc_info=True)
    return False


def _check_fn_cached(fn: Callable) -> bool:
    """Return bool(fn()), TTL-cached across calls."""
    now = time.monotonic()
    if fn in _NO_CACHE_CHECK_FNS:
        return _run_check_fn_uncached(fn)
    scope = check_fn_cache_scope()
    if scope == CHECK_FN_CACHE_BYPASS:
        return _run_check_fn_uncached(fn)
    cache_key = (fn, scope)
    with _check_fn_cache_lock:
        _prune_check_fn_caches(now)  # leaves only entries within TTL
        cached = _check_fn_cache.get(cache_key)
        if cached is not None:
            return cached[1]
    exc_info = None
    try:
        value, outcome = bool(fn()), "returned False"
    except Exception as exc:
        # Keep the exception for the verdict log below (emitted outside this block, where
        # ``exc_info=True`` would resolve to nothing): a check_fn that raises is a bug in the probe
        # or its resolver, and a bare "raised" verdict reads as "nothing configured" (#87950).
        value, outcome, exc_info = False, "raised", exc
    # Resolved outside the cache lock: the registry snapshot takes its own lock.
    core_dropped = sorted(_core_tools_gated_by(fn)) if not value else []
    with _check_fn_cache_lock:
        _prune_check_fn_caches(now)
        if value:
            _check_fn_last_good[cache_key] = now
            _check_fn_ever_good.add(cache_key)
            _check_fn_cache[cache_key] = (now, True)
            return True
        last_good = _check_fn_last_good.get(cache_key)
        if last_good is not None and now - last_good < _CHECK_FN_FAILURE_GRACE_SECONDS:
            # Recent success → flake: serve last-good True, do NOT cache (next call re-probes).
            logger.warning(
                "check_fn %s failed (%s) within %.0fs of last success; "
                "treating as transient and keeping tool(s) available",
                _fn_label(fn), outcome, _CHECK_FN_FAILURE_GRACE_SECONDS)
            return True

        # No recent success (or grace expired) — honor the failure. A False verdict is the
        # expected state for optional, unconfigured toolsets; only a raised probe is actionable.
        # Core (non-deferrable) tools are the exception when they were available earlier in
        # this process and the probe now fails: dropped, they leave neither the schema nor the
        # tool_search catalog, so the model's "no such tool" is accurate and nothing points at
        # the probe. That regression is a WARNING once per probe per process (#112649). A core
        # tool whose probe never succeeded (browser, image_gen, HA unconfigured on a stock home)
        # is the expected state and keeps the INFO verdict.
        if core_dropped and cache_key in _check_fn_ever_good and cache_key not in _check_fn_core_drop_warned:
            _check_fn_core_drop_warned.add(cache_key)
            logger.warning(
                "check_fn %s %s; previously available core tool(s) %s dropped (non-deferrable, "
                "so not searchable either); dependent tools will be unavailable this turn",
                _fn_label(fn), outcome, ", ".join(core_dropped), exc_info=exc_info)
        else:
            log = logger.warning if exc_info else logger.info
            log(
                "check_fn %s %s; dependent tools will be unavailable this turn", _fn_label(fn), outcome,
                exc_info=exc_info)
        _check_fn_cache[cache_key] = (now, False)
        return False


def _core_tools_gated_by(fn: Callable) -> Set[str]:
    """Names of ``_HERMES_CORE_TOOLS`` members whose registered ``check_fn`` is *fn*."""
    try:
        from toolsets import _HERMES_CORE_TOOLS
        core = frozenset(_HERMES_CORE_TOOLS)
    except Exception:
        return set()
    return {e.name for e in registry._snapshot_entries() if e.check_fn is fn and e.name in core}


def _memo_check(fn: Callable, memo: Dict[Callable, bool]) -> bool:
    """Per-pass memo on top of the TTL cache: one probe per distinct check_fn."""
    if fn not in memo:
        memo[fn] = _check_fn_cached(fn)
    return memo[fn]


def invalidate_check_fn_cache() -> None:
    """Drop all cached ``check_fn`` results (after config changes like ``hermes tools enable``)."""
    with _check_fn_cache_lock:
        _check_fn_cache.clear()
        _check_fn_last_good.clear()
        _check_fn_ever_good.clear()
        _check_fn_core_drop_warned.clear()


def get_cached_check_fn_result(fn: Callable) -> Optional[bool]:
    """Cached verdict for *fn* if its TTL is still valid, else None. NEVER runs the probe:
    for read-only surfaces (dashboard panels) that must not do network/auth/SDK work."""
    now = time.monotonic()
    scope = check_fn_cache_scope()
    if scope == CHECK_FN_CACHE_BYPASS:
        # Unresolved profile identity bypasses the cache; nothing trustworthy to report.
        return None
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get((fn, scope))
    return cached[1] if cached is not None and now - cached[0] < _CHECK_FN_TTL_SECONDS else None


class ToolRegistry:
    """Singleton registry that collects tool schemas + handlers from tool files."""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}  # built-in / process-global registrations
        # Plugin overlays keyed by resolved HERMES_HOME; a profile sees its overlay first.
        self._scoped_tools: Dict[str, Dict[str, ToolEntry]] = {}
        # Plugin namespace -> operator opt-in for built-in override (lifecycle-managed);
        # scope attribution stays durable after policy removal so delayed callbacks
        # remain confined to the profile that loaded them.
        self._plugin_override_policy: Dict[tuple[Optional[str], str], _PluginOverridePolicy] = {}
        self._plugin_module_scopes: Dict[str, Set[Optional[str]]] = {}
        self._toolset_checks: Dict[str, Callable] = {}
        self._toolset_aliases: Dict[str, str] = {}
        # MCP refresh mutates while other threads read: serialize writes, snapshot reads.
        self._lock = threading.RLock()
        # Bumped on every mutation; get_tool_definitions memoizes against it.
        self._generation: int = 0

        # ── Lazy built-in discovery state ────────────────────────────────
        # Disabled by default so a bare ``ToolRegistry()`` (used throughout
        # the tests) behaves exactly as before — it only knows about tools
        # explicitly registered on it. The process-wide singleton opts in via
        # ``enable_lazy_builtins()`` (called from model_tools at import), which
        # merely records the tools dir; the actual AST scan + module imports
        # are deferred until the first query that needs them.
        self._lazy_enabled: bool = False
        self._lazy_builtins_dir: Optional[Path] = None
        self._lazy_index: Optional[dict] = None
        self._lazy_loaded_modules: Set[str] = set()
        self._lazy_all_loaded: bool = False
        self._lazy_opaque_loaded: bool = False
        # Orchestrates lazy imports without holding self._lock across arbitrary
        # module-body code (which itself re-enters register() -> self._lock).
        self._lazy_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lazy built-in discovery
    # ------------------------------------------------------------------

    def enable_lazy_builtins(self, tools_dir: Optional[Path] = None) -> None:
        """Opt this registry into deferred built-in tool discovery.

        Cheap: records the tools directory and flips a flag. The metadata
        index is scanned lazily on first use and individual tool modules are
        imported on demand. Called once on the module-level ``registry``
        singleton by ``model_tools`` at import time.
        """
        with self._lazy_lock:
            self._lazy_builtins_dir = (
                Path(tools_dir) if tools_dir is not None
                else Path(__file__).resolve().parent
            )
            self._lazy_enabled = True

    def _ensure_index(self) -> dict:
        """Build/load the lazy metadata index once. Returns an (empty when
        disabled) index dict."""
        if not self._lazy_enabled:
            return _empty_tool_index()
        index = self._lazy_index
        if index is not None:
            return index
        with self._lazy_lock:
            if self._lazy_index is None:
                try:
                    self._lazy_index = load_or_build_tool_index(self._lazy_builtins_dir)
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning("Lazy tool index unavailable: %s", e)
                    self._lazy_index = _empty_tool_index()
            return self._lazy_index

    def _lazy_import_module(self, modname: str) -> None:
        """Import a tool module exactly once. Import runs OUTSIDE self._lock so
        the register() calls it triggers acquire the lock independently."""
        with self._lazy_lock:
            if modname in self._lazy_loaded_modules:
                return
            # Mark before importing so a re-entrant lookup during the import
            # can't kick off a second attempt.
            self._lazy_loaded_modules.add(modname)
        try:
            importlib.import_module(modname)
        except Exception as e:
            logger.warning("Lazy import of tool module %s failed: %s", modname, e)

    def _load_opaque_modules(self) -> None:
        """Import modules whose tool names couldn't be statically indexed.
        No-op for the built-in tree (every tool registers a literal name)."""
        if self._lazy_opaque_loaded:
            return
        index = self._ensure_index()
        for modname in index.get("opaque_modules", ()):
            self._lazy_import_module(modname)
        self._lazy_opaque_loaded = True

    def _ensure_tool_loaded(self, name: str) -> None:
        """Import the module that provides tool ``name`` if not already live."""
        if not self._lazy_enabled or name in self._tools:
            return
        index = self._ensure_index()
        modname = index["tool_to_module"].get(name)
        if modname is not None:
            self._lazy_import_module(modname)
        if name not in self._tools and index.get("opaque_modules"):
            self._load_opaque_modules()

    def _ensure_toolset_loaded(self, toolset: str) -> None:
        """Import every module that contributes a tool to ``toolset``."""
        if not self._lazy_enabled:
            return
        index = self._ensure_index()
        for modname in index["toolset_to_modules"].get(toolset, ()):
            self._lazy_import_module(modname)
        if index.get("opaque_modules"):
            self._load_opaque_modules()

    def _ensure_all_loaded(self) -> None:
        """Import every self-registering built-in tool module. Used by
        aggregate queries that must see the whole catalog (parity with the
        old eager discovery). Runs at most once."""
        if not self._lazy_enabled or self._lazy_all_loaded:
            return
        index = self._ensure_index()
        for modname in index["modules"]:
            self._lazy_import_module(modname)
        self._load_opaque_modules()
        self._lazy_all_loaded = True

    def _lazy_tool_names(self) -> Set[str]:
        """All statically-known built-in tool names (no imports)."""
        if not self._lazy_enabled:
            return set()
        return set(self._ensure_index()["tool_to_module"].keys())

    @staticmethod
    def current_scope_key() -> str:
        return hermes_home_key()

    @staticmethod
    def _grouped(entries: List[ToolEntry]) -> Dict[str, List[ToolEntry]]:
        """``{toolset: entries}`` in first-appearance order."""
        groups: Dict[str, List[ToolEntry]] = {}
        for entry in entries:
            groups.setdefault(entry.toolset, []).append(entry)
        return groups

    def _slot(self, scope: Optional[str], *, create: bool = False) -> Dict[str, ToolEntry]:
        """The registration map for *scope*: global when None, else that profile's overlay."""
        if scope is None:
            return self._tools
        if create:
            return self._scoped_tools.setdefault(scope, {})
        return self._scoped_tools.get(scope, {})

    def _drop_toolset_aliases(self, toolset: str) -> None:
        self._toolset_aliases = {
            alias: target for alias, target in self._toolset_aliases.items() if target != toolset}

    def _merged_tools(self, scope: Optional[str] = None) -> Dict[str, ToolEntry]:
        """Return global tools overlaid with one profile's plugin tools."""
        return {**self._tools, **self._scoped_tools.get(scope or self.current_scope_key(), {})}

    def _toolset_entries(self, toolset: str, scope: Optional[str]) -> List[ToolEntry]:
        return self._grouped(self._merged_tools(scope).values()).get(toolset, [])

    def _snapshot_state(
        self, scope: Optional[str] = None) -> tuple[List[ToolEntry], Dict[str, Callable]]:
        """Return a coherent snapshot of registry entries and toolset checks."""
        with self._lock:
            entries = list(self._merged_tools(scope).values())
            checks = dict(self._toolset_checks)
            checks.update({e.toolset: e.check_fn for e in entries if e.check_fn is not None})
            return entries, checks

    def _snapshot_entries(self) -> List[ToolEntry]:
        return self._snapshot_state()[0]

    def _toolset_has_exposable_tools(self, toolset: str, entries: List[ToolEntry]) -> bool:
        """True when at least one tool in *toolset* would be exposed. Mirrors
        :meth:`get_definitions` per-tool filtering so doctor/banners agree with runtime:
        mixed toolsets (``terminal`` + desktop-only ``read_terminal``) must not be gated
        by the first ``check_fn``."""
        memo: Dict[Callable, bool] = {}
        members = (e for e in entries if e.toolset == toolset)
        return any(not e.check_fn or _memo_check(e.check_fn, memo) for e in members)

    def get_entry(self, name: str, *, scope: Optional[str] = None) -> Optional[ToolEntry]:
        """Active profile's entry by name, falling back to global."""
        # Lazily import the module providing this tool before lookup — the
        # lazy tool index must stay lazy (get_tool_definitions cache isolation
        # and the cold-start budget depend on it).
        self._ensure_tool_loaded(name)
        with self._lock:
            return self._merged_tools(scope).get(name)

    def snapshot_registration(
        self, name: str, *, scope: Optional[str] = None) -> Optional[ToolEntry]:
        """Local slot state only — no global fallback."""
        with self._lock:
            return self._slot(scope).get(name)

    def get_registered_toolset_names(self) -> List[str]:
        self._ensure_all_loaded()
        return sorted(self._grouped(self._snapshot_entries()))

    def get_all_entries(self) -> List[ToolEntry]:
        return self._snapshot_entries()

    def get_tool_names_for_toolset(self, toolset: str) -> List[str]:
        self._ensure_toolset_loaded(toolset)
        return sorted(e.name for e in self._grouped(self._snapshot_entries()).get(toolset, []))

    def register_toolset_alias(self, alias: str, toolset: str) -> None:
        """Register an explicit alias for a canonical toolset name."""
        with self._lock:
            existing = self._toolset_aliases.get(alias)
            if existing and existing != toolset:
                logger.warning(
                    "Toolset alias collision: '%s' (%s) overwritten by %s", alias, existing, toolset
                )
            self._toolset_aliases[alias] = toolset
            self._generation += 1

    def get_registered_toolset_aliases(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._toolset_aliases)

    def get_toolset_alias_target(self, alias: str) -> Optional[str]:
        with self._lock:
            return self._toolset_aliases.get(alias)

    # ---- Registration ------------------------------------------------

    def register_plugin_override_policy(
        self, module_namespace: str, allowed: bool, *, scope: Optional[str] = None,
    ) -> _PluginOverridePolicy:
        """Bind a plugin module namespace to its current operator opt-in. The identity-bearing
        result lets unload/reload revoke a stale authorization without losing attribution."""
        with self._lock:
            policy = _PluginOverridePolicy(allowed)
            self._plugin_override_policy[(scope, module_namespace)] = policy
            self._plugin_module_scopes.setdefault(module_namespace, set()).add(scope)
            return policy

    def snapshot_plugin_override_policy(
        self, module_namespace: str, *, scope: Optional[str] = None,
    ) -> Optional[_PluginOverridePolicy]:
        """Return one local authorization generation without fallback."""
        with self._lock:
            return self._plugin_override_policy.get((scope, module_namespace))

    def restore_plugin_override_policy(
        self, module_namespace: str, current: _PluginOverridePolicy,
        previous: Optional[_PluginOverridePolicy], *, scope: Optional[str] = None) -> bool:
        """CAS-restore policy state while retaining durable scope attribution."""
        with self._lock:
            key = (scope, module_namespace)
            if self._plugin_override_policy.get(key) is not current:
                return False
            if previous is None:
                self._plugin_override_policy.pop(key, None)
            else:
                self._plugin_override_policy[key] = previous
            return True

    def _plugin_override_allowed(self, scope: Optional[str], module_namespace: str) -> bool:
        policy = self._plugin_override_policy.get((scope, module_namespace))
        if policy is None and scope is not None:
            policy = self._plugin_override_policy.get((None, module_namespace))
        return bool(policy and policy.allowed)

    def _plugin_owner_of(self, handler: Callable) -> Optional[str]:
        """Plugin namespace that DEFINED *handler* (None for built-in/MCP handlers). Bound to
        ``handler.__globals__["__name__"]``, fixed at definition time so it cannot drift with
        call site/thread/timing; lambdas and nested functions inherit it, so a plugin cannot
        launder an override via a callback."""
        mod = self._callable_module(handler)
        return self._plugin_namespace_of_module(mod) if mod else None

    @staticmethod
    def _callable_module(handler: Callable) -> str:
        """Resolve defining module through wrappers, partials, and objects."""
        current = handler
        seen: Set[int] = set()
        while id(current) not in seen:
            seen.add(id(current))
            globals_dict = getattr(current, "__globals__", None)
            if isinstance(current, functools.partial):
                current = current.func
            elif getattr(current, "__func__", None) is not None:
                current = current.__func__
            elif isinstance(globals_dict, dict) and globals_dict.get("__name__", ""):
                return str(globals_dict["__name__"])
            elif getattr(current, "__wrapped__", None) is not None:
                current = current.__wrapped__
            else:
                break
        module_name = getattr(current, "__module__", "")
        return str(module_name or getattr(type(current), "__module__", "") or "")

    def _plugin_namespace_of_module(self, module_namespace: str) -> Optional[str]:
        """Resolve a module/submodule to its durable plugin namespace."""
        with self._lock:
            matches = [
                namespace for namespace in self._plugin_module_scopes
                if module_namespace == namespace or module_namespace.startswith(f"{namespace}.")]
            if matches:
                return max(matches, key=len)
        # Also gate plugin modules currently loading but not yet policy-recorded
        # (defensive: a handler defined in the plugin namespace is plugin code).
        if module_namespace.startswith("hermes_plugins."):
            return ".".join(module_namespace.split(".")[:2])
        return None

    def _plugin_scope_of(self, module_namespace: str) -> Optional[str]:
        """Return the profile scope bound to a loaded plugin module."""
        with self._lock:
            scopes = self._plugin_module_scopes.get(module_namespace)
            if not scopes:
                return None
            active_scope = self.current_scope_key()
            if active_scope in scopes:
                return active_scope
            if len(scopes) == 1:
                return next(iter(scopes))
            raise PermissionError(
                f"Plugin module {module_namespace!r} is active in multiple profiles and cannot "
                "register outside one of those scopes.")

    def plugin_scope_for_module(self, module_namespace: str) -> Optional[str]:
        """Public host lookup for a loaded plugin module's immutable scope."""
        owner = self._plugin_namespace_of_module(module_namespace)
        return self._plugin_scope_of(owner or module_namespace)

    def plugin_scope_for_callable(self, callback: Callable) -> Optional[str]:
        """Return the durable plugin scope for any supported callable shape."""
        module_name = self._callable_module(callback)
        return self.plugin_scope_for_module(module_name) if module_name else None

    @staticmethod
    def _caller_module() -> str:
        """Best-effort module name of the registry method's caller (two frames up).
        ``deregister()`` takes only a tool name — no handler for ``_plugin_owner_of`` —
        so frame inspection is the only way to know who is asking."""
        try:
            return sys._getframe(2).f_globals.get("__name__", "") or ""
        except Exception:
            return ""

    def register(
        self, name: str, toolset: str, schema: dict, handler: Callable,
        check_fn: Callable = None, requires_env: list = None, is_async: bool = False,
        description: str = "", emoji: str = "", max_result_size_chars: int | float | None = None,
        dynamic_schema_overrides: Callable = None, override: bool = False,
        scope: Optional[str] = None):
        """Register a tool (called at import time by each tool file). ``override=True`` is an
        explicit opt-in for plugins replacing a built-in implementation (e.g. a headed-Chrome
        browser backend); without it, cross-toolset shadowing is rejected."""
        # Reject malformed schemas at registration, not at request time: a non-dict
        # ``parameters`` (e.g. a list) serializes into every provider request and 400s the
        # whole turn far from the offending plugin. Failing here names the culprit instead.
        if not isinstance(schema, dict):
            raise ValueError(
                f"Tool {name!r}: schema must be a dict, got {type(schema).__name__}")
        params = schema.get("parameters")
        if params is not None and not isinstance(params, dict):
            raise ValueError(
                f"Tool {name!r}: schema['parameters'] must be an object (JSON Schema dict), "
                f"got {type(params).__name__}")
        handler_owner = self._plugin_owner_of(handler)
        caller_owner = self._plugin_namespace_of_module(self._caller_module())
        owner = caller_owner or handler_owner
        if scope is None and owner is not None:
            scope = self._plugin_scope_of(owner)
        with self._lock:
            target = self._slot(scope, create=True)
            existing = (self._tools if scope is None else self._merged_tools(scope)).get(name)
            plugin_override_denied = (
                owner is not None and not self._plugin_override_allowed(scope, owner))
            shadows_global = (
                owner is not None and scope is not None
                and name not in target and name in self._tools)
            if shadows_global:
                if not override:
                    logger.error(
                        "Tool registration REJECTED: plugin %r attempted to shadow global tool %r "
                        "without override=True", owner, name)
                    return
                if plugin_override_denied:
                    raise PermissionError(_OVERRIDE_DENIED_MSG.format(owner=owner, name=name))
            if existing and existing.toolset != toolset:
                if override:
                    if plugin_override_denied:
                        logger.error(
                            "Tool registration REJECTED: plugin %r attempted to override built-in "
                            "tool %r (existing toolset %r) without operator opt-in. Set "
                            "plugins.entries.<plugin_id>.allow_tool_override: true in config.yaml "
                            "to allow it.",
                            owner, name, existing.toolset)
                        raise PermissionError(_OVERRIDE_DENIED_MSG.format(owner=owner, name=name))
                    # Explicit opt-in (or non-plugin caller): INFO so the override is auditable.
                    logger.info(
                        "Tool '%s': toolset '%s' overriding existing toolset '%s' "
                        "(override=True opt-in)", name, toolset, existing.toolset)
                else:
                    # Reject every cross-toolset shadow (incl. MCP-to-MCP); same-toolset
                    # re-registration (MCP reconnect/refresh) stays allowed.
                    logger.error(
                        "Tool registration REJECTED: '%s' (toolset '%s') would shadow existing "
                        "tool from toolset '%s'. Pass override=True to register() if the "
                        "replacement is intentional, or deregister the existing tool first.",
                        name, toolset, existing.toolset)
                    return
            target[name] = ToolEntry(
                name=name, toolset=toolset, schema=schema, handler=handler, check_fn=check_fn,
                requires_env=requires_env or [], is_async=is_async,
                description=description or schema.get("description", ""), emoji=emoji,
                max_result_size_chars=max_result_size_chars,
                dynamic_schema_overrides=dynamic_schema_overrides)
            # Availability is derived per-tool (_toolset_has_exposable_tools), so this map no
            # longer gates a toolset; it still feeds get_toolset_requirements ->
            # TOOLSET_REQUIREMENTS["check_fn"], which banner.py reads (presence only,
            # never called) to classify an unavailable toolset as lazy-init vs disabled.
            if scope is None and check_fn and toolset not in self._toolset_checks:
                self._toolset_checks[toolset] = check_fn
            self._generation += 1

    def deregister(self, name: str, *, scope: Optional[str] = None) -> None:
        """Remove a tool; drops the toolset check/aliases if it was the last in its toolset.

        ``scope`` selects a profile overlay explicitly (multiplexed MCP tools live in the
        owning profile's overlay); plugin callers may not name another scope, non-plugin
        callers default to the process-global map. Gated by the same opt-in as
        ``register(override=True)``, else a plugin could deregister a tool it doesn't own
        and re-register over the empty slot (the override check only runs when an entry
        exists). ``mcp-*`` toolsets are exempt — discovery repaves its own tools per refresh."""
        with self._lock:
            caller_mod = self._caller_module()
            caller_owner = self._plugin_namespace_of_module(caller_mod)
            caller_scope = self._plugin_scope_of(caller_owner) if caller_owner is not None else None
            if caller_owner is not None and scope is not None and scope != caller_scope:
                raise PermissionError(
                    f"Plugin module {caller_mod!r} cannot deregister tools "
                    "outside its own profile scope.")
            if scope is None:
                scope = caller_scope
            target = self._slot(scope)
            entry = target.get(name)
            if entry is None:
                if scope is not None and caller_owner is not None and name in self._tools:
                    raise PermissionError(
                        f"Scoped plugin module {caller_mod!r} cannot deregister process-global "
                        f"tool {name!r}; register a scoped override instead.")
                return
            if not entry.toolset.startswith("mcp-"):
                owner = self._plugin_owner_of(entry.handler)
                # Ownership binds to the plugin package root (``hermes_plugins.{name}``), not
                # the exact module: a submodule's handler is still the package's to remove.
                # A handler defined in ``hermes_plugins.pkg.handlers`` is still owned by the
                # ``hermes_plugins.pkg`` package — exact string equality would wrongly block root-module
                # cleanup code from removing tools registered by a submodule of the same plugin (egilewski
                # review on #55840).
                same_plugin = bool(owner and caller_owner == owner)
                if (
                    caller_owner is not None
                    and not same_plugin
                    and not self._plugin_override_allowed(caller_scope, caller_owner)):
                    logger.error(
                        "Tool deregistration REJECTED: plugin %r attempted to "
                        "remove tool %r (toolset %r) it does not own, without operator opt-in. Set "
                        "plugins.entries.%s.allow_tool_override: true in config.yaml to allow it.",
                        caller_mod, name, entry.toolset, caller_mod)
                    raise PermissionError(
                        f"Plugin module {caller_mod!r} cannot deregister tool {name!r} (toolset "
                        f"{entry.toolset!r}) without operator opt-in (allow_tool_override).")
            del target[name]
            if scope is not None and not target:
                self._scoped_tools.pop(scope, None)
            if not self._toolset_entries(entry.toolset, scope):
                self._toolset_checks.pop(entry.toolset, None)
                self._drop_toolset_aliases(entry.toolset)
            self._generation += 1
        logger.debug("Deregistered tool: %s", name)

    def restore_registration(
        self, name: str, current: ToolEntry, previous: Optional[ToolEntry], *,
        scope: Optional[str] = None) -> bool:
        """Restore a host-owned registration if it is still current (plugin ownership ledger).
        The identity check is deliberate: another plugin (or ``PluginManager`` in a
        multi-profile process) may have registered a newer entry under the same name, and
        unloading this entry must leave that newer one untouched."""
        with self._lock:
            target = self._slot(scope, create=True)
            if target.get(name) is not current:
                return False
            if previous is None:
                target.pop(name, None)
            else:
                target[name] = previous
            if scope is not None and not target:
                self._scoped_tools.pop(scope, None)

            # Rebuild affected toolset checks from survivors: a plugin may have replaced an
            # entry in the same toolset, so its check_fn would otherwise linger after restore.
            affected_toolsets = {current.toolset}
            if previous is not None:
                affected_toolsets.add(previous.toolset)
            for toolset in affected_toolsets:
                surviving = self._toolset_entries(toolset, scope)
                check_fn = next((entry.check_fn for entry in surviving if entry.check_fn), None)
                if scope is None:
                    if check_fn is None:
                        self._toolset_checks.pop(toolset, None)
                    else:
                        self._toolset_checks[toolset] = check_fn
                in_overlays = (e for m in self._scoped_tools.values() for e in m.values())
                if not surviving and not any(e.toolset == toolset for e in in_overlays):
                    self._drop_toolset_aliases(toolset)
            self._generation += 1
        logger.debug("Restored tool registration: %s", name)
        return True

    # ---- Schema retrieval --------------------------------------------

    def get_definitions(self, tool_names: Set[str], quiet: bool = False) -> List[dict]:
        """OpenAI-format schemas for the requested tools whose ``check_fn`` passes (or is
        absent). Probes use the ~30 s TTL cache so ``hermes tools enable`` lands quickly."""
        # Lazily import only the modules that provide the requested tools —
        # this is what keeps ``get_tool_definitions(enabled_toolsets=[...])``
        # from dragging in the whole tool tree (browser, image gen, etc.).
        if self._lazy_enabled:
            for name in tool_names:
                self._ensure_tool_loaded(name)
        result = []
        check_results: Dict[Callable, bool] = {}
        # Snapshot only the REQUESTED entries under a brief lock, instead of
        # materializing a {name: entry} map of the entire (~250-tool) registry
        # on every call. A toolset selection usually asks for a handful of
        # tools, so this both shrinks the allocation and shortens the lock hold
        # (the check_fn probes below still run outside the lock). Equivalent to
        # the old full-snapshot + per-name .get(): absent names simply aren't in
        # the map and are skipped identically.
        #
        # NOTE: use the merged snapshot (global + active profile's scoped
        # plugin tools), not the bare global dict — plugin tools register
        # under the plugin manager's profile scope and must stay reachable
        # through the normal toolset path (validate_toolset/resolve_toolset
        # consult the same merged view).
        with self._lock:
            entries_by_name = {entry.name: entry for entry in self._snapshot_entries()}
        for name in sorted(tool_names):
            entry = entries_by_name.get(name)
            if not entry:
                continue
            if entry.check_fn and not _memo_check(entry.check_fn, check_results):
                if not quiet:
                    logger.debug("Tool %s unavailable (check failed)", name)
                continue
            schema_with_name = {**entry.schema, "name": entry.name}
            # Runtime-dynamic overrides (e.g. delegate_task limits); the caller's memo is
            # keyed on config.yaml mtime+size, so config changes invalidate it automatically.
            if entry.dynamic_schema_overrides is not None:
                try:
                    overrides = entry.dynamic_schema_overrides()
                except Exception as exc:
                    overrides = None
                    logger.warning(
                        "dynamic_schema_overrides for tool %s raised %s; using static schema",
                        name, exc)
                if isinstance(overrides, dict):
                    schema_with_name.update(overrides)
            result.append({"type": "function", "function": schema_with_name})
        return result

    # ---- Dispatch ----------------------------------------------------

    @staticmethod
    def _normalize_handler_result(name: str, result):
        """Results must be a string or the multimodal envelope; anything else becomes a
        string error so logging/hooks/budgeting/persistence never receive values they
        cannot slice or size."""
        if isinstance(result, str):
            return _bound_json_error_result(result)
        if (
            isinstance(result, dict)
            and result.get("_multimodal") is True
            and isinstance(result.get("content"), list)):
            return result
        result_type = type(result).__name__
        logger.error("Tool %s handler returned unsupported result type: %s", name, result_type)
        return tool_error(
            f"Tool handler returned unsupported result type: {result_type}",
            error_type="tool_result_contract", tool=name, result_type=result_type)

    def dispatch(
        self, name: str, args: dict, *, scope: Optional[str] = None, **kwargs) -> str | dict:
        """Execute a tool handler by name: async handlers bridged via ``_run_async()``,
        results normalized, every exception returned as ``{"error": ...}``."""
        entry = self.get_entry(name, scope=scope)
        if not entry:
            return tool_error(f"Unknown tool: {name}")
        try:
            if entry.is_async:
                from model_tools import _run_async
                result = _run_async(entry.handler(args, **kwargs))
            else:
                result = entry.handler(args, **kwargs)
            return self._normalize_handler_result(name, result)
        except Exception as e:
            # exc_info already renders the exception, so keep the message copy bounded.
            logger.exception("Tool %s dispatch error: %s", name, _bound_error_text(str(e)))
            # Sanitize so framing tokens/CDATA/fences in exception text aren't structural noise.
            raw = f"Tool execution failed: {type(e).__name__}: {e}"
            try:
                from model_tools import _sanitize_tool_error
                sanitized = _sanitize_tool_error(raw)
            except Exception:
                sanitized = raw  # defensive: never let the sanitizer block error propagation
            return tool_error(sanitized)

    # ---- Query helpers -----------------------------------------------

    def _attr(self, name: str, attr: str):
        return getattr(self.get_entry(name), attr, None)

    def get_max_result_size(self, name: str, default: int | float | None = None) -> int | float:
        """Return per-tool max result size, or *default* (or global default)."""
        size = self._attr(name, "max_result_size_chars")
        if size is not None:
            return size
        if default is not None:
            return default
        from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS
        return DEFAULT_RESULT_SIZE_CHARS

    def get_all_tool_names(self) -> List[str]:
        self._ensure_all_loaded()
        return sorted(entry.name for entry in self._snapshot_entries())

    def get_schema(self, name: str) -> Optional[dict]:
        """Raw schema dict, bypassing check_fn filtering (token estimates, introspection)."""
        return self._attr(name, "schema")

    def get_schema_json(self, name: str) -> Optional[str]:
        """Return a tool's raw schema pre-serialized as a JSON string, cached.

        Tool schemas are deterministic after registration, so the ``json.dumps``
        result can be reused across callers that need a serialized schema
        (token estimation, tool_search, prompt formatting for models without
        native tool-calling) instead of re-serializing on every hot-path call.

        Computed lazily on first request and memoized on the ToolEntry — NOT at
        ``register()`` time, which would add one ``json.dumps`` per tool to the
        import cascade the lazy-discovery design deliberately avoids. A
        re-``register()`` builds a fresh entry (and bumps ``_generation``), so
        the cache invalidates transparently. Mirrors :meth:`get_schema`: this is
        the STATIC registered schema, so per-call ``dynamic_schema_overrides``
        are intentionally not reflected. Output matches
        ``orjson.dumps(get_schema(name)).decode('utf-8')`` exactly, so
        ``orjson.loads(get_schema_json(name)) == get_schema(name)``.
        """
        entry = self.get_entry(name)
        if entry is None:
            return None
        cached = entry._schema_json
        if cached is None:
            # Benign race: two threads may compute the same value concurrently;
            # both write an identical string, so no lock is needed.
            cached = orjson.dumps(entry.schema).decode('utf-8')
            entry._schema_json = cached
        return cached

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        entry = self.get_entry(name)
        if entry:
            return entry.toolset
        if self._lazy_enabled:
            return self._ensure_index()["tool_to_toolset"].get(name)
        return None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """Return the emoji for a tool, or *default* if unset."""
        return self._attr(name, "emoji") or default

    def get_tool_to_toolset_map(self) -> Dict[str, str]:
        self._ensure_all_loaded()
        return {entry.name: entry.toolset for entry in self._snapshot_entries()}

    def is_toolset_available(self, toolset: str) -> bool:
        """True when a toolset has at least one exposable tool (never raises)."""
        self._ensure_toolset_loaded(toolset)
        return self._toolset_has_exposable_tools(toolset, self._snapshot_entries())


    def check_toolset_requirements(self) -> Dict[str, bool]:
        self._ensure_all_loaded()
        entries = self._snapshot_entries()
        return {
            toolset: self._toolset_has_exposable_tools(toolset, entries)
            for toolset in sorted(self._grouped(entries))}

    def get_available_toolsets(self) -> Dict[str, dict]:
        """Return toolset metadata for UI display."""
        self._ensure_all_loaded()
        entries = self._snapshot_entries()
        toolsets: Dict[str, dict] = {}
        for toolset, members in self._grouped(entries).items():
            toolsets[toolset] = {
                "available": self._toolset_has_exposable_tools(toolset, entries),
                "tools": [entry.name for entry in members],
                "description": "",
                "requirements": _unique_env(members)}
        return toolsets

    def get_toolset_requirements(self) -> Dict[str, dict]:
        """Build a TOOLSET_REQUIREMENTS-compatible dict for backward compat."""
        self._ensure_all_loaded()
        entries, toolset_checks = self._snapshot_state()
        result: Dict[str, dict] = {}
        for toolset, members in self._grouped(entries).items():
            result[toolset] = {
                "name": toolset,
                "env_vars": _unique_env(members),
                "check_fn": toolset_checks.get(toolset),
                "setup_url": None,
                "tools": [entry.name for entry in members]}
        return result

    def check_tool_availability(self, quiet: bool = False):
        """Return (available_toolsets, unavailable_info) like the old function."""
        self._ensure_all_loaded()
        available, unavailable = [], []
        entries = self._snapshot_entries()
        groups = self._grouped(entries)
        for ts in sorted(groups):
            if self._toolset_has_exposable_tools(ts, entries):
                available.append(ts)
            else:
                unavailable.append({
                    "name": ts, "env_vars": groups[ts][0].requires_env,
                    "tools": [entry.name for entry in groups[ts]]})
        return available, unavailable


def _unique_env(entries: List[ToolEntry]) -> list:
    """Union of ``requires_env`` across *entries*, first-seen order, no duplicates."""
    out: list = []
    for entry in entries:
        out.extend(v for v in (entry.requires_env or []) if v not in out)
    return out


# Module-level singleton
registry = ToolRegistry()


# Tool handlers must return JSON strings; these replace the ubiquitous
# ``json.dumps({"error": msg}, ensure_ascii=False)`` boilerplate.


def tool_error(message, **extra) -> str:
    """``'{"error": "<message>", **extra}'`` — the error body is bounded so a raw
    exception can't bloat history across retries."""
    return orjson.dumps({"error": _bound_error_text(str(message)), **extra}).decode('utf-8')


def tool_result(data=None, **kwargs) -> str:
    """JSON-encode a dict positional arg *or* keyword arguments (not both)."""
    return orjson.dumps(data if data is not None else kwargs).decode('utf-8')
