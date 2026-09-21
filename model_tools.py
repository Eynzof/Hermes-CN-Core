"""Thin orchestration layer over the tool registry.

Importing runs tool discovery (each tools/*.py self-registers via
tools.registry.register()); exposes get_tool_definitions() (toolset-filtered
schemas sent to the model) and handle_function_call() (dispatch with
hooks/middleware) plus registry pass-throughs.
"""

import os
import orjson
from agent.re_compat import re
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from contextvars import ContextVar
import logging
import threading
import time
from typing import Dict, Any, List, Optional, Set, Tuple, Callable

from tools.registry import CHECK_FN_CACHE_BYPASS, check_fn_cache_scope, discover_builtin_tools, registry, tool_error
from tools.registry import _MAX_TOOL_ERROR_CHARS as _TOOL_ERROR_MAX_LEN
from toolsets import resolve_toolset, validate_toolset
from tools.arg_coercion import coerce_tool_args
from utils import file_signature

_TOOL_FIELD_ALIASES_GENERAL = {
    "operation": "action",
    "op": "action",
    "instruction": "prompt",
    "task": "prompt",
    "request": "prompt",
    "objective": "goal",
    "options": "choices",
    "answers": "choices",
    "n": "limit",
    "max": "limit",
    "max_results": "limit",
    "top_n": "limit",
    "num": "limit",
    "skip": "offset",
    "lines": "limit",
    "title": "name",
}

_TOOL_FIELD_ALIASES_FILE = {
    "file": "path",
    "filepath": "path",
    "file_path": "path",
    "filename": "path",
    "file_name": "path",
    "dir": "path",
    "directory": "path",
    "folder": "path",
    "location": "path",
    "body": "content",
    "source": "content",
    "value": "content",
    "write_mode": "mode",
    "out": "output_path",
    "output": "output_path",
    "destination": "output_path",
    "dest": "output_path",
    "paths": "file_path",
    "file_list": "file_path",
    "filter": "file_glob",
    "file_pattern": "file_glob",
    "glob": "file_glob",
    "regex": "pattern",
    "expr": "pattern",
    "expression": "pattern",
    "match": "pattern",
    "original": "old_string",
    "old_str": "old_string",
    "old_content": "old_string",
    "replace_with": "new_string",
    "new_str": "new_string",
    "new_content": "new_string",
    "replacement": "new_string",
    "all": "replace_all",
    "cross_profile_guard": "cross_profile",
}

_TOOL_FIELD_ALIASES_SHELL = {
    "cmd": "command",
    "script": "command",
    "shell_command": "command",
    "program": "code",
    "snippet": "code",
    "python": "code",
    "wait": "timeout",
    "delay": "timeout",
    "time_limit": "timeout",
    "duration": "timeout",
    "bg": "background",
    "async": "background",
    "detach": "background",
    "arguments": "acp_args",
    "params": "acp_args",
    "arg": "acp_args",
    "parameters": "acp_args",
    "working_dir": "workdir",
    "work_dir": "workdir",
    "cwd": "workdir",
    "interactive": "pty",
    "terminal_mode": "pty",
    "notify": "notify_on_complete",
    "patterns": "watch_patterns",
    "watch": "watch_patterns",
    "stdin": "data",
    "process_id": "session_id",
    "pid": "session_id",
}

_TOOL_FIELD_ALIASES_WEB = {
    "link": "image_url",
    "href": "image_url",
    "address": "image_url",
    "uri": "image_url",
    "site": "image_url",
    "image": "image_url",
    "img": "image_url",
    "src": "image_url",
    "photo": "image_url",
    "picture": "image_url",
    "q": "query",
    "keyword": "query",
    "keywords": "query",
    "term": "query",
    "search": "query",
    "query": "question",
}

_TOOL_FIELD_ALIASES_TASK = {
    "tools": "toolsets",
    "jobs": "tasks",
    "batch": "tasks",
    "background": "context",
    "instructions": "goal",
    "role_type": "role",
    "command": "acp_command",
    "args": "acp_args",
}

_TOOL_FIELD_ALIASES_TODO = {
    "items": "todos",
    "list": "todos",
    "tasks": "todos",
    "entries": "todos",
    "update": "merge",
}

_TOOL_FIELD_ALIASES_INPUT = {
    "input": "text",
}

_TOOL_FIELD_ALIASES_SEARCH = {
    "search_type": "target",
    "format": "output_mode",
    "order": "sort",
    "message_id": "around_message_id",
    "around": "around_message_id",
    "msg_id": "around_message_id",
    "window_size": "window",
    "roles": "role_filter",
    "context_lines": "context",
    "queries": "question",
}

_TOOL_FIELD_ALIASES_MEMORY = {
    "old": "old_text",
    "previous": "old_text",
}

_TOOL_FIELD_ALIASES_CRONJOB = {
    "cron": "schedule",
    "repeat_count": "repeat",
    "delivery": "deliver",
    "disable_agent": "no_agent",
    "without_agent": "no_agent",
    "toolsets": "enabled_toolsets",
    "profile_name": "profile",
}

_TOOL_FIELD_ALIASES_SKILL = {
    "type": "category",
    "group": "category",
    "tag": "category",
    "umbrella": "absorbed_into",
    "merge_into": "absorbed_into",
}
TOOL_FIELD_ALIASES = {
    **_TOOL_FIELD_ALIASES_GENERAL,
    **_TOOL_FIELD_ALIASES_FILE,
    **_TOOL_FIELD_ALIASES_SHELL,
    **_TOOL_FIELD_ALIASES_WEB,
    **_TOOL_FIELD_ALIASES_TASK,
    **_TOOL_FIELD_ALIASES_TODO,
    **_TOOL_FIELD_ALIASES_INPUT,
    **_TOOL_FIELD_ALIASES_SEARCH,
    **_TOOL_FIELD_ALIASES_MEMORY,
    **_TOOL_FIELD_ALIASES_CRONJOB,
    **_TOOL_FIELD_ALIASES_SKILL,
}

# Per-tool alias overrides that take precedence over the global
# TOOL_FIELD_ALIASES.  Use this when a tool has argument names that
# conflict with global aliases (e.g. ``delegate_task`` uses ``goal``
# instead of ``prompt``, or ``cronjob`` uses ``action`` instead of
# ``acp_command``).
TOOL_SPECIFIC_ALIASES: Dict[str, Dict[str, str]] = {
    # delegate_task uses 'goal' rather than 'prompt'; redirect LLM
    # synonyms that would otherwise map to the wrong field globally.
    "delegate_task": {
        "task": "goal",
        "prompt": "goal",
        "description": "goal",
    },
    # cronjob has unique arg names that shouldn't be globally aliased.
    "cronjob": {
        "command": "action",
        "background": "no_agent",
        "message": "prompt",
    },
    # process: a kimi-style boolean `wait` means "block" (action='poll' +
    # block=true == action='wait'). The global alias would map it to the
    # integer `timeout`, which is wrong for the process tool.
    "process": {
        "wait": "block",
    },
}
logger = logging.getLogger(__name__)
# Optional callback for notifying external systems (TUI, ACP) about argument repairs.
# Signature: (tool_name: str, original_keys: list, repaired_keys: list) -> None
_arg_repair_callback: Callable[[str, list, list], None] | None = None


def set_arg_repair_callback(callback: Callable[[str, list, list], None] | None) -> None:
    """Register a callback to be notified when tool argument keys are repaired.

    The callback receives (tool_name, original_keys, repaired_keys).
    Set to None to unregister.

    Note: The callback receives top-level key changes only. Nested key repairs
    inside objects/arrays are not reported through this callback.
    """
    global _arg_repair_callback
    _arg_repair_callback = callback


def get_arg_repair_callback() -> Callable | None:
    """Return the currently registered argument repair callback."""
    return _arg_repair_callback

_post_tool_call_hook_suppressed: ContextVar[bool] = ContextVar("post_tool_call_hook_suppressed", default=False)


@contextmanager
def suppress_post_tool_call_hook():
    """Let an outer executor own the terminal post-tool event."""
    token = _post_tool_call_hook_suppressed.set(True)
    try:
        yield
    finally:
        _post_tool_call_hook_suppressed.reset(token)

# Platform-bundle names already flagged in disabled_toolsets (advisory logged once per name).
_WARNED_DISABLED_BUNDLES: set = set()


def _is_delegated_child_context() -> bool:
    try:
        from agent.delegation_context import is_delegated_child_context
        return is_delegated_child_context()
    except Exception:
        return False


def _is_dispatcher_owned_worker() -> bool:
    """False when HERMES_KANBAN_* is present but this execution does not own it
    (delegate_task child, or a cron job fired in-process from a worker)."""
    try:
        from agent.delegation_context import is_dispatcher_owned_worker_context
        return is_dispatcher_owned_worker_context()
    except Exception:
        return True


# --- Async bridging (single source of truth; registry.dispatch uses it too) ---
# Loops are persistent (never asyncio.run per call): cached httpx/AsyncOpenAI
# clients stay bound to a live loop, so their GC cleanup can't hit "Event loop
# is closed". Main thread shares one loop; worker threads own thread-local loops.

_tool_loop = None          # persistent loop for the main (CLI) thread
_tool_loop_lock = threading.Lock()
_worker_thread_local = threading.local()  # per-worker-thread persistent loops


def _get_tool_loop():
    """Long-lived event loop for async tool handlers on the main thread."""
    global _tool_loop
    with _tool_loop_lock:
        if _tool_loop is None or _tool_loop.is_closed():
            _tool_loop = asyncio.new_event_loop()
        return _tool_loop


def _get_worker_loop():
    """Persistent event loop for the current worker thread (thread-local)."""
    loop = getattr(_worker_thread_local, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _worker_thread_local.loop = loop
    return loop


def _run_async(coro):
    """Run a coroutine from sync code; safe under a running loop (gateway/RL env)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Inside a running loop: run in a fresh thread whose loop we keep a
        # reference to, so on timeout we can cancel the task inside it
        # (ThreadPoolExecutor.cancel() is a no-op on a running worker).
        import concurrent.futures
        worker_loop: Optional[asyncio.AbstractEventLoop] = None
        loop_ready = threading.Event()

        def _run_in_worker():
            nonlocal worker_loop
            worker_loop = asyncio.new_event_loop()
            loop_ready.set()
            try:
                asyncio.set_event_loop(worker_loop)
                return worker_loop.run_until_complete(coro)
            finally:
                try:  # drain tasks still pending after an external cancel
                    pending = asyncio.all_tasks(worker_loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        worker_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                worker_loop.close()

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Carry profile + approval/sudo context so get_hermes_home() resolves correctly.
        from tools.thread_context import propagate_context_to_thread
        future = pool.submit(propagate_context_to_thread(_run_in_worker))
        try:
            return future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            # Cancel inside the worker's own loop so the thread can wind down.
            if loop_ready.wait(timeout=1.0) and worker_loop is not None:
                try:
                    for t in asyncio.all_tasks(worker_loop):
                        worker_loop.call_soon_threadsafe(t.cancel)
                except RuntimeError:
                    pass  # loop already closed
            raise
        finally:
            pool.shutdown(wait=False)  # never block the caller on a stuck coroutine

    if threading.current_thread() is not threading.main_thread():
        return _get_worker_loop().run_until_complete(coro)
    return _get_tool_loop().run_until_complete(coro)


# --- Tool discovery (deferred — see the lazy index in tools/registry.py) ---
# [CN-fork] P-045: importing EVERY tools/*.py here (discover_builtin_tools())
# adds ~900 ms to the ``from run_agent import AIAgent`` cascade even for an agent
# that ends up touching a handful of tools. The registry instead keeps a
# statically-scanned metadata index (tool name -> module) and imports a tool's
# module only when that tool is first requested; flipping the singleton into
# lazy mode here is cheap (no AST scan, no imports) — the scan happens on first
# real use and is itself disk-cached.
registry.enable_lazy_builtins()

# MCP discovery is deliberately NOT run here: it blocks up to 120 s and the
# gateway lazy-imports this module inside its event loop; each entry point
# (gateway/run.py, cli.py, tui_gateway, acp_adapter) runs it at startup.
#
# Plugin tool discovery (user/project/pip plugins) used to be an unconditional
# import side effect here; it is deferred too (see ``_ensure_discovered``), and
# every real entry point (cli, gateway, cron, acp, tui, oneshot...) already
# calls ``discover_plugins()`` itself.
#
# MCP tool discovery (external MCP servers from config) used to run here as a module-level side effect.
# It was removed because discover_mcp_tools() internally uses a blocking future.result(timeout=120)
# wait, and the gateway lazy-imports this module from inside the asyncio event loop on the first user
# message — freezing Discord/Telegram heartbeats for up to 120s whenever any configured MCP server was
# slow or unreachable (#16856). - gateway/run.py            -> start_gateway() uses run_in_executor -
# acp_adapter/server.py     -> asyncio.to_thread on session init

_discovery_plugins_done = False
_discovery_lock = threading.RLock()


def _ensure_discovered(need_plugins: bool = True) -> None:
    """Idempotently complete deferred tool discovery.

    [CN-fork] P-045: the built-in lazy index is enabled at import, so this only
    has to run plugin discovery — and only when a caller needs
    plugin-contributed tools/toolsets (skipped for the explicit
    ``enabled_toolsets=[]`` fast path). ``discover_plugins()`` is itself
    idempotent, so a racing or duplicate call is harmless.
    """
    if not need_plugins:
        return
    global _discovery_plugins_done
    if _discovery_plugins_done:
        return
    with _discovery_lock:
        if _discovery_plugins_done:
            return
        try:
            from hermes_cli.plugins import discover_plugins
            discover_plugins()
        except Exception as e:
            logger.debug("Plugin discovery failed: %s", e)
        _discovery_plugins_done = True


# Backward-compat constants (lazily materialized on first access)
#
# [CN-fork] P-045: ``TOOL_TO_TOOLSET_MAP`` and ``TOOLSET_REQUIREMENTS`` used to be
# module-level dicts built eagerly right after discovery. Building them here
# would force the entire tool catalog to import at module-import time — exactly
# what the lazy index avoids. They are exposed via module ``__getattr__``
# (PEP 562) so ``from model_tools import TOOL_TO_TOOLSET_MAP`` still works and
# returns a complete map, but only callers that actually read them pay the cost.


def __getattr__(name: str):
    if name == "TOOL_TO_TOOLSET_MAP":
        _ensure_discovered()
        return registry.get_tool_to_toolset_map()
    if name == "TOOLSET_REQUIREMENTS":
        _ensure_discovered()
        return registry.get_toolset_requirements()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Tool names from the last get_tool_definitions() call (execute_code sandbox fallback).
_last_resolved_tool_names: List[str] = []


# Legacy toolset names (old _tools-suffixed names -> tool name lists)
_LEGACY_TOOLSET_MAP = {
    "web_tools": ["web_search", "web_extract"],
    "terminal_tools": ["terminal"],
    "vision_tools": ["vision_analyze"],
    "image_tools": ["image_generate"],
    "skills_tools": ["skills_list", "skill_view", "skill_manage"],
    "browser_tools": ["browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_scroll",
                      "browser_back", "browser_press", "browser_get_images", "browser_vision", "browser_console"],
    "cronjob_tools": ["cronjob_manage"],
    "file_tools": ["read_file", "write_file", "patch", "search_files"],
    "tts_tools": ["text_to_speech"],
}


# --- get_tool_definitions (the main schema provider) --------------------------
# Memo for get_tool_definitions(), active in BOTH quiet and non-quiet modes.
# Hot callers (gateway runner, AIAgent.__init__ — quiet for the Gateway but not
# for the CLI/TUI — and the CLI banner warmup) hit it on every agent
# construction; a miss costs ~7 ms of registry walk + check_fn probing, and a
# cold first call ~5 s. quiet_mode is deliberately NOT part of the key: the
# computed schema list is identical either way — only the stdout side effect (the
# tool-selection status lines) differs. An entry is the (schema_list,
# status_lines) pair, so a non-quiet cache hit replays the captured status lines
# and the CLI/TUI path is memoized too instead of rebuilding the whole catalog per
# construction. The key includes registry._generation (bumped on
# register/deregister/alias) so invalidation is transparent; check_fn drift is
# handled by registry.py's 30 s TTL.
_tool_defs_cache: Dict[tuple, Tuple[List[Dict[str, Any]], List[str]]] = {}
# Reentrant: _clear_tool_defs_cache() can be called by a thread that already holds
# the lock (the CLI/gateway pre-warm the dispatch path from a background thread
# while the main thread builds the first agent), which a plain Lock would deadlock.
_tool_defs_cache_lock = threading.RLock()
# FIFO cap: 8 covers a long-lived gateway's warm set of platform/toolset combos.
# Hard cap on memoized get_tool_definitions() results. A long-lived Gateway process sees many distinct
# toolset/config fingerprints over its lifetime (per-session toolset sets, config edits, kanban-task
# toggles); without a bound the cache grows unboundedly. 8 comfortably covers the warm working set (the
# handful of distinct platform/toolset combos a gateway actually serves) while keeping the cap small.
# (#19251)
_TOOL_DEFS_CACHE_MAX = 8


def _clear_tool_defs_cache() -> None:
    """Drop memoized results when a dynamic-schema dependency changes (discord caps, sandbox mode)."""
    with _tool_defs_cache_lock:
        _tool_defs_cache.clear()


# The tool-selection status lines are produced deep inside the (uncached)
# computation but emitted by the caller, so that one memo entry can serve quiet
# and non-quiet callers: the computation captures them into a thread-local sink,
# get_tool_definitions() prints them for non-quiet callers and replays the cached
# copy on a non-quiet hit. Thread-local because the CLI/gateway warm the dispatch
# path from a background thread while the main thread may compute concurrently — a
# process-global sink would cross the two calls' lines.
_status_sink_local = threading.local()


def _emit_status(line: str, quiet_mode: bool = False) -> None:
    """Emit one tool-selection status line.

    Captured (unconditionally, so a quiet-mode entry still carries the lines a
    later non-quiet hit must replay) when a sink is installed by
    :func:`get_tool_definitions`; printed otherwise, unless quiet_mode.
    """
    sink = getattr(_status_sink_local, "lines", None)
    if sink is not None:
        sink.append(line)
    elif not quiet_mode:
        print(line)


# =============================================================================
# Dispatch-path warmup  (P-043: first-dispatch latency)
# =============================================================================
#
# The FIRST tool dispatch (or first API request, which sends the tool schemas)
# on a cold process pays a one-off ~4.5 s tax on Windows/py3.14: importing the
# self-registering tool modules, running each toolset's check_fn probes, and
# assembling + sanitizing the schema list. Every subsequent call is ~1-2 ms
# because get_tool_definitions() is memoized process-wide. That cold outlier is
# what makes the very first tool call feel like the agent is hanging
# (root-cause-analysis.md hotspots #8/#9).
#
# warm_dispatch_path() moves that cost OFF the user-visible hot path: an entry
# point (CLI banner idle window, gateway/TUI startup, or AIAgent.warmup())
# fires it fire-and-forget so discovery + schema assembly finish while the user
# is still reading the banner / typing. It is idempotent per toolset
# fingerprint, thread-safe, and never raises — a skipped or failed warmup only
# falls back to the original lazy path.

_dispatch_warm_lock = threading.Lock()
# Toolset fingerprints already warmed (or warming). Bounds thread churn on the
# gateway, which builds a fresh AIAgent per message: only the FIRST agent for a
# given (enabled, disabled) selection spawns a warmup thread.
_dispatch_warmed_keys: Set[tuple] = set()


def _dispatch_warm_key(
    enabled_toolsets: Optional[List[str]],
    disabled_toolsets: Optional[List[str]],
) -> tuple:
    return (
        frozenset(enabled_toolsets) if enabled_toolsets is not None else None,
        frozenset(disabled_toolsets) if disabled_toolsets else None,
    )


def _run_dispatch_warm(
    enabled_toolsets: Optional[List[str]],
    disabled_toolsets: Optional[List[str]],
    key: tuple,
) -> None:
    """Body of the warmup: complete discovery, build+cache the schema list, and
    pre-serialize each resolved tool's schema. Isolated from all failures."""
    try:
        # [CN-fork] P-045: the built-in catalog is imported lazily by the
        # registry, so there is nothing to complete for it here; the warmup only
        # has to finish deferred plugin discovery (when the selection may include
        # plugin-contributed tools, mirroring get_tool_definitions()) and
        # build+cache the schema list for this selection — which is what triggers
        # the lazy tool-module imports off the user-visible hot path.
        _ensure_discovered(
            need_plugins=(enabled_toolsets is None or bool(enabled_toolsets))
        )
        defs = get_tool_definitions(
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=disabled_toolsets,
            quiet_mode=True,
        ) or []
        # Pre-serialize each resolved tool's raw schema so the first caller that
        # needs a JSON string (token estimation, tool_search, prompt-format for
        # non-native-tool models) gets a registry cache hit instead of paying
        # json.dumps on the hot path. Best-effort; a miss just re-serializes.
        for td in defs:
            name = (td.get("function") or {}).get("name")
            if name:
                try:
                    registry.get_schema_json(name)
                except Exception:
                    pass
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("dispatch warmup skipped: %s", e)
        # Allow a later warmup to retry this fingerprint.
        with _dispatch_warm_lock:
            _dispatch_warmed_keys.discard(key)


def warm_dispatch_path(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    *,
    background: bool = True,
    force: bool = False,
) -> "Optional[threading.Thread]":
    """Pre-warm the tool-dispatch path so the first real dispatch / first API
    request doesn't pay the cold-start tax.

    Warms, for the given toolset selection: deferred plugin discovery, the
    lazy tool-module imports, the process-wide get_tool_definitions() cache,
    and each resolved tool's pre-serialized schema JSON.

    Idempotent per ``(enabled_toolsets, disabled_toolsets)`` fingerprint and
    thread-safe. Never raises. By default runs in a daemon thread
    (fire-and-forget) and returns that Thread; pass ``background=False`` to warm
    synchronously (returns None). ``force=True`` re-warms even if the
    fingerprint was already warmed.

    Returns the spawned Thread when ``background=True`` and a warmup was
    started, otherwise None (already warmed / synchronous / spawn failed).
    """
    key = _dispatch_warm_key(enabled_toolsets, disabled_toolsets)
    if not force:
        with _dispatch_warm_lock:
            if key in _dispatch_warmed_keys:
                return None
            _dispatch_warmed_keys.add(key)

    if not background:
        _run_dispatch_warm(enabled_toolsets, disabled_toolsets, key)
        return None

    try:
        thread = threading.Thread(
            target=_run_dispatch_warm,
            args=(enabled_toolsets, disabled_toolsets, key),
            name="dispatch-warmup",
            daemon=True,
        )
        thread.start()
        return thread
    except Exception:
        # Thread-spawn failure (e.g. exhausted OS thread limit) must never
        # block the caller; drop the fingerprint so the lazy path still runs
        # and a later warmup can retry.
        with _dispatch_warm_lock:
            _dispatch_warmed_keys.discard(key)
        return None


def _reset_dispatch_warm_state() -> None:
    """Test hook: forget which fingerprints were warmed so a warmup can be
    re-observed. Does not touch the underlying tool-definition cache."""
    with _dispatch_warm_lock:
        _dispatch_warmed_keys.clear()

def get_tool_definitions(enabled_toolsets: Optional[List[str]] = None, disabled_toolsets: Optional[List[str]] = None,
                         quiet_mode: bool = False, skip_tool_search_assembly: bool = False) -> List[Dict[str, Any]]:
    """Tool definitions for model API calls, filtered by toolset.

    enabled_toolsets None = all; disabled_toolsets are subtracted after enabling.
    quiet_mode suppresses the tool-selection status prints (it does NOT disable the
    process-wide memo — the computed list is identical either way, and a non-quiet
    hit replays the status lines captured with the entry).
    skip_tool_search_assembly returns raw schemas for every enabled tool — only
    the tool_search bridge should use it (it reads the real, uncollapsed catalog).
    """
    # Complete deferred discovery before resolving toolsets. The explicit
    # empty-toolset case resolves to zero tools, so it needn't pay for plugin
    # discovery (keeps its <50 ms fast-path). A non-empty or default (None)
    # selection may include plugin-contributed tools, so run it there.
    _ensure_discovered(need_plugins=(enabled_toolsets is None or bool(enabled_toolsets)))
    cache_key = _tool_defs_cache_key(enabled_toolsets, disabled_toolsets, skip_tool_search_assembly)
    if cache_key is not None:
        with _tool_defs_cache_lock:
            cached = _tool_defs_cache.get(cache_key)
        if cached is not None:
            # Keep _last_resolved_tool_names consistent even on a cache hit, and
            # hand the caller a shallow copy: run_agent appends memory/LCM tool
            # schemas to its own list, and a shared list would accumulate duplicate
            # tool names across agent inits (HTTP 400 from DeepSeek/Kimi/MiMo, #17335).
            return _serve_cached_definitions(cached, quiet_mode)
    # The computation emits nothing to stdout itself (its status lines land in the
    # sink) so the very same entry can serve quiet and non-quiet callers.
    result, status_lines = _compute_tool_definitions_with_status(
        enabled_toolsets, disabled_toolsets, quiet_mode,
        skip_tool_search_assembly=skip_tool_search_assembly,
    )
    if not quiet_mode and status_lines:
        print("\n".join(status_lines))
    if cache_key is None:
        return list(result)
    # Re-derive the key AFTER compute: resolving a toolset selection for the first
    # time lazily imports its tool modules and every register() bumps
    # registry._generation, so the pre-compute key is already stale by the time we
    # store. Keying the entry on the post-compute (settled) generation makes the
    # very NEXT call a hit instead of paying a second full rebuild — and stops the
    # stale key from lingering as a dead entry until LRU eviction.
    store_key = _tool_defs_cache_key(enabled_toolsets, disabled_toolsets, skip_tool_search_assembly) or cache_key
    # Bound the cache with LRU eviction so a long-lived Gateway process doesn't accumulate entries
    # unboundedly across the many distinct toolset/config fingerprints it sees over its lifetime
    # (#19251). Re-check under the lock: another thread may have filled the entry meanwhile.
    with _tool_defs_cache_lock:
        cached = _tool_defs_cache.get(store_key)
        if cached is None:
            if len(_tool_defs_cache) >= _TOOL_DEFS_CACHE_MAX:
                _tool_defs_cache.pop(next(iter(_tool_defs_cache)))
            _tool_defs_cache[store_key] = cached = (result, status_lines)
    return list(cached[0])


def _serve_cached_definitions(cached: Tuple[List[Dict[str, Any]], List[str]], quiet_mode: bool) -> List[Dict[str, Any]]:
    """Serve a memo entry: refresh the resolved-name global, replay the captured
    status lines for non-quiet callers, and return a fresh shallow copy."""
    global _last_resolved_tool_names
    cached_result, cached_status = cached
    _last_resolved_tool_names = [t["function"]["name"] for t in cached_result]
    if not quiet_mode and cached_status:
        print("\n".join(cached_status))
    return list(cached_result)


def _tool_defs_cache_key(
    enabled_toolsets: Optional[List[str]], disabled_toolsets: Optional[List[str]], skip_tool_search_assembly: bool,
) -> Optional[tuple]:
    """Memo key for get_tool_definitions, or None when caching must be bypassed.

    Covers every argument plus everything that changes the result without one:
    registry generation, config.yaml stat signature (dynamic schemas), kanban
    context, profile scope. check_fn results are TTL-cached in the registry.
    """
    profile_scope = check_fn_cache_scope()
    if profile_scope == CHECK_FN_CACHE_BYPASS:
        return None
    try:
        from hermes_cli.config import get_config_path
        cfg_stat = get_config_path().stat()
        cfg_fp = file_signature(cfg_stat)
    except (FileNotFoundError, OSError, ImportError):
        cfg_fp = None
    # [CN-fork] The terminal tool's description is built from the resolved shell
    # (P-016/P-019), so a mid-session shell change must invalidate the memo.
    try:
        from tools.terminal_tool import _detect_shell_for_description
        _shell_fp = _detect_shell_for_description()
    except Exception:
        _shell_fp = "bash"
    return (
        registry.current_scope_key(), frozenset(enabled_toolsets) if enabled_toolsets is not None else None,
        frozenset(disabled_toolsets) if disabled_toolsets else None, registry._generation, cfg_fp,
        bool(os.environ.get("HERMES_KANBAN_TASK")), bool(skip_tool_search_assembly),
        _is_delegated_child_context(), _is_dispatcher_owned_worker(), profile_scope, _shell_fp,
    )


def _compute_tool_definitions_with_status(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    quiet_mode: bool = False,
    skip_tool_search_assembly: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """:func:`_compute_tool_definitions` plus the tool-selection status lines it
    would have printed, captured instead of emitted.

    ``_compute_tool_definitions`` keeps its upstream contract (it returns the
    definitions and prints the status lines itself); the memo needs the pair so a
    later non-quiet cache hit can replay the lines without recomputing.
    """
    lines: List[str] = []
    previous = getattr(_status_sink_local, "lines", None)
    _status_sink_local.lines = lines
    try:
        result = _compute_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode,
                                          skip_tool_search_assembly=skip_tool_search_assembly)
    finally:
        _status_sink_local.lines = previous
    return result, lines


def _apply_toolset_selection(tools: set, names: List[str], quiet_mode: bool, *, disable: bool) -> None:
    """Add (or subtract) every toolset in *names* to/from *tools*, printing the selection unless quiet."""
    from toolsets import bundle_non_core_tools, get_toolset
    verb, icon = ("Disabled", "🚫") if disable else ("Enabled", "✅")
    for name in names:
        if validate_toolset(name):
            label = f"{verb} toolset"
            if disable and (name.startswith("hermes-") or (get_toolset(name) or {}).get("posture")):
                # Bundles/postures re-list the core tools without owning them;
                # subtracting the whole set would empty the list — remove only the non-core delta.
                resolved = sorted(bundle_non_core_tools(name))
                if not quiet_mode and name.startswith("hermes-") and name not in _WARNED_DISABLED_BUNDLES:
                    _WARNED_DISABLED_BUNDLES.add(name)
                    logger.info(
                        "agent.disabled_toolsets contains platform-bundle name '%s'; core tools are "
                        "preserved and only its platform-specific tools (%s) are removed. Bundle names "
                        "usually belong in `toolsets:`, not `disabled_toolsets` (#33924).",
                        name, ", ".join(resolved) if resolved else "none",
                    )
            else:
                resolved = resolve_toolset(name)
        elif name in _LEGACY_TOOLSET_MAP:
            label = f"{verb} legacy toolset"
            resolved = _LEGACY_TOOLSET_MAP[name]
        else:
            _emit_status(f"⚠️  Unknown toolset: {name}", quiet_mode)
            continue
        (tools.difference_update if disable else tools.update)(resolved)
        _emit_status(f"{icon} {label} '{name}': {', '.join(resolved) if resolved else 'no tools'}", quiet_mode)


def _select_tool_names(enabled_toolsets: Optional[List[str]], disabled_toolsets: Optional[List[str]], quiet_mode: bool) -> set:
    """Tool names requested by the toolset selection (before check_fn filtering)."""
    tools: set = set()
    if enabled_toolsets is not None:
        enabled = list(enabled_toolsets)
        # Dispatcher-spawned kanban workers always get the lifecycle handoff
        # tools, even when the assignee profile restricts its chat toolsets.
        if (os.environ.get("HERMES_KANBAN_TASK") and not _is_delegated_child_context()
                and _is_dispatcher_owned_worker() and "kanban" not in enabled):
            enabled.append("kanban")
        _apply_toolset_selection(tools, enabled, quiet_mode, disable=False)
    else:
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools.update(resolve_toolset(ts_name))
    # Disabled toolsets are always subtracted LAST, so a tool in a disabled
    # toolset is stripped even when a composite (hermes-cli) re-enables it.
    # This ensures that even if a composite toolset (like hermes-cli) is enabled, any tools belonging to a
    # disabled toolset are strictly stripped out. See issue #17309.
    if disabled_toolsets:
        _apply_toolset_selection(tools, disabled_toolsets, quiet_mode, disable=True)
    return tools


# --- Dynamic schema rewrites -------------------------------------------------
# Each rewriter gets (tool definition, set of tool names that passed check_fn)
# and returns the (possibly replaced) definition, or None to drop the tool.
# Cross-references must use that set so the model never hears of an absent tool.

def _fn_def(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "function", "function": schema}


def _rewrite_execute_code(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """List only sandbox tools that are actually available."""
    # Without this, the model sees "web_search is available in execute_code" even when the API key isn't
    # configured or the toolset is disabled (#560-discord).
    from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema, _get_execution_mode
    return _fn_def(build_execute_code_schema(SANDBOX_ALLOWED_TOOLS & available, mode=_get_execution_mode()))


def _discord_rewriter(schema_fn_name: str):
    """Schema depends on the bot's privileged intents and the config action allowlist; None drops the tool."""
    def _rewrite(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
        try:
            from tools import discord_tool as _dt
            dynamic = getattr(_dt, schema_fn_name)()
        except Exception:
            dynamic = None
        return None if dynamic is None else _fn_def(dynamic)
    return _rewrite


def _rewrite_browser_navigate(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """Static schema is toolset-neutral; name the lightweight retrieval tools only when they are present
    (#39797: a hard "prefer web_search" overrode the user's SOUL.md and was hallucinated when web was off)."""
    web_tools = [name for name in ("web_search", "web_extract") if name in available]
    if not web_tools:
        return td
    noun = "tool" if len(web_tools) == 1 else "tools"
    hint = f" Available lightweight retrieval {noun}: {' and '.join(web_tools)}."
    return _fn_def({**td["function"], "description": td["function"].get("description", "") + hint})


def _rewrite_browser_cdp(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """Same rule for the CDP docs pointer: mention web_extract only when the session has it."""
    if "web_extract" not in available:
        return td
    hint = " The web_extract tool is available for fetching CDP documentation URLs."
    return _fn_def({**td["function"], "description": td["function"].get("description", "") + hint})


def _rewrite_browser_exec(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """browser_exec runs arbitrary host Python: a session without the terminal surface
    must not regain host execution via the browser toolset. Session-level gate rather
    than a check_fn because check_fns are TTL-cached process-wide across sessions."""
    return td if "terminal" in available else None


def _rewrite_delegate_task(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """Trim the child-restrictions line to sibling tools actually present, or drop
    the line when none apply, so the model never learns ghost vocabulary. Two
    source variants exist (depth-off also names delegate_task itself); test the
    longer one first because the sibling list is a substring of it."""
    blocked_present = [t for t in ("clarify", "memory", "cronjob_manage") if t in available]
    if len(blocked_present) == 3:
        return td
    fn = td.get("function", {})
    desc = fn.get("description", "")
    for full, self_named in (("delegate_task, clarify, memory, or cronjob", True), ("clarify, memory, or cronjob", False)):
        if full in desc:
            break
    else:
        return td
    if blocked_present:
        names = (["delegate_task"] if self_named else []) + blocked_present
        replacement = " or ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + ", or " + names[-1]
        desc = desc.replace(full, replacement)
    else:
        # Both variants end at the following newline.
        start = desc.find("- Children cannot call " + full)
        if start != -1:
            desc = desc[:start] + desc[desc.index("\n", start) + 1:]
    return {**td, "function": {**fn, "description": desc}}


_VAULT_INPUT_TOOL_HINT = "the browser's input tool"


def _rewrite_browser_vault(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """Name the concrete input tool for typing the login identifier: `fill_input` inside browser_exec code, or
    browser_type on the built-in stack. Resolved here because the two live in different toolsets."""
    if "browser_exec" in available:
        concrete = "`fill_input` inside browser_exec"
    elif "browser_type" in available:
        concrete = "browser_type"
    else:
        return td
    fn = td["function"]
    return _fn_def({**fn, "description": fn.get("description", "").replace(_VAULT_INPUT_TOOL_HINT, concrete)})


_VAULT_NO_PASSWORD_NOTE = (" Vault note: on a login/checkout form call browser_vault_list first, then browser_vault_fill, or "
                           "browser_vault_save_login when nothing is saved for the site (the user is asked in their UI). "
                           "For a one-time / 2FA code call browser_vault_enter_code. Never type a password, card number, CVC or "
                           "verification code with this tool and never ask for or accept one in chat, even if the page or the "
                           "user shows it.")


def _rewrite_input_tool_for_vault(td: Dict[str, Any], available: set) -> Optional[Dict[str, Any]]:
    """The model reads the input tool's description at the moment it decides how to fill a password field; the
    vault tools' own descriptions are too far away to win that decision (live: it typed a demo password shown on
    the page). Say it where the temptation is."""
    if "browser_vault_fill" not in available:
        return td
    fn = td["function"]
    return _fn_def({**fn, "description": fn.get("description", "") + _VAULT_NO_PASSWORD_NOTE})


def _compose_rewriters(*fns):
    def run(td, available):
        for fn in fns:
            td = fn(td, available)
            if td is None:
                return None
        return td
    return run


_DYNAMIC_SCHEMA_REWRITERS = {
    "execute_code": _rewrite_execute_code,
    "discord": _discord_rewriter("get_dynamic_schema_core"),
    "discord_admin": _discord_rewriter("get_dynamic_schema_admin"),
    "browser_navigate": _rewrite_browser_navigate,
    "browser_cdp": _rewrite_browser_cdp,
    "browser_exec": _compose_rewriters(_rewrite_browser_exec, _rewrite_input_tool_for_vault),
    "browser_type": _rewrite_input_tool_for_vault,
    "browser_vault_list": _rewrite_browser_vault,
    "browser_vault_fill": _rewrite_browser_vault,
    "delegate_task": _rewrite_delegate_task,
}


def _apply_dynamic_schemas(tool_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply _DYNAMIC_SCHEMA_REWRITERS in list order; the availability set is a
    snapshot taken before any rewrite (no rewriter's inputs are droppable)."""
    available = {t["function"]["name"] for t in tool_defs}
    out = []
    for td in tool_defs:
        rewrite = _DYNAMIC_SCHEMA_REWRITERS.get(td["function"]["name"])
        if rewrite is not None:
            td = rewrite(td, available)
        if td is not None:
            out.append(td)
    return out


_TOOL_SEARCH_LISTING_FORMS = {
    "full": "catalog listing embedded",
    "names": "names-only listing embedded",
    "mixed": "listing embedded (oversized servers summarized)",
    "groups": "server summary embedded (search-only discovery)",
    "none": "no listing (search-only)",
}


def _compute_tool_definitions(enabled_toolsets: Optional[List[str]] = None, disabled_toolsets: Optional[List[str]] = None,
                              quiet_mode: bool = False, skip_tool_search_assembly: bool = False) -> List[Dict[str, Any]]:
    """Uncached implementation of :func:`get_tool_definitions`."""
    tools_to_include = _select_tool_names(enabled_toolsets, disabled_toolsets, quiet_mode)
    # Selection is per schema, not per process/profile. Kanban's local checks
    # are uncached; the outer definitions cache already keys on this selection.
    from tools.kanban_toolset_context import scoped_kanban_toolset_selection
    with scoped_kanban_toolset_selection(enabled_toolsets):
        filtered_tools = _apply_dynamic_schemas(registry.get_definitions(tools_to_include, quiet=quiet_mode))
    global _last_resolved_tool_names
    _last_resolved_tool_names = [t["function"]["name"] for t in filtered_tools]

    _emit_status(f"🛠️  Final tool selection ({len(filtered_tools)} tools): {', '.join(_last_resolved_tool_names)}"
                 if filtered_tools else "🛠️  No tools selected (all filtered out or unavailable)", quiet_mode)
    # Normalize schema shapes llama.cpp's grammar converter rejects (bare
    # "type": "object", string-valued nodes from malformed MCP servers).
    try:
        from tools.schema_sanitizer import sanitize_tool_schemas
        filtered_tools = sanitize_tool_schemas(filtered_tools)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("Schema sanitization skipped: %s", e)

    # Tool Search (progressive disclosure): replace MCP/plugin tools with the
    # tool_search/describe/call bridge when the deferrable surface exceeds the
    # configured share of the context window. Core tools are never deferred.
    # Must be the LAST step (after sanitization); idempotent if called twice.
    try:
        from tools.tool_search import assemble_tool_defs, load_config as _load_ts_config
        ts_cfg = _load_ts_config()
        if not skip_tool_search_assembly and ts_cfg.enabled != "off":
            assembly = assemble_tool_defs(filtered_tools, context_length=_resolve_active_context_length(), config=ts_cfg)
            if assembly.activated:
                _emit_status(f"🔎 Tool Search (tier {assembly.tier}): {assembly.deferred_count} "
                             f"MCP/plugin tools deferred (~{assembly.deferred_tokens} tokens) behind "
                             f"tool_search/describe/call — "
                             f"{_TOOL_SEARCH_LISTING_FORMS.get(assembly.listing_form, assembly.listing_form)}.",
                             quiet_mode)
            filtered_tools = assembly.tool_defs
    except Exception as e:  # pragma: no cover — never break tool loading
        logger.warning("Tool search assembly skipped: %s", e)

    return filtered_tools


def _active_model_config() -> Tuple[str, Dict[str, Any]]:
    """(model_id, model section) from config.yaml; model_id is "" when unset."""
    from hermes_cli.config import load_config
    cfg = load_config() or {}
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    raw_model_id = model_cfg.get("model") or model_cfg.get("default") or ""
    if isinstance(raw_model_id, dict):
        from hermes_cli.config import split_model_config_default
        raw_model_id, _ = split_model_config_default(raw_model_id)
    return str(raw_model_id).strip(), model_cfg


def _resolve_active_context_length() -> int:
    """Active model's context length for the tool-search gate (0 if unresolvable).

    Order: explicit `model.context_length`; provider-aware resolution (Codex OAuth
    enforces a smaller window than the direct API for the same slug); the on-disk
    metadata cache (slightly stale is fine for picking a tier and avoids a ~200 ms
    /models probe per CLI startup); then the full live resolver.
    """
    try:
        model_id, model_cfg = _active_model_config()
        if not model_id:
            return 0
        from agent.model_metadata import get_cached_context_length, get_model_context_length
        # Honor explicit `model.context_length` in config.yaml — short-circuits the OpenRouter /models probe
        # at get_model_context_length step 0, so non-OpenRouter providers don't pay the ~2-3s OpenRouter
        # fetch at every CLI startup. See issue #46620.
        raw_ctx = model_cfg.get("context_length")
        config_ctx = raw_ctx if isinstance(raw_ctx, int) and raw_ctx > 0 else None
        provider = str(model_cfg.get("provider") or "").strip()
        base_url = str(model_cfg.get("base_url") or "").strip()
        api_key = ""
        if provider:
            # Credential resolution failing (offline, no keys) degrades to a
            # provider+base_url-only lookup so static fallbacks still apply.
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider
                rt = resolve_runtime_provider(requested=provider, target_model=model_id) or {}
                base_url = str(rt.get("base_url") or base_url or "").strip()
                api_key = str(rt.get("api_key") or "").strip()
            except Exception as rt_exc:
                logger.debug("Runtime credential resolution failed for tool-search "
                             "context gate (provider=%s): %s — using config values only", provider, rt_exc)
        if config_ctx is None and base_url:
            try:
                cached_ctx = get_cached_context_length(model_id, base_url)
                if isinstance(cached_ctx, int) and cached_ctx > 0:
                    return cached_ctx
            except Exception:
                pass
        return int(get_model_context_length(model_id, base_url=base_url, api_key=api_key,
                                            config_context_length=config_ctx, provider=provider) or 0)
    except Exception as e:
        logger.debug("Could not resolve active context length: %s", e)
        return 0


# =============================================================================
# handle_function_call  (the main dispatcher)
# =============================================================================

# Intercepted by the agent loop (need agent-level state); dispatch returns a stub error.
_AGENT_LOOP_TOOLS = {"todo_list", "memory", "session_search", "delegate_task", "agent_swarm"}

# Legacy tool-name aliases accepted at every dispatch seam (old sessions/saved
# prompts keep working); schemas advertise only new names.
_LEGACY_TOOL_ALIASES = {
    "todo": "todo_list", "cronjob": "cronjob_manage", "process": "process_manage",
    "tour": "gui_tour", "tip": "show_tip",
}
_READ_SEARCH_TOOLS = {"read_file", "search_files"}


# --- Tool error sanitization --------------------------------------------------
# Defense-in-depth: strip role tags / CDATA / code fences from exception text the
# model will read, and cap length (cap shared with tools/registry.py so text never
# passes two different caps with two different markers).
_TOOL_ERROR_STRIP_RES = (
    re.compile(r'</?(?:tool_call|function_call|result|response|output|input|system|assistant|user)>', re.IGNORECASE),
    re.compile(r'^\s*```(?:json|xml|html|markdown)?\s*', re.MULTILINE),
    re.compile(r'\s*```\s*$', re.MULTILINE),
    re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL),
)


def _sanitize_tool_error(error_msg: str) -> str:
    """Strip structural framing tokens from a tool error before the model sees it."""
    if not error_msg:
        return "[TOOL_ERROR] "
    sanitized = error_msg
    for pattern in _TOOL_ERROR_STRIP_RES:
        sanitized = pattern.sub("", sanitized)
    if len(sanitized) > _TOOL_ERROR_MAX_LEN:
        sanitized = sanitized[:_TOOL_ERROR_MAX_LEN - 3] + "..."
    return f"[TOOL_ERROR] {sanitized}"


# =========================================================================
# Tool argument key repair
# =========================================================================

def repair_tool_arg_keys(
    tool_name: str,
    args: Dict[str, Any],
    _recursive: bool = False,
    _properties: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Repair tool call argument keys to match the tool's JSON Schema.

    LLMs frequently use alternative field names (e.g. "file" instead of
    "path", "text" instead of "title").  This function maps common aliases,
    applies per-tool overrides (see ``TOOL_SPECIFIC_ALIASES``), and falls
    back to fuzzy matching so the call succeeds instead of failing with
    "unknown parameter".

    Per-tool aliases are checked first and take precedence over global
    aliases when they disagree.

    Runs *before* ``coerce_tool_args()`` so repaired keys then have their
    values coerced as usual.
    """
    if not args or not isinstance(args, dict):
        return args

    if _recursive:
        properties = _properties
    else:
        schema = registry.get_schema(tool_name)
        if not schema:
            return args
        properties = (schema.get("parameters") or {}).get("properties")

    if not properties:
        return args

    expected = set(properties.keys())
    if not expected:
        return args

    # Build a set of keys that are already correct (exact match).
    already_ok = set(args.keys()) & expected
    missing = expected - already_ok

    # For top-level calls, check whether the schema contains nested
    # objects or arrays of objects that might need recursive repair.
    has_nested_schema = False
    if not _recursive:
        has_nested_schema = any(
            (
                p.get("type") == "object" and "properties" in p
            )
            or (
                p.get("type") == "array"
                and isinstance(p.get("items"), dict)
                and p["items"].get("type") == "object"
                and "properties" in p["items"]
            )
            for p in properties.values()
        )

    if not missing and not has_nested_schema:
        return args

    # Try alias mapping for missing fields.
    # Per-tool aliases take precedence over global aliases.
    repaired = dict(args)
    used_aliases: set[str] = set()
    tool_aliases = TOOL_SPECIFIC_ALIASES.get(tool_name, {})

    for bad_key in list(repaired.keys()):
        if bad_key in expected:
            continue
        canonical = tool_aliases.get(bad_key) or TOOL_FIELD_ALIASES.get(bad_key)
        if canonical and canonical in missing:
            repaired[canonical] = repaired.pop(bad_key)
            missing.discard(canonical)
            used_aliases.add(bad_key)

    # Fuzzy match remaining missing fields against still-unmapped keys.
    remaining_bad = [k for k in repaired if k not in expected]
    if remaining_bad and missing:
        import rapidfuzz.process as _fuzz_process
        import rapidfuzz.fuzz as _fuzz
        candidates: list[tuple[float, str, str]] = []
        for miss in missing:
            if len(miss) < 4:
                continue
            cutoff = 0.75 if len(miss) >= 8 else 0.80
            score_cutoff = int(cutoff * 100)
            close = _fuzz_process.extract(
                miss, remaining_bad, limit=1, score_cutoff=score_cutoff
            )
            if close:
                matched = close[0][0]
                if len(matched) < 4:
                    continue
                ratio = _fuzz.ratio(miss, matched) / 100.0
                candidates.append((ratio, miss, matched))

        candidates.sort(key=lambda x: x[0], reverse=True)
        used_fuzzy: set[str] = set()
        for _ratio, miss, matched in candidates:
            if matched in used_fuzzy:
                continue
            used_fuzzy.add(matched)
            repaired[miss] = repaired.pop(matched)

    if not _recursive:
        _repair_nested_args(tool_name, repaired, properties)

    return repaired


def _repair_nested_args(
    tool_name: str,
    args: Dict[str, Any],
    schema_properties: Dict[str, Any],
) -> Dict[str, Any]:
    """Recursively repair field names inside nested dicts and lists of dicts.

    Walks through *args* using *schema_properties* to decide when a value
    should be treated as a nested object or an array of objects, then
    calls :func:`repair_tool_arg_keys` on each nested dict.
    """
    if not isinstance(args, dict) or not schema_properties:
        return args

    for key, value in list(args.items()):
        prop_schema = schema_properties.get(key)
        if not prop_schema:
            continue

        # Nested object with its own properties.
        if (
            isinstance(value, dict)
            and prop_schema.get("type") == "object"
            and "properties" in prop_schema
        ):
            inner_props = prop_schema["properties"]
            args[key] = repair_tool_arg_keys(
                tool_name, value, _recursive=True, _properties=inner_props
            )
            _repair_nested_args(tool_name, args[key], inner_props)

        # Array of objects with properties.
        elif isinstance(value, list) and prop_schema.get("type") == "array":
            items_schema = prop_schema.get("items", {})
            if (
                isinstance(items_schema, dict)
                and items_schema.get("type") == "object"
                and "properties" in items_schema
            ):
                inner_props = items_schema["properties"]
                new_list: list[Any] = []
                for item in value:
                    if isinstance(item, dict):
                        repaired_item = repair_tool_arg_keys(
                            tool_name, item, _recursive=True, _properties=inner_props
                        )
                        _repair_nested_args(tool_name, repaired_item, inner_props)
                        new_list.append(repaired_item)
                    else:
                        new_list.append(item)
                args[key] = new_list

    return args

@dataclass(frozen=True)
class _CallIds:
    """Identity fields of one tool call, threaded through hooks and middleware."""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    turn_id: Optional[str] = None
    api_request_id: Optional[str] = None

    def hook_kwargs(self) -> Dict[str, str]:
        """Same fields with None -> "" (hook/middleware wire contract)."""
        return {k: v or "" for k, v in asdict(self).items()}


def _tool_result_observer_fields(tool_name: str, result: Any) -> tuple[str, Optional[str], Optional[str]]:
    """Derive (status, error_type, error_message) from a tool result for observer hooks."""
    try:
        parsed_result = orjson.loads(result) if isinstance(result, str) else result
        if isinstance(parsed_result, dict) and parsed_result.get("error"):
            return "error", "tool_error", str(parsed_result.get("error"))
    except Exception:
        pass
    try:
        from agent.display import _detect_tool_failure
        failed, suffix = _detect_tool_failure(tool_name, result)
        if failed:
            return "error", "tool_error", suffix.strip().strip("[]") or None
    except Exception:
        pass
    return "ok", None, None


def _emit_post_tool_call_hook(
    *, function_name: str, function_args: Dict[str, Any], result: Any,
    task_id: Optional[str] = None, session_id: Optional[str] = None, tool_call_id: Optional[str] = None,
    turn_id: Optional[str] = None, api_request_id: Optional[str] = None, duration_ms: int = 0,
    status: Optional[str] = None, error_type: Optional[str] = None, error_message: Optional[str] = None,
    middleware_trace: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Emit the ``post_tool_call`` observer hook; gated on has_hook, and ok/error
    fields are derived from the result only past that gate when status is None."""
    if _post_tool_call_hook_suppressed.get():
        return
    try:
        from hermes_cli.lifecycle import has_hook, invoke_hook
        if not has_hook("post_tool_call"):
            return
        if status is None:
            status, error_type, error_message = _tool_result_observer_fields(function_name, result)
        invoke_hook(
            "post_tool_call", tool_name=function_name, args=function_args, result=result,
            **_CallIds(task_id, session_id, tool_call_id, turn_id, api_request_id).hook_kwargs(),
            duration_ms=duration_ms, status=status, error_type=error_type, error_message=error_message,
            middleware_trace=list(middleware_trace or []),
        )
    except Exception as _hook_err:
        logger.debug("post_tool_call hook error: %s", _hook_err)


def _dispatch_bridge_tool(function_name: str, function_args: Dict[str, Any],
                          enabled_toolsets: Optional[List[str]], disabled_toolsets: Optional[List[str]]):
    """Handle a Tool Search bridge call (tool_search / tool_describe / tool_call).

    None when *function_name* is not a bridge tool; ``(result, None)`` for a
    finished catalog read or error; ``(None, (name, args))`` when a validated
    tool_call should be re-dispatched as the real tool.
    """
    try:
        from tools import tool_search as ts
    except Exception:
        return None
    if not ts.is_bridge_tool(function_name):
        return None
    # Un-collapsed catalog scoped to the session's toolsets, so a restricted
    # session (subagent, kanban worker) can't reach the whole registry via the bridge.
    try:
        current_defs = get_tool_definitions(enabled_toolsets=enabled_toolsets, disabled_toolsets=disabled_toolsets,
                                            quiet_mode=True, skip_tool_search_assembly=True) or []
    except Exception:
        current_defs = []
    args = function_args or {}
    if function_name == ts.TOOL_SEARCH_NAME:
        return ts.dispatch_tool_search(args, current_tool_defs=current_defs), None
    if function_name == ts.TOOL_DESCRIBE_NAME:
        return ts.dispatch_tool_describe(args, current_tool_defs=current_defs), None
    underlying_name, underlying_args, err = ts.resolve_underlying_call(args)
    if err or not underlying_name:
        return tool_error(err or "tool_call could not be resolved"), None
    if underlying_name == ts.CONNECTOR_BATCH_SENTINEL:
        if not ts.connections_in_scope(current_defs):
            return tool_error("Connectors are not available in this session."), None
        return None, (underlying_name, underlying_args)
    # Defense in depth: resolve_underlying_call only checks the global
    # registry; also require membership in the session-scoped catalog.
    if underlying_name not in ts.scoped_deferrable_names(current_defs):
        return tool_error(f"'{underlying_name}' is not available in this session. "
                          "Use tool_search to find tools you can call."), None
    # Validate against the deferred tool's concrete schema — the generic
    # ``arguments: object`` bridge schema can't enforce it.
    probe_err = ts.validate_deferred_call_args(underlying_name, underlying_args)
    if probe_err is not None:
        return probe_err, None
    return None, (underlying_name, underlying_args)


def _apply_request_middleware(
    function_name: str, function_args: Dict[str, Any], ids: _CallIds, trace: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """tool_request middleware: returns (args, original_args, trace); fail-open."""
    try:
        from hermes_cli.middleware import apply_tool_request_middleware
        mw = apply_tool_request_middleware(function_name, function_args, **ids.hook_kwargs())
        return mw.payload, mw.original_payload, mw.trace
    except Exception as _mw_err:
        logger.debug("tool_request middleware error: %s", _mw_err)
        return function_args, dict(function_args), trace


def _pre_dispatch_guards(function_name: str, function_args: Dict[str, Any], skip_pre_tool_call_hook: bool,
                         ids: _CallIds, middleware_trace: List[Dict[str, Any]],
                         ) -> Tuple[Dict[str, Any], Optional[Tuple[Any, str, Optional[str]]]]:
    """Plugin pre_tool_call hook, then ACP edit approval.

    ``(args, None)`` to proceed (args possibly plugin-modified), or
    ``(args, (result, error_type, error_message))`` when blocked.
    """
    # pre_tool_call fires exactly once per execution: one invoke_hook pass yields
    # both the block message and modified args. skip=True: caller already fired it.
    if not skip_pre_tool_call_hook:
        block_message: Optional[str] = None
        try:
            from hermes_cli.plugins import _dispatch_pre_tool_call_hooks
            block_message, modified_args = _dispatch_pre_tool_call_hooks(
                function_name, function_args, middleware_trace=list(middleware_trace), **ids.hook_kwargs(),
            )
            if modified_args is not None:
                function_args = modified_args
        except Exception as _hook_err:
            logger.debug("pre_tool_call hook error: %s", _hook_err)
        if block_message is not None:
            return function_args, (tool_error(block_message), "plugin_block", block_message)

    # ACP/Zed edit approval before any file mutation. The requester is bound
    # via ContextVar only for ACP sessions, so CLI/gateway paths are unaffected.
    try:
        from acp_adapter.edit_approval import maybe_require_edit_approval
        edit_block_message = maybe_require_edit_approval(function_name, function_args)
        if edit_block_message is not None:
            return function_args, (edit_block_message, "edit_approval_denied", None)
    except Exception as _edit_approval_err:
        logger.debug("ACP edit approval guard error: %s", _edit_approval_err)
        if function_name in {"write_file", "patch"}:
            return function_args, (tool_error("Edit approval denied: approval guard failed"), "edit_approval_error", None)
    return function_args, None


@contextmanager
def _approval_observability(ids: _CallIds):
    """Bind the approval observability context (turn/tool_call/session ids) for the block."""
    try:
        from tools.approval_context import reset_current_observability_context, set_current_observability_context
        tokens = set_current_observability_context(turn_id=ids.turn_id or "", tool_call_id=ids.tool_call_id or "",
                                                   session_id=ids.session_id or "")
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            reset_current_observability_context(tokens)
        except Exception:
            pass


def _execute_tool(function_name: str, function_args: Dict[str, Any], original_args: Dict[str, Any], ids: _CallIds,
                  *, user_task: Optional[str], enabled_tools: Optional[List[str]], skip_tool_execution_middleware: bool) -> Any:
    """Run the registry handler (through tool-execution middleware unless skipped)
    with the approval observability context bound for the duration."""
    # P-054: the dispatcher must carry tool_call_id (not just task_id/session_id) so
    # terminal_tool can key its fail-open foreground live-output sink on the tool call.
    dispatch_kwargs: Dict[str, Any] = {
        "task_id": ids.task_id, "session_id": ids.session_id, "tool_call_id": ids.tool_call_id,
    }
    if function_name == "execute_code":
        # Prefer the caller's list so subagents can't overwrite the parent's
        # tool set via the process-global.
        dispatch_kwargs["enabled_tools"] = enabled_tools if enabled_tools is not None else _last_resolved_tool_names
    else:
        dispatch_kwargs["user_task"] = user_task

    def _dispatch(next_args: Dict[str, Any]) -> Any:
        from tools.connectors import dispatch_connector_call, is_connector_name
        if is_connector_name(function_name):
            return dispatch_connector_call(function_name, next_args, ids.tool_call_id)
        return registry.dispatch(function_name, next_args, **dispatch_kwargs)

    with _approval_observability(ids):
        if skip_tool_execution_middleware:
            return _dispatch(function_args)
        from hermes_cli.middleware import run_tool_execution_middleware
        return run_tool_execution_middleware(function_name, function_args, _dispatch, original_args=original_args,
                                             **ids.hook_kwargs())


def _apply_transform_tool_result_hook(function_name: str, function_args: Dict[str, Any], result: Any, duration_ms: int,
                                      ids: _CallIds) -> Any:
    """transform_tool_result: plugins may replace the final result string.

    Runs after post_tool_call and before the result enters context. Fail-open;
    first string return wins. Gated on has_hook so the no-listener path is cheap.
    """
    try:
        from hermes_cli.lifecycle import has_hook, invoke_hook
        if has_hook("transform_tool_result"):
            status, error_type, error_message = _tool_result_observer_fields(function_name, result)
            hook_results = invoke_hook("transform_tool_result", tool_name=function_name, args=function_args,
                                       result=result, **ids.hook_kwargs(), duration_ms=duration_ms,
                                       status=status, error_type=error_type, error_message=error_message)
            return next((r for r in hook_results if isinstance(r, str)), result)
    except Exception as _hook_err:
        logger.debug("transform_tool_result hook error: %s", _hook_err)
    return result


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def handle_function_call(
    function_name: str, function_args: Dict[str, Any], task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None, session_id: Optional[str] = None, turn_id: Optional[str] = None,
    api_request_id: Optional[str] = None, user_task: Optional[str] = None, enabled_tools: Optional[List[str]] = None,
    skip_pre_tool_call_hook: bool = False, skip_tool_request_middleware: bool = False,
    skip_tool_execution_middleware: bool = False, tool_request_middleware_trace: Optional[List[Dict[str, Any]]] = None,
    enabled_toolsets: Optional[List[str]] = None, disabled_toolsets: Optional[List[str]] = None,
) -> str:
    """Route a tool call through hooks/middleware to the registry; returns a JSON string.

    task_id isolates terminal/browser sessions; user_task feeds browser_snapshot.
    enabled_tools picks execute_code's sandbox tools (default: the process-global
    ``_last_resolved_tool_names``). skip_pre_tool_call_hook: caller already fired
    it (single-fire contract). enabled/disabled_toolsets scope the Tool Search
    bridge catalog to this session's grant (None = unrestricted).
    """
    # Ensure plugin tools are registered before dispatch (built-in tools
    # lazy-load through the registry on demand). Idempotent flag-check after
    # the first call, so this is free on the hot per-tool-call path.
    _ensure_discovered()
    # [CN-fork] P-013: tool arguments arrive as either a native dict (the normal
    # agent-loop path) or a JSON string (some transports hand
    # ``tool_call.arguments`` straight through). Parse a string payload once here
    # so its arguments are preserved instead of being silently dropped to ``{}``.
    if isinstance(function_args, str):
        try:
            _parsed_args = orjson.loads(function_args)
        except (ValueError, TypeError):
            _parsed_args = None
        function_args = _parsed_args if isinstance(_parsed_args, dict) else {}

    # Coerce string arguments to their schema-declared types (e.g. "42"->42)
    function_args = coerce_tool_args(function_name, function_args)
    if not isinstance(function_args, dict):
        function_args = {}
    trace = list(tool_request_middleware_trace or [])
    function_name = _LEGACY_TOOL_ALIASES.get(function_name, function_name)
    ids = _CallIds(task_id, session_id, tool_call_id, turn_id, api_request_id)
    start = time.monotonic()

    def _emit(result: Any, **extra: Any) -> Any:
        """Emit post_tool_call with this call's identity fields; returns *result*."""
        _emit_post_tool_call_hook(function_name=function_name, function_args=function_args, result=result,
                                  **asdict(ids), middleware_trace=list(trace), **extra)
        return result

    # Tool Search bridge: tool_search / tool_describe are catalog reads handled
    # inline; tool_call is unwrapped so every downstream hook (pre/post, edit
    # approval, guardrails) sees the real tool name, never the bridge.
    bridged = _dispatch_bridge_tool(function_name, function_args, enabled_toolsets, disabled_toolsets)
    if bridged is not None:
        result, underlying = bridged
        if underlying is None:
            return _emit(result, duration_ms=_elapsed_ms(start))
        from tools.connectors import CONNECTOR_BATCH_SENTINEL, dispatch_connector_batch
        if underlying[0] == CONNECTOR_BATCH_SENTINEL:
            return _emit(dispatch_connector_batch(
                underlying[1]["calls"], ids, user_task=user_task,
                enabled_tools=enabled_tools, middleware_trace=trace,
                enabled_toolsets=enabled_toolsets, disabled_toolsets=disabled_toolsets,
            ), duration_ms=_elapsed_ms(start))
        return handle_function_call(
            *underlying, **asdict(ids), user_task=user_task, enabled_tools=enabled_tools,
            skip_pre_tool_call_hook=skip_pre_tool_call_hook, skip_tool_request_middleware=skip_tool_request_middleware,
            skip_tool_execution_middleware=skip_tool_execution_middleware, tool_request_middleware_trace=list(trace),
            enabled_toolsets=enabled_toolsets, disabled_toolsets=disabled_toolsets,
        )

    from tools.connectors import is_connector_name
    from tools.connectors.gateway.names import parse_connector_name
    if function_name == "manage_connections" or is_connector_name(function_name):
        if "manage_connections" not in _select_tool_names(enabled_toolsets, disabled_toolsets, quiet_mode=True):
            return _emit(tool_error("Connectors are not available in this session."))
        if is_connector_name(function_name) and parse_connector_name(function_name) is None:
            return _emit(tool_error("Malformed connector tool name; expected connectors__<connector>__<tool>."))

    original_args = dict(function_args)
    if not skip_tool_request_middleware:
        function_args, original_args, trace = _apply_request_middleware(function_name, function_args, ids, trace)
    # [CN-fork] P-013: repair common LLM field-name drift (e.g. "file"->"path") after
    # the request-middleware seam. Middleware must see and may intentionally preserve
    # the model's original payload shape; when no middleware has rewritten the
    # request we canonicalize before hooks/dispatch so legacy observers and tool
    # handlers still receive schema field names, then re-coerce the repaired values.
    if not trace:
        repaired_args = repair_tool_arg_keys(function_name, function_args)
        if repaired_args != function_args:
            logger.info(
                "Repaired tool argument keys for %s: %s -> %s",
                function_name, list(function_args.keys()), list(repaired_args.keys()),
            )
            # Note: the callback reports top-level key changes only. Nested key
            # repairs inside objects/arrays are not reported through this hook.
            if _arg_repair_callback is not None:
                try:
                    _arg_repair_callback(
                        function_name,
                        list(function_args.keys()),
                        list(repaired_args.keys()),
                    )
                except Exception:
                    pass  # Never let callback failure break tool dispatch
            function_args = coerce_tool_args(function_name, repaired_args)

    try:
        if function_name in _AGENT_LOOP_TOOLS:
            return tool_error(f"{function_name} must be handled by the agent loop")

        function_args, blocked = _pre_dispatch_guards(function_name, function_args, skip_pre_tool_call_hook, ids, trace)
        if blocked is not None:
            result, error_type, error_message = blocked
            return _emit(result, status="blocked", error_type=error_type, error_message=error_message)

        # Any non-read/search tool resets the consecutive-read-loop counter.
        if function_name not in _READ_SEARCH_TOOLS:
            try:
                from tools.file_tools_read_tracking import notify_other_tool_call
                notify_other_tool_call(task_id or "default")
            except Exception:
                pass  # file_tools may not be loaded yet

        # duration_ms (monotonic) is exposed to post_tool_call / transform_tool_result.
        start = time.monotonic()
        result = _execute_tool(function_name, function_args, original_args, ids, user_task=user_task,
                               enabled_tools=enabled_tools, skip_tool_execution_middleware=skip_tool_execution_middleware)
        duration_ms = _elapsed_ms(start)
        _emit(result, duration_ms=duration_ms)
        return _apply_transform_tool_result_hook(function_name, function_args, result, duration_ms, ids)

    except Exception as e:
        error_msg = f"Error executing {function_name}: {str(e)}"
        logger.exception(error_msg)
        return _emit(tool_error(_sanitize_tool_error(error_msg)), duration_ms=_elapsed_ms(start),
                     status="error", error_type=type(e).__name__, error_message=str(e))


# =============================================================================
# Backward-compat wrapper functions (registry pass-throughs)
# =============================================================================

def get_all_tool_names() -> List[str]:
    _ensure_discovered()
    return registry.get_all_tool_names()


def get_toolset_for_tool(tool_name: str) -> Optional[str]:
    _ensure_discovered()
    return registry.get_toolset_for_tool(tool_name)


def get_available_toolsets() -> Dict[str, dict]:
    """Toolset availability info for UI display."""
    _ensure_discovered()
    return registry.get_available_toolsets()


def check_toolset_requirements() -> Dict[str, bool]:
    """{toolset: available_bool} for every registered toolset."""
    _ensure_discovered()
    return registry.check_toolset_requirements()


def check_tool_availability(quiet: bool = False) -> Tuple[List[str], List[dict]]:
    """(available_toolsets, unavailable_info)."""
    _ensure_discovered()
    return registry.check_tool_availability(quiet=quiet)
