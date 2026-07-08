#!/usr/bin/env python3
"""
Model Tools Module

Thin orchestration layer over the tool registry. Each tool file in tools/
self-registers its schema, handler, and metadata via tools.registry.register().
This module triggers discovery (by importing all tool modules), then provides
the public API that run_agent.py, cli.py, batch_runner.py, and the RL
environments consume.

Public API (signatures preserved from the original 2,400-line version):
    get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode) -> list
    handle_function_call(function_name, function_args, task_id, user_task) -> str
    TOOL_TO_TOOLSET_MAP: dict          (for batch_runner.py)
    TOOLSET_REQUIREMENTS: dict         (for cli.py, doctor.py)
    get_all_tool_names() -> list
    get_toolset_for_tool(name) -> str
    get_available_toolsets() -> dict
    check_toolset_requirements() -> dict
    check_tool_availability(quiet) -> tuple
"""

import os
import orjson
import re
import asyncio
import logging
import threading
import time
from typing import Dict, Any, List, Optional, Set, Tuple, Callable

from tools.registry import registry
from toolsets import resolve_toolset, validate_toolset

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

# Tracks platform-bundle names already flagged in disabled_toolsets so the
# advisory (#33924) is logged once per name, not on every tool recompute.
_WARNED_DISABLED_BUNDLES: set = set()


# =============================================================================
# Async Bridging  (single source of truth -- used by registry.dispatch too)
# =============================================================================

_tool_loop = None          # persistent loop for the main (CLI) thread
_tool_loop_lock = threading.Lock()
_worker_thread_local = threading.local()  # per-worker-thread persistent loops


def _get_tool_loop():
    """Return a long-lived event loop for running async tool handlers.

    Using a persistent loop (instead of asyncio.run() which creates and
    *closes* a fresh loop every time) prevents "Event loop is closed"
    errors that occur when cached httpx/AsyncOpenAI clients attempt to
    close their transport on a dead loop during garbage collection.
    """
    global _tool_loop
    with _tool_loop_lock:
        if _tool_loop is None or _tool_loop.is_closed():
            _tool_loop = asyncio.new_event_loop()
        return _tool_loop


def _get_worker_loop():
    """Return a persistent event loop for the current worker thread.

    Each worker thread (e.g., delegate_task's ThreadPoolExecutor threads)
    gets its own long-lived loop stored in thread-local storage.  This
    prevents the "Event loop is closed" errors that occurred when
    asyncio.run() was used per-call: asyncio.run() creates a loop, runs
    the coroutine, then *closes* the loop — but cached httpx/AsyncOpenAI
    clients remain bound to that now-dead loop and raise RuntimeError
    during garbage collection or subsequent use.

    By keeping the loop alive for the thread's lifetime, cached clients
    stay valid and their cleanup runs on a live loop.
    """
    loop = getattr(_worker_thread_local, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _worker_thread_local.loop = loop
    return loop


def _run_async(coro):
    """Run an async coroutine from a sync context.

    If the current thread already has a running event loop (e.g., inside
    the gateway's async stack or Atropos's event loop), we spin up a
    disposable thread so asyncio.run() can create its own loop without
    conflicting.

    For the common CLI path (no running loop), we use a persistent event
    loop so that cached async clients (httpx / AsyncOpenAI) remain bound
    to a live loop and don't trigger "Event loop is closed" on GC.

    When called from a worker thread (parallel tool execution), we use a
    per-thread persistent loop to avoid both contention with the main
    thread's shared loop AND the "Event loop is closed" errors caused by
    asyncio.run()'s create-and-destroy lifecycle.

    This is the single source of truth for sync->async bridging in tool
    handlers. Each handler is self-protecting via this function.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Inside an async context (gateway, RL env) — run in a fresh thread
        # with its own event loop we own a reference to, so on timeout we
        # can cancel the task inside that loop (ThreadPoolExecutor.cancel()
        # only works on not-yet-started futures — it's a no-op on a running
        # worker, which previously leaked the thread on every 300 s timeout).
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
                try:
                    # Cancel anything still pending (e.g. task cancelled
                    # externally via call_soon_threadsafe on timeout).
                    pending = asyncio.all_tasks(worker_loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        worker_loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass
                worker_loop.close()

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Carry the active profile + approval/sudo callbacks into the worker so
        # async tools resolve get_hermes_home() under the active profile.
        from tools.thread_context import propagate_context_to_thread

        future = pool.submit(propagate_context_to_thread(_run_in_worker))
        try:
            return future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            # Cancel the coroutine inside its own loop so the worker thread
            # can wind down instead of running forever.
            if loop_ready.wait(timeout=1.0) and worker_loop is not None:
                try:
                    for t in asyncio.all_tasks(worker_loop):
                        worker_loop.call_soon_threadsafe(t.cancel)
                except RuntimeError:
                    # Loop already closed — nothing to cancel.
                    pass
            raise
        finally:
            # wait=False: don't block the caller on a stuck coroutine. We've
            # already requested cancellation above; the worker will exit
            # once the coroutine observes it (usually at the next await).
            pool.shutdown(wait=False)

    # If we're on a worker thread (e.g., parallel tool execution in
    # delegate_task), use a per-thread persistent loop.  This avoids
    # contention with the main thread's shared loop while keeping cached
    # httpx/AsyncOpenAI clients bound to a live loop for the thread's
    # lifetime — preventing "Event loop is closed" on GC cleanup.
    if threading.current_thread() is not threading.main_thread():
        worker_loop = _get_worker_loop()
        return worker_loop.run_until_complete(coro)

    tool_loop = _get_tool_loop()
    return tool_loop.run_until_complete(coro)


# =============================================================================
# Tool Discovery  (deferred — see the lazy index in tools/registry.py)
# =============================================================================
#
# Historically this module imported EVERY tools/*.py at import time (via
# discover_builtin_tools()) plus ran plugin discovery — adding ~900 ms to the
# ``from run_agent import AIAgent`` import cascade even for an agent that ends
# up touching a handful of tools. Both are now deferred:
#
#   * Built-in tools: the registry keeps a statically-scanned metadata index
#     (tool name -> module) and imports a tool's module only when that tool is
#     first requested. We just flip the singleton into lazy mode here — cheap
#     (no AST scan, no imports); the scan happens on first real use and is
#     itself disk-cached.
#   * Plugins: discover_plugins() is idempotent and already invoked explicitly
#     at every real entry point (cli, gateway, cron, acp, tui, oneshot...). We
#     run it lazily on the first API call that needs the full picture instead
#     of as an unconditional import side effect.
#
# Aggregate queries (get_all_tool_names, TOOL_TO_TOOLSET_MAP, doctor/banner
# availability checks, the default all-toolsets get_tool_definitions()) still
# transparently load the whole catalog, so nothing that previously saw every
# tool loses visibility — it just pays on first use, not on import.

registry.enable_lazy_builtins()

# MCP tool discovery (external MCP servers from config) used to run here as
# a module-level side effect.  It was removed because discover_mcp_tools()
# internally uses a blocking future.result(timeout=120) wait, and the
# gateway lazy-imports this module from inside the asyncio event loop on
# the first user message — freezing Discord/Telegram heartbeats for up to
# 120s whenever any configured MCP server was slow or unreachable (#16856).
#
# Each entry point now runs discovery explicitly at its own startup:
#   - gateway/run.py            -> start_gateway() uses run_in_executor
#   - cli.py, hermes_cli/*      -> inline on startup (no event loop)
#   - tui_gateway/server.py     -> inline on startup (no event loop)
#   - acp_adapter/server.py     -> asyncio.to_thread on session init

_discovery_plugins_done = False
_discovery_lock = threading.RLock()


def _ensure_discovered(need_plugins: bool = True) -> None:
    """Idempotently complete deferred tool discovery.

    The built-in lazy index is enabled at import, so this only has to run
    plugin discovery — and only when a caller needs plugin-contributed
    tools/toolsets (skipped for the explicit ``enabled_toolsets=[]`` fast
    path). ``discover_plugins()`` is itself idempotent, so a racing or
    duplicate call is harmless.
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


# =============================================================================
# Backward-compat constants  (lazily materialized on first access)
# =============================================================================
#
# ``TOOL_TO_TOOLSET_MAP`` and ``TOOLSET_REQUIREMENTS`` used to be module-level
# dicts built eagerly right after discovery. Building them now would force the
# entire tool catalog to import at module-import time — exactly what the lazy
# design avoids. They are exposed via module ``__getattr__`` (PEP 562) so
# ``from model_tools import TOOL_TO_TOOLSET_MAP`` still works and returns a
# complete map, but only callers that actually read them pay the cost.


def __getattr__(name: str):
    if name == "TOOL_TO_TOOLSET_MAP":
        _ensure_discovered()
        return registry.get_tool_to_toolset_map()
    if name == "TOOLSET_REQUIREMENTS":
        _ensure_discovered()
        return registry.get_toolset_requirements()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Resolved tool names from the last get_tool_definitions() call.
# Used by code_execution_tool to know which tools are available in this session.
_last_resolved_tool_names: List[str] = []


# =============================================================================
# Legacy toolset name mapping  (old _tools-suffixed names -> tool name lists)
# =============================================================================

_LEGACY_TOOLSET_MAP = {
    "web_tools": ["web_search", "web_extract"],
    "terminal_tools": ["terminal"],
    "vision_tools": ["vision_analyze"],
    "image_tools": ["image_generate"],
    "skills_tools": ["skills_list", "skill_view", "skill_manage"],
    "browser_tools": [
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_type", "browser_scroll", "browser_back",
        "browser_press", "browser_get_images",
        "browser_vision", "browser_console"
    ],
    "cronjob_tools": ["cronjob"],
    "file_tools": ["read_file", "write_file", "patch", "search_files"],
    "tts_tools": ["text_to_speech"],
}


# =============================================================================
# get_tool_definitions  (the main schema provider)
# =============================================================================

# Module-level memoization for get_tool_definitions(). Keyed on
# (frozenset(enabled_toolsets), frozenset(disabled_toolsets), registry._generation,
#  config fingerprint, kanban flag, tool-search flag, shell type). Hot callers
# (gateway runner, AIAgent.__init__, the CLI background warmup) invoke this on
# every agent construction; caching avoids ~12 ms of registry walking + schema
# filtering + check_fn probing per call.
#
# Active in BOTH quiet and non-quiet modes. quiet_mode is deliberately NOT part
# of the key: the computed schema list is identical regardless of it — only the
# stdout side effect (tool-selection status lines) differs. We cache the
# (schema_list, status_lines) pair and replay the status lines on a non-quiet
# hit, so the CLI/TUI path is memoized too instead of rebuilding the whole
# catalog on every construction.
#
# Invalidation happens transparently via the registry's _generation counter,
# which bumps on register() / deregister() / register_toolset_alias(). The
# inner check_fn TTL cache in registry.py handles environment drift (Docker
# daemon start/stop, env var changes, etc.) on a 30 s horizon.
_tool_defs_cache: Dict[tuple, "tuple[List[Dict[str, Any]], List[str]]"] = {}

# Guards _tool_defs_cache against concurrent mutation. The CLI/gateway now
# pre-warm tool definitions from a background thread while the main thread may
# build the first agent, so the cache can be read and written from two threads
# at once. The lock keeps the check → (compute) → store → LRU-evict sequence
# from corrupting the dict ("dict changed size during iteration" on eviction).
# A reentrant lock is used so _clear_tool_defs_cache() can be called while held.
_tool_defs_cache_lock = threading.RLock()

# Hard cap on memoized get_tool_definitions() results. A long-lived Gateway
# process sees many distinct toolset/config fingerprints over its lifetime
# (per-session toolset sets, config edits, kanban-task toggles); without a
# bound the cache grows unboundedly. 8 comfortably covers the warm working
# set (the handful of distinct platform/toolset combos a gateway actually
# serves) while keeping the cap small. (#19251)
_TOOL_DEFS_CACHE_MAX = 8


def _clear_tool_defs_cache() -> None:
    """Drop memoized get_tool_definitions() results. Called when dynamic
    schema dependencies change (e.g. discord capability cache reset,
    execute_code sandbox reconfigured)."""
    with _tool_defs_cache_lock:
        _tool_defs_cache.clear()


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
        # Plugin discovery is only needed when the selection may include
        # plugin-contributed tools (mirrors get_tool_definitions()).
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


def get_tool_definitions(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    quiet_mode: bool = False,
    skip_tool_search_assembly: bool = False,
) -> List[Dict[str, Any]]:
    """
    Get tool definitions for model API calls with toolset-based filtering.

    All tools must be part of a toolset to be accessible.

    Args:
        enabled_toolsets: Only include tools from these toolsets.
        disabled_toolsets: Exclude tools from these toolsets (if enabled_toolsets is None).
        quiet_mode: Suppress status prints.
        skip_tool_search_assembly: When True, return the pre-assembly tool list
            (raw schemas for every enabled tool). Used internally by the
            tool_search / tool_describe bridge handlers so they can read the
            real catalog, not the already-collapsed one. Public callers should
            leave this False.

    Returns:
        Filtered list of OpenAI-format tool definitions.
    """
    # Complete deferred discovery before resolving toolsets. The explicit
    # empty-toolset case resolves to zero tools, so it needn't pay for plugin
    # discovery (keeps its <50 ms fast-path). A non-empty or default (None)
    # selection may include plugin-contributed tools, so run it there.
    _ensure_discovered(need_plugins=(enabled_toolsets is None or bool(enabled_toolsets)))

    # Process-wide memoization — active in BOTH quiet and non-quiet modes.
    # The cache key captures every argument-level input; the registry
    # generation captures registry mutations (MCP refresh, plugin load).
    # check_fn results are TTL-cached one level down, inside
    # registry.get_definitions. The config-mtime fingerprint captures
    # user-visible config edits that affect dynamic schemas (execute_code
    # mode, discord action allowlist, etc.) without needing an explicit
    # invalidate hook on every config-writer. The shell type is included so a
    # mid-session auto-install of pwsh (which changes the terminal tool's
    # dynamic description) invalidates the cached definitions.
    global _last_resolved_tool_names

    def _cache_key() -> tuple:
        try:
            from hermes_cli.config import get_config_path
            cfg_stat = get_config_path().stat()
            cfg_fp = (cfg_stat.st_mtime_ns, cfg_stat.st_size)
        except (FileNotFoundError, OSError, ImportError):
            cfg_fp = None
        try:
            from tools.terminal_tool import _detect_shell_for_description
            _shell_fp = _detect_shell_for_description()
        except Exception:
            _shell_fp = "bash"
        return (
            frozenset(enabled_toolsets) if enabled_toolsets is not None else None,
            frozenset(disabled_toolsets) if disabled_toolsets else None,
            registry._generation,
            cfg_fp,
            bool(os.environ.get("HERMES_KANBAN_TASK")),
            bool(skip_tool_search_assembly),
            _shell_fp,
        )

    lookup_key = _cache_key()
    with _tool_defs_cache_lock:
        cached = _tool_defs_cache.get(lookup_key)
    if cached is not None:
        cached_result, cached_status = cached
        # Keep _last_resolved_tool_names consistent even on a cache hit.
        _last_resolved_tool_names = [t["function"]["name"] for t in cached_result]
        # Replay the tool-selection status lines for non-quiet callers so the
        # CLI still shows them on a cache hit (they were captured, not printed,
        # during the original compute).
        if not quiet_mode and cached_status:
            print("\n".join(cached_status))
        # Hand callers a shallow copy so downstream mutations (run_agent
        # appending memory/LCM tool schemas to self.tools) don't poison the
        # cache; the schema dicts themselves are treated as read-only. (#17335)
        return list(cached_result)

    result, status_lines = _compute_tool_definitions(
        enabled_toolsets, disabled_toolsets, quiet_mode,
        skip_tool_search_assembly=skip_tool_search_assembly,
    )
    if not quiet_mode and status_lines:
        print("\n".join(status_lines))

    # Re-derive the key AFTER compute. Resolving a toolset selection for the
    # first time lazily imports its tool modules, and every register() bumps
    # registry._generation — so the generation captured before compute is
    # already stale by the time we store. Keying the stored entry on the
    # post-compute (settled) generation lets the very NEXT call hit the cache
    # instead of paying a second full rebuild; this is what makes the "second
    # agent init" effectively free rather than a ~12 ms recompute. Any genuine
    # later mutation still bumps the generation and invalidates correctly.
    store_key = _cache_key()
    with _tool_defs_cache_lock:
        # Bound the cache with LRU eviction so a long-lived Gateway process
        # doesn't accumulate entries unboundedly across the many distinct
        # toolset/config fingerprints it sees over its lifetime (#19251).
        if store_key not in _tool_defs_cache and len(_tool_defs_cache) >= _TOOL_DEFS_CACHE_MAX:
            _tool_defs_cache.pop(next(iter(_tool_defs_cache)))  # evict oldest
        _tool_defs_cache[store_key] = (result, status_lines)
    return list(result)


def _compute_tool_definitions(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    quiet_mode: bool = False,
    skip_tool_search_assembly: bool = False,
) -> "tuple[List[Dict[str, Any]], List[str]]":
    """Uncached implementation of :func:`get_tool_definitions`.

    Returns ``(tool_defs, status_lines)``. The status lines are the
    human-readable tool-selection messages (``✅ Enabled toolset ...``,
    ``🛠️  Final tool selection ...``) collected here but NOT printed — the
    caller emits them for non-quiet callers and caches them so a later
    non-quiet cache hit can replay them. Keeping this side effect out of the
    computation is what lets one memo entry serve quiet and non-quiet callers.
    """
    # Determine which tool names the caller wants
    status_lines: List[str] = []
    tools_to_include: set = set()

    if enabled_toolsets is not None:
        effective_enabled_toolsets = list(enabled_toolsets)
        if os.environ.get("HERMES_KANBAN_TASK") and "kanban" not in effective_enabled_toolsets:
            # Dispatcher-spawned workers are scoped by HERMES_KANBAN_TASK and
            # must always receive the lifecycle handoff tools. Assignee
            # profiles may intentionally restrict their normal chat toolsets
            # (for token/cost reasons), but that should not strip the kanban
            # worker's completion/block/heartbeat surface.
            effective_enabled_toolsets.append("kanban")
        for toolset_name in effective_enabled_toolsets:
            if validate_toolset(toolset_name):
                resolved = resolve_toolset(toolset_name)
                tools_to_include.update(resolved)
                status_lines.append(f"✅ Enabled toolset '{toolset_name}': {', '.join(resolved) if resolved else 'no tools'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.update(legacy_tools)
                status_lines.append(f"✅ Enabled legacy toolset '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                status_lines.append(f"⚠️  Unknown toolset: {toolset_name}")
    else:
        # Default: start with everything
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

    # Always apply disabled toolsets as a subtraction step at the end.
    # This ensures that even if a composite toolset (like hermes-cli)
    # is enabled, any tools belonging to a disabled toolset are strictly
    # stripped out. See issue #17309.
    if disabled_toolsets:
        for toolset_name in disabled_toolsets:
            if validate_toolset(toolset_name):
                if toolset_name.startswith("hermes-"):
                    # Platform bundles (hermes-*) include _HERMES_CORE_TOOLS, so
                    # subtracting the whole bundle would strip core tools shared
                    # by other enabled toolsets and empty the tool list (#33924).
                    # Subtract only the bundle's non-core delta; keep core.
                    from toolsets import bundle_non_core_tools
                    to_remove = bundle_non_core_tools(toolset_name)
                    tools_to_include.difference_update(to_remove)
                    resolved = sorted(to_remove)
                    if toolset_name not in _WARNED_DISABLED_BUNDLES:
                        _WARNED_DISABLED_BUNDLES.add(toolset_name)
                        logger.info(
                            "agent.disabled_toolsets contains platform-bundle "
                            "name '%s'; core tools are preserved and only its "
                            "platform-specific tools (%s) are removed. Bundle "
                            "names usually belong in `toolsets:`, not "
                            "`disabled_toolsets` (#33924).",
                            toolset_name,
                            ", ".join(resolved) if resolved else "none",
                        )
                else:
                    resolved = resolve_toolset(toolset_name)
                    tools_to_include.difference_update(resolved)
                status_lines.append(f"🚫 Disabled toolset '{toolset_name}': {', '.join(resolved) if resolved else 'no tools'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.difference_update(legacy_tools)
                status_lines.append(f"🚫 Disabled legacy toolset '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                status_lines.append(f"⚠️  Unknown toolset: {toolset_name}")

    # Plugin-registered tools are now resolved through the normal toolset
    # path — validate_toolset() / resolve_toolset() / get_all_toolsets()
    # all check the tool registry for plugin-provided toolsets.  No bypass
    # needed; plugins respect enabled_toolsets / disabled_toolsets like any
    # other toolset.

    # Ask the registry for schemas (only returns tools whose check_fn passes)
    filtered_tools = registry.get_definitions(tools_to_include, quiet=quiet_mode)

    # The set of tool names that actually passed check_fn filtering.
    # Use this (not tools_to_include) for any downstream schema that references
    # other tools by name — otherwise the model sees tools mentioned in
    # descriptions that don't actually exist, and hallucinates calls to them.
    available_tool_names = {t["function"]["name"] for t in filtered_tools}

    # Rebuild execute_code schema to only list sandbox tools that are actually
    # available.  Without this, the model sees "web_search is available in
    # execute_code" even when the API key isn't configured or the toolset is
    # disabled (#560-discord).
    if "execute_code" in available_tool_names:
        from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema, _get_execution_mode
        sandbox_enabled = SANDBOX_ALLOWED_TOOLS & available_tool_names
        dynamic_schema = build_execute_code_schema(sandbox_enabled, mode=_get_execution_mode())
        for i, td in enumerate(filtered_tools):
            if td.get("function", {}).get("name") == "execute_code":
                filtered_tools[i] = {"type": "function", "function": dynamic_schema}
                break

    # Rebuild discord / discord_admin schemas based on the bot's privileged
    # intents (detected from GET /applications/@me) and the user's action
    # allowlist in config.  Hides actions the bot's intents don't support so
    # the model never attempts them, and annotates fetch_messages when the
    # MESSAGE_CONTENT intent is missing.
    _discord_schema_fns = {
        "discord": "get_dynamic_schema_core",
        "discord_admin": "get_dynamic_schema_admin",
    }
    for discord_tool_name in _discord_schema_fns:
        if discord_tool_name in available_tool_names:
            try:
                from tools import discord_tool as _dt
                schema_fn = getattr(_dt, _discord_schema_fns[discord_tool_name])
                dynamic = schema_fn()
            except Exception:
                dynamic = None
            if dynamic is None:
                filtered_tools = [
                    t for t in filtered_tools
                    if t.get("function", {}).get("name") != discord_tool_name
                ]
                available_tool_names.discard(discord_tool_name)
            else:
                for i, td in enumerate(filtered_tools):
                    if td.get("function", {}).get("name") == discord_tool_name:
                        filtered_tools[i] = {"type": "function", "function": dynamic}
                        break

    # Strip web tool cross-references from browser_navigate description when
    # web_search / web_extract are not available.  The static schema says
    # "prefer web_search or web_extract" which causes the model to hallucinate
    # those tools when they're missing.
    if "browser_navigate" in available_tool_names:
        web_tools_available = {"web_search", "web_extract"} & available_tool_names
        if not web_tools_available:
            for i, td in enumerate(filtered_tools):
                if td.get("function", {}).get("name") == "browser_navigate":
                    desc = td["function"].get("description", "")
                    desc = desc.replace(
                        " For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
                        "",
                    )
                    filtered_tools[i] = {
                        "type": "function",
                        "function": {**td["function"], "description": desc},
                    }
                    break

    if filtered_tools:
        tool_names = [t["function"]["name"] for t in filtered_tools]
        status_lines.append(f"🛠️  Final tool selection ({len(filtered_tools)} tools): {', '.join(tool_names)}")
    else:
        status_lines.append("🛠️  No tools selected (all filtered out or unavailable)")

    global _last_resolved_tool_names
    _last_resolved_tool_names = [t["function"]["name"] for t in filtered_tools]

    # Sanitize schemas for broad backend compatibility. llama.cpp's
    # json-schema-to-grammar converter (used by its OAI server to build
    # GBNF tool-call parsers) rejects some shapes that cloud providers
    # silently accept — bare "type": "object" with no properties,
    # string-valued schema nodes from malformed MCP servers, etc. This
    # is a no-op for schemas that are already well-formed.
    try:
        from tools.schema_sanitizer import sanitize_tool_schemas
        filtered_tools = sanitize_tool_schemas(filtered_tools)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("Schema sanitization skipped: %s", e)

    # ── Tool Search (progressive disclosure) ────────────────────────────
    # Conditionally replace MCP + plugin (non-core) tools with three bridge
    # tools (tool_search / tool_describe / tool_call) when the deferrable
    # surface exceeds the configured threshold (default 10% of context
    # window). Core Hermes tools (toolsets._HERMES_CORE_TOOLS) are NEVER
    # deferred. See tools/tool_search.py for full design notes.
    #
    # This is deliberately the last step before returning — sanitization
    # has already normalized schemas, and the assembly is idempotent in
    # case some caller invokes get_tool_definitions twice.
    try:
        from tools.tool_search import assemble_tool_defs, load_config as _load_ts_config
        ts_cfg = _load_ts_config()
        if not skip_tool_search_assembly and ts_cfg.enabled != "off":
            context_length = _resolve_active_context_length()
            assembly = assemble_tool_defs(
                filtered_tools,
                context_length=context_length,
                config=ts_cfg,
            )
            if assembly.activated:
                status_lines.append(
                    f"🔎 Tool Search: {assembly.deferred_count} MCP/plugin tools deferred "
                    f"(~{assembly.deferred_tokens} tokens) behind tool_search/describe/call. "
                    f"Threshold ~{assembly.threshold_tokens} tokens."
                )
            filtered_tools = assembly.tool_defs
    except Exception as e:  # pragma: no cover — never break tool loading
        logger.warning("Tool search assembly skipped: %s", e)

    return filtered_tools, status_lines


def _resolve_active_context_length() -> int:
    """Look up the active model's context length for the tool-search gate.

    Returns 0 when the model can't be resolved — ``should_activate`` falls
    back to a fixed token cutoff in that case.
    """
    try:
        from hermes_cli.config import load_config as _load
        cfg = _load() or {}
        model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        model_id = (model_cfg.get("model") or model_cfg.get("default") or "").strip()
        if not model_id:
            return 0
        from agent.model_metadata import get_model_context_length
        return int(get_model_context_length(model_id) or 0)
    except Exception as e:
        logger.debug("Could not resolve active context length: %s", e)
        return 0


# =============================================================================
# handle_function_call  (the main dispatcher)
# =============================================================================

# Tools whose execution is intercepted by the agent loop (run_agent.py)
# because they need agent-level state (TodoStore, MemoryStore, etc.).
# The registry still holds their schemas; dispatch just returns a stub error
# so if something slips through, the LLM sees a sensible message.
_AGENT_LOOP_TOOLS = {"todo", "memory", "session_search", "delegate_task"}
_READ_SEARCH_TOOLS = {"read_file", "search_files"}


# =========================================================================
# Tool error sanitization
# =========================================================================
#
# Tool exceptions can carry arbitrary text into the model's context as the
# `tool` message content. orjson.dumps().decode('utf-8') handles quote/backslash escaping so a
# raw injection of `</tool_call>` won't break message framing, but the model
# still *reads* those tokens and they can confuse downstream tool-call
# parsing or, in adversarial cases, nudge it toward role-confusion framing.
#
# This helper strips structural framing tokens (XML role tags, CDATA,
# markdown code fences) and caps the message at a sane upper bound before it
# becomes part of the conversation. It's defense-in-depth — the json layer
# already prevents framing escape — but cheap and worth having.
#
# Ported from ironclaw#1639.
_TOOL_ERROR_ROLE_TAG_RE = re.compile(
    r'</?(?:tool_call|function_call|result|response|output|input|system|assistant|user)>',
    re.IGNORECASE,
)
_TOOL_ERROR_FENCE_OPEN_RE = re.compile(r'^\s*```(?:json|xml|html|markdown)?\s*', re.MULTILINE)
_TOOL_ERROR_FENCE_CLOSE_RE = re.compile(r'\s*```\s*$', re.MULTILINE)
_TOOL_ERROR_CDATA_RE = re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL)
_TOOL_ERROR_MAX_LEN = 2000


def _sanitize_tool_error(error_msg: str) -> str:
    """Strip structural framing tokens from a tool error before showing it to the model.

    See _TOOL_ERROR_ROLE_TAG_RE docstring above for rationale.
    """
    if not error_msg:
        return "[TOOL_ERROR] "
    sanitized = _TOOL_ERROR_ROLE_TAG_RE.sub("", error_msg)
    sanitized = _TOOL_ERROR_FENCE_OPEN_RE.sub("", sanitized)
    sanitized = _TOOL_ERROR_FENCE_CLOSE_RE.sub("", sanitized)
    sanitized = _TOOL_ERROR_CDATA_RE.sub("", sanitized)
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
        import difflib
        candidates: list[tuple[float, str, str]] = []
        for miss in missing:
            if len(miss) < 4:
                continue
            cutoff = 0.75 if len(miss) >= 8 else 0.80
            close = difflib.get_close_matches(
                miss, remaining_bad, n=1, cutoff=cutoff
            )
            if close:
                matched = close[0]
                if len(matched) < 4:
                    continue
                ratio = difflib.SequenceMatcher(None, miss, matched).ratio()
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


# =========================================================================
# Tool argument type coercion
# =========================================================================

def _value_matches_schema_type(value: Any, prop_schema: dict) -> bool:
    """Return True when *value* already satisfies *prop_schema*'s type so that
    :func:`coerce_tool_args` would leave it untouched.

    Mirrors the coercion rules exactly, so a True result GUARANTEES coercion is
    a no-op for this value.  The fast path in :func:`handle_function_call`
    depends on that guarantee — a false positive would skip a needed
    conversion — so anything ambiguous returns False (the value then takes the
    normal, coercing path).
    """
    expected = prop_schema.get("type")

    # Array fields: coerce_tool_args wraps any non-list value (and JSON-decodes
    # list-shaped strings).  Only an actual list/tuple — or None, which is
    # preserved — is a genuine no-op.
    if expected == "array":
        return value is None or isinstance(value, (list, tuple))

    # Non-string values are never coerced for non-array fields.
    if not isinstance(value, str):
        return True

    # value is a string from here on.  A nullable field rewrites the literal
    # "null" to None, so that is not a no-op.
    if _schema_allows_null(prop_schema) and value.strip().lower() == "null":
        return False

    # No declared type (and not the nullable-"null" case handled above) means
    # coerce_tool_args leaves the string as-is.
    if not expected:
        return True

    # A string survives unchanged only when the schema wants a string.  Scalar
    # types (integer/number/boolean/object) and union types may rewrite it, so
    # treat those conservatively as "not a no-op".
    return expected == "string"


def _prop_needs_nested_repair(prop_schema: dict) -> bool:
    """True when a property is an object (or array-of-objects) that carries its
    own ``properties`` — the shape ``repair_tool_arg_keys`` recurses into.

    When any property in a schema has this shape, correct top-level keys are no
    longer enough to prove repair is a no-op, so the exact-match fast path must
    fall back to the full pipeline.
    """
    if not isinstance(prop_schema, dict):
        return False
    if prop_schema.get("type") == "object" and "properties" in prop_schema:
        return True
    if prop_schema.get("type") == "array":
        items = prop_schema.get("items")
        if (
            isinstance(items, dict)
            and items.get("type") == "object"
            and "properties" in items
        ):
            return True
    return False


def _args_match_schema_exactly(args: Any, schema: Optional[dict]) -> bool:
    """Return True when *args* already match *schema* so that BOTH
    ``repair_tool_arg_keys`` and ``coerce_tool_args`` are guaranteed no-ops.

    Sufficient conditions (all required):

      * ``args`` is a dict.
      * The schema declares no nested object / array-of-objects property (no
        recursive repair can trigger).
      * Every key present is a declared property (no aliasing / fuzzy repair).
      * Every value already matches its declared type (no coercion needed).

    A True result lets :func:`handle_function_call` skip the argument-repair
    pipeline entirely on the hot dispatch path.  Conservative by construction:
    any uncertainty yields False, which only costs a (correct) slow path.
    """
    if not isinstance(args, dict) or not schema:
        return False
    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        # No declared properties: repair/coerce cannot act, but only treat
        # genuinely-empty args as an exact match to stay conservative.
        return not args
    # If ANY property is a nested object/array-of-objects, repair may recurse
    # even when the top-level keys are correct — bail to the slow path.
    for prop in properties.values():
        if _prop_needs_nested_repair(prop):
            return False
    for key, value in args.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            return False
        if not _value_matches_schema_type(value, prop):
            return False
    return True


def coerce_tool_args(
    tool_name: str,
    args: Dict[str, Any],
    schema: Optional[dict] = None,
) -> Dict[str, Any]:
    """Coerce tool call arguments to match their JSON Schema types.

    LLMs frequently return numbers as strings (``"42"`` instead of ``42``)
    and booleans as strings (``"true"`` instead of ``true``).  This compares
    each argument value against the tool's registered JSON Schema and attempts
    safe coercion when the value is a string but the schema expects a different
    type.  Original values are preserved when coercion fails.

    Handles ``"type": "integer"``, ``"type": "number"``, ``"type": "boolean"``,
    and union types (``"type": ["integer", "string"]``).

    Also wraps bare scalar values in a single-element list when the schema
    declares ``"type": "array"``.  Open-weight models (DeepSeek, Qwen, GLM)
    sometimes emit ``{"urls": "https://a.com"}`` when the tool expects
    ``{"urls": ["https://a.com"]}``; wrapping here avoids a confusing tool
    failure on what is otherwise a well-formed call.
    """
    if not args or not isinstance(args, dict):
        return args

    schema = registry.get_schema(tool_name)
    if not schema:
        return args

    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args

    for key, value in list(args.items()):
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")

        # Wrap bare non-list values when the schema declares ``array``.
        # Strings still go through _coerce_value first so JSON-encoded
        # arrays (``'["a","b"]'``) get parsed and nullable ``"null"``
        # becomes ``None`` rather than ``["null"]``.
        # ``None`` itself is preserved — we don't know whether the model
        # meant "omit" or "empty list", and tools with sensible defaults
        # (e.g. read_file's normalize_read_pagination) already handle it.
        if expected == "array" and value is not None and not isinstance(value, (list, tuple)):
            if isinstance(value, str):
                coerced = _coerce_value(value, expected, schema=prop_schema)
                if coerced is not value:
                    # _coerce_value handled it (JSON-parsed list or
                    # nullable "null" → None).
                    args[key] = coerced
                    continue
                # If the string looks like a JSON array but _coerce_value
                # failed to parse it, warn clearly instead of silently wrapping.
                if value.strip().startswith("["):
                    logger.warning(
                        "coerce_tool_args: %s.%s looks like a JSON array string "
                        "but could not be parsed — model may have emitted a "
                        "JSON-encoded string instead of a native array. "
                        "Falling back to single-element list.",
                        tool_name, key,
                    )
                args[key] = [value]
                logger.info(
                    "coerce_tool_args: wrapped bare string in list for %s.%s",
                    tool_name, key,
                )
                continue
            args[key] = [value]
            logger.info(
                "coerce_tool_args: wrapped bare %s in list for %s.%s",
                type(value).__name__, tool_name, key,
            )
            continue

        if not isinstance(value, str):
            continue
        if not expected and not _schema_allows_null(prop_schema):
            continue
        coerced = _coerce_value(value, expected, schema=prop_schema)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type, schema: dict | None = None):
    """Attempt to coerce a string *value* to *expected_type*.

    Returns the original string when coercion is not applicable or fails.
    """
    if _schema_allows_null(schema) and value.strip().lower() == "null":
        return None

    if isinstance(expected_type, list):
        # Union type — try each in order, return first successful coercion
        for t in expected_type:
            result = _coerce_value(value, t, schema=schema)
            if result is not value:
                return result
        return value

    if expected_type in {"integer", "number"}:
        return _coerce_number(value, integer_only=(expected_type == "integer"))
    if expected_type == "boolean":
        return _coerce_boolean(value)
    if expected_type == "array":
        return _coerce_json(value, list)
    if expected_type == "object":
        return _coerce_json(value, dict)
    if expected_type == "null" and value.strip().lower() == "null":
        return None
    return value


def _schema_allows_null(schema: dict | None) -> bool:
    """Return True when a JSON Schema fragment explicitly permits null."""
    if not isinstance(schema, dict):
        return False

    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    if schema.get("nullable") is True:
        return True

    for union_key in ("anyOf", "oneOf"):
        variants = schema.get(union_key)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if isinstance(variant, dict) and variant.get("type") == "null":
                return True

    return False


def _coerce_json(value: str, expected_python_type: type):
    """Parse *value* as JSON when the schema expects an array or object.

    Handles model output drift where a complex oneOf/discriminated-union schema
    causes the LLM to emit the array/object as a JSON string instead of a native
    structure.  Returns the original string if parsing fails or yields the wrong
    Python type.
    """
    try:
        parsed = orjson.loads(value)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "coerce_tool_args: failed to parse string as JSON for expected type %s: %s",
            expected_python_type.__name__,
            exc,
        )
        return value
    if isinstance(parsed, expected_python_type):
        logger.debug(
            "coerce_tool_args: coerced string to %s via json.loads",
            expected_python_type.__name__,
        )
        return parsed
    logger.warning(
        "coerce_tool_args: JSON-parsed value is %s, expected %s — skipping coercion",
        type(parsed).__name__,
        expected_python_type.__name__,
    )
    return value


def _coerce_number(value: str, integer_only: bool = False):
    """Try to parse *value* as a number.  Returns original string on failure."""
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    # Guard against inf/nan — not JSON-serializable, keep original string
    if f != f or f == float("inf") or f == float("-inf"):
        return value
    # If it looks like an integer (no fractional part), return int
    if f == int(f):
        return int(f)
    if integer_only:
        # Schema wants an integer but value has decimals — keep as string
        return value
    return f


def _coerce_boolean(value: str):
    """Try to parse *value* as a boolean.  Returns original string on failure."""
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value


def _tool_result_observer_fields(result: Any) -> tuple[str, Optional[str], Optional[str]]:
    try:
        parsed_result = orjson.loads(result) if isinstance(result, str) else result
        if isinstance(parsed_result, dict) and parsed_result.get("error"):
            return "error", "tool_error", str(parsed_result.get("error"))
    except Exception:
        pass
    return "ok", None, None


def _emit_post_tool_call_hook(
    *,
    function_name: str,
    function_args: Dict[str, Any],
    result: Any,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    api_request_id: Optional[str] = None,
    duration_ms: int = 0,
    status: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    middleware_trace: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Emit the ``post_tool_call`` observer hook.

    No-ops cheaply when no plugin has registered for ``post_tool_call`` —
    the ``has_hook`` gate skips both the result-field derivation and the
    payload dispatch so the no-listener path costs one dict lookup.  When
    ``status`` is not supplied, the ok/error fields are derived from the
    result *after* the gate (parsing the result is only worth it when a
    listener will actually consume it).
    """
    try:
        from hermes_cli.plugins import has_hook, invoke_hook
        if not has_hook("post_tool_call"):
            return
        if status is None:
            status, error_type, error_message = _tool_result_observer_fields(result)
        invoke_hook(
            "post_tool_call",
            tool_name=function_name,
            args=function_args,
            result=result,
            task_id=task_id or "",
            session_id=session_id or "",
            tool_call_id=tool_call_id or "",
            turn_id=turn_id or "",
            api_request_id=api_request_id or "",
            duration_ms=duration_ms,
            status=status,
            error_type=error_type,
            error_message=error_message,
            middleware_trace=list(middleware_trace or []),
        )
    except Exception as _hook_err:
        logger.debug("post_tool_call hook error: %s", _hook_err)


def handle_function_call(
    function_name: str,
    function_args: Dict[str, Any],
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    api_request_id: Optional[str] = None,
    user_task: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
    skip_pre_tool_call_hook: bool = False,
    skip_tool_request_middleware: bool = False,
    tool_request_middleware_trace: Optional[List[Dict[str, Any]]] = None,
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
) -> str:
    """
    Main function call dispatcher that routes calls to the tool registry.

    Args:
        function_name: Name of the function to call.
        function_args: Arguments for the function.
        task_id: Unique identifier for terminal/browser session isolation.
        user_task: The user's original task (for browser_snapshot context).
        enabled_tools: Tool names enabled for this session.  When provided,
                       execute_code uses this list to determine which sandbox
                       tools to generate.  Falls back to the process-global
                       ``_last_resolved_tool_names`` for backward compat.
        enabled_toolsets: The session's enabled toolsets.  Used to scope the
                       Tool Search bridge catalog so ``tool_search`` /
                       ``tool_describe`` / ``tool_call`` only see and invoke
                       tools the session was actually granted.  ``None`` means
                       "no restriction" (the caller scopes to every toolset),
                       matching ``get_tool_definitions`` semantics.
        disabled_toolsets: The session's disabled toolsets, applied as a
                       subtraction when scoping the bridge catalog.

    Returns:
        Function result as a JSON string.
    """
    # Ensure plugin tools are registered before dispatch (built-in tools
    # lazy-load through the registry on demand). Idempotent flag-check after
    # the first call, so this is free on the hot per-tool-call path.
    _ensure_discovered()

    # Tool arguments arrive as either a native dict (the normal agent-loop
    # path) or a JSON string (some transports hand ``tool_call.arguments``
    # straight through). Parse a string payload once here so its arguments are
    # preserved instead of being silently dropped to ``{}`` downstream, and so
    # the exact-match fast path below can inspect the real arguments.
    if isinstance(function_args, str):
        try:
            _parsed_args = orjson.loads(function_args)
        except (ValueError, TypeError):
            _parsed_args = None
        function_args = _parsed_args if isinstance(_parsed_args, dict) else {}

    # Resolve the tool schema once and reuse it for coercion / exact-match so a
    # hot dispatch does a single registry lookup instead of three (the pipeline
    # used to re-fetch it in coerce → repair → coerce).
    _tool_schema = registry.get_schema(function_name)

    # Fast path (P1 concurrent dispatch): when the arguments already match the
    # schema exactly — known keys, correct value types, no nested-object repair
    # — both coerce_tool_args and repair_tool_arg_keys are guaranteed no-ops.
    # Skip the argument-repair pipeline entirely; it is the dominant per-call
    # overhead once tool discovery is warm. Coercion still runs for every
    # payload that is NOT an exact match (string→int, bare-scalar→array, and
    # field-alias repair) so model output drift is corrected exactly as before.
    _args_exact = _args_match_schema_exactly(function_args, _tool_schema)
    if not _args_exact:
        # Coerce string arguments to their schema-declared types (e.g. "42"→42)
        function_args = coerce_tool_args(function_name, function_args, schema=_tool_schema)
        if not isinstance(function_args, dict):
            function_args = {}
    _tool_middleware_trace = list(tool_request_middleware_trace or [])

    # ── Tool Search bridge dispatch ──────────────────────────────────
    # tool_search and tool_describe are pure catalog reads — handle them
    # inline. tool_call is unwrapped to the underlying tool so that every
    # downstream hook (pre/post, edit approval, guardrails) sees the real
    # tool name, not the bridge.
    _ts_mod = None
    try:
        from tools import tool_search as _ts_mod  # noqa: F401
    except Exception:
        _ts_mod = None

    if _ts_mod is not None and _ts_mod.is_bridge_tool(function_name):
        try:
            # Use skip_tool_search_assembly=True so we see the real catalog,
            # not the already-collapsed bridge-only list (the bridge would
            # otherwise be searching only itself).
            #
            # Scope the catalog to the session's toolsets so the bridge can
            # only surface and invoke tools the session was actually granted.
            # Without this, a restricted-toolset session (subagent, kanban
            # worker, curated gateway session) would see and be able to call
            # the entire process registry via the bridge. Passing the same
            # enabled/disabled toolsets the session was assembled with keeps
            # the deferred catalog identical to the deferrable subset of the
            # session's own tool list, and avoids polluting the process-global
            # _last_resolved_tool_names with out-of-scope tools.
            current_defs = get_tool_definitions(
                enabled_toolsets=enabled_toolsets,
                disabled_toolsets=disabled_toolsets,
                quiet_mode=True, skip_tool_search_assembly=True,
            ) or []
        except Exception:
            current_defs = []
        if function_name == _ts_mod.TOOL_SEARCH_NAME:
            return _ts_mod.dispatch_tool_search(function_args or {},
                                                current_tool_defs=current_defs)
        if function_name == _ts_mod.TOOL_DESCRIBE_NAME:
            return _ts_mod.dispatch_tool_describe(function_args or {},
                                                  current_tool_defs=current_defs)
        if function_name == _ts_mod.TOOL_CALL_NAME:
            underlying_name, underlying_args, err = _ts_mod.resolve_underlying_call(function_args or {})
            if err or not underlying_name:
                return orjson.dumps({"error": err or "tool_call could not be resolved"}).decode('utf-8')
            # Defense in depth: the underlying tool MUST be in the session's
            # scoped deferrable catalog. resolve_underlying_call() only checks
            # that the name is deferrable in the global registry; this gate
            # additionally rejects any tool the session was not granted, so a
            # restricted session can never invoke an out-of-scope tool through
            # the bridge even if the catalog scoping above regressed.
            _scoped_deferrable = _ts_mod.scoped_deferrable_names(current_defs)
            if underlying_name not in _scoped_deferrable:
                return orjson.dumps({
                    "error": (
                        f"'{underlying_name}' is not available in this session. "
                        "Use tool_search to find tools you can call."
                    ),
                }).decode('utf-8')
            # Recurse with the underlying tool. All hooks fire against the
            # real tool name. The bridge is invisible to hooks by design.
            return handle_function_call(
                function_name=underlying_name,
                function_args=underlying_args,
                task_id=task_id,
                tool_call_id=tool_call_id,
                session_id=session_id,
                user_task=user_task,
                enabled_tools=enabled_tools,
                skip_pre_tool_call_hook=skip_pre_tool_call_hook,
                skip_tool_request_middleware=skip_tool_request_middleware,
                tool_request_middleware_trace=list(_tool_middleware_trace),
                enabled_toolsets=enabled_toolsets,
                disabled_toolsets=disabled_toolsets,
            )

    _tool_original_args = dict(function_args)
    if not skip_tool_request_middleware:
        try:
            from hermes_cli.middleware import apply_tool_request_middleware

            _tool_request_mw = apply_tool_request_middleware(
                function_name,
                function_args,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
                turn_id=turn_id or "",
                api_request_id=api_request_id or "",
            )
            function_args = _tool_request_mw.payload
            _tool_original_args = _tool_request_mw.original_payload
            _tool_middleware_trace = _tool_request_mw.trace
        except Exception as _mw_err:
            logger.debug("tool_request middleware error: %s", _mw_err)

    if not _tool_middleware_trace and not _args_match_schema_exactly(function_args, _tool_schema):
        # Repair common LLM field-name drift (e.g. "file"→"path") after the
        # request middleware seam.  Middleware must see and may intentionally
        # preserve the model's original payload shape; when no middleware has
        # rewritten the request we canonicalize before hooks/dispatch so legacy
        # observers and tool handlers still receive schema field names.
        #
        # Exactness is re-checked against the CURRENT args (request middleware
        # may have rewritten them since the top-of-function check) so skipping
        # repair here is always sound: an exact match means repair + coercion
        # cannot change anything.
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
        function_args = coerce_tool_args(function_name, repaired_args, schema=_tool_schema)

    try:
        if function_name in _AGENT_LOOP_TOOLS:
            return orjson.dumps({"error": f"{function_name} must be handled by the agent loop"}).decode('utf-8')

        # Check plugin hooks for a block directive (unless caller already
        # checked — e.g. run_agent._invoke_tool passes skip=True to
        # avoid double-firing the hook).
        #
        # Single-fire contract: pre_tool_call fires exactly once per tool
        # execution. get_pre_tool_call_block_message() internally calls
        # invoke_hook("pre_tool_call", ...) and returns the first block
        # directive (if any), so observer plugins see the hook on that same
        # pass. When skip=True, the caller already fired it — do nothing
        # here.
        if not skip_pre_tool_call_hook:
            block_message: Optional[str] = None
            try:
                from hermes_cli.plugins import get_pre_tool_call_block_message
                block_message = get_pre_tool_call_block_message(
                    function_name,
                    function_args,
                    task_id=task_id or "",
                    session_id=session_id or "",
                    tool_call_id=tool_call_id or "",
                    turn_id=turn_id or "",
                    api_request_id=api_request_id or "",
                    middleware_trace=list(_tool_middleware_trace),
                )
            except Exception as _hook_err:
                logger.debug("pre_tool_call hook error: %s", _hook_err)

            if block_message is not None:
                result = orjson.dumps({"error": block_message}).decode('utf-8')
                _emit_post_tool_call_hook(
                    function_name=function_name,
                    function_args=function_args,
                    result=result,
                    task_id=task_id,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    turn_id=turn_id,
                    api_request_id=api_request_id,
                    status="blocked",
                    error_type="plugin_block",
                    error_message=block_message,
                    middleware_trace=list(_tool_middleware_trace),
                )
                return result

        # ACP/Zed edit approval runs before any file mutation.  The requester
        # is bound via ContextVar only for ACP sessions, so CLI/gateway paths
        # are unaffected when it is unset.
        try:
            from acp_adapter.edit_approval import maybe_require_edit_approval

            edit_block_message = maybe_require_edit_approval(function_name, function_args)
            if edit_block_message is not None:
                return edit_block_message
        except Exception as _edit_approval_err:
            logger.debug("ACP edit approval guard error: %s", _edit_approval_err)
            if function_name in {"write_file", "patch"}:
                return orjson.dumps({"error": "Edit approval denied: approval guard failed"}).decode('utf-8')

        # Notify the read-loop tracker when a non-read/search tool runs,
        # so the *consecutive* counter resets (reads after other work are fine).
        if function_name not in _READ_SEARCH_TOOLS:
            try:
                from tools.file_tools import notify_other_tool_call
                notify_other_tool_call(task_id or "default")
            except Exception:
                pass  # file_tools may not be loaded yet

        # Measure tool dispatch latency so post_tool_call and
        # transform_tool_result hooks can observe per-tool duration.
        # Inspired by Claude Code 2.1.119, which added ``duration_ms`` to
        # PostToolUse hook inputs so plugin authors can build latency
        # dashboards, budget alerts, and regression canaries without having
        # to wrap every tool manually.  We use monotonic() so the value is
        # unaffected by wall-clock adjustments during the call.
        _dispatch_start = time.monotonic()
        _approval_tokens = None
        try:
            from tools.approval import (
                reset_current_observability_context,
                set_current_observability_context,
            )
            _approval_tokens = set_current_observability_context(
                turn_id=turn_id or "",
                tool_call_id=tool_call_id or "",
            )
        except Exception:
            reset_current_observability_context = None
        try:
            if function_name == "execute_code":
                # Prefer the caller-provided list so subagents can't overwrite
                # the parent's tool set via the process-global.
                sandbox_enabled = enabled_tools if enabled_tools is not None else _last_resolved_tool_names
                def _dispatch(next_args: Dict[str, Any]) -> Any:
                    return registry.dispatch(
                        function_name, next_args,
                        task_id=task_id,
                        session_id=session_id,
                        enabled_tools=sandbox_enabled,
                    )
            else:
                def _dispatch(next_args: Dict[str, Any]) -> Any:
                    return registry.dispatch(
                        function_name, next_args,
                        task_id=task_id,
                        session_id=session_id,
                        user_task=user_task,
                    )
            from hermes_cli.middleware import run_tool_execution_middleware

            result = run_tool_execution_middleware(
                function_name,
                function_args,
                _dispatch,
                original_args=_tool_original_args,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
                turn_id=turn_id or "",
                api_request_id=api_request_id or "",
            )
        finally:
            if _approval_tokens is not None and reset_current_observability_context is not None:
                try:
                    reset_current_observability_context(_approval_tokens)
                except Exception:
                    pass
        duration_ms = int((time.monotonic() - _dispatch_start) * 1000)

        _emit_post_tool_call_hook(
            function_name=function_name,
            function_args=function_args,
            result=result,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            turn_id=turn_id,
            api_request_id=api_request_id,
            duration_ms=duration_ms,
            middleware_trace=list(_tool_middleware_trace),
        )

        # Generic tool-result canonicalization seam: plugins receive the
        # final result string (JSON, usually) and may replace it by
        # returning a string from transform_tool_result. Runs after
        # post_tool_call (which stays observational) and before the result
        # is appended back into conversation context. Fail-open; the first
        # valid string return wins; non-string returns are ignored.
        # Gated on has_hook so the no-listener path skips both the result
        # field derivation and the payload dispatch.
        try:
            from hermes_cli.plugins import has_hook, invoke_hook
            if has_hook("transform_tool_result"):
                status, error_type, error_message = _tool_result_observer_fields(result)
                hook_results = invoke_hook(
                    "transform_tool_result",
                    tool_name=function_name,
                    args=function_args,
                    result=result,
                    task_id=task_id or "",
                    session_id=session_id or "",
                    tool_call_id=tool_call_id or "",
                    turn_id=turn_id or "",
                    api_request_id=api_request_id or "",
                    duration_ms=duration_ms,
                    status=status,
                    error_type=error_type,
                    error_message=error_message,
                )
                for hook_result in hook_results:
                    if isinstance(hook_result, str):
                        result = hook_result
                        break
        except Exception as _hook_err:
            logger.debug("transform_tool_result hook error: %s", _hook_err)

        return result

    except Exception as e:
        error_msg = f"Error executing {function_name}: {str(e)}"
        logger.exception(error_msg)
        return orjson.dumps({"error": _sanitize_tool_error(error_msg)}).decode('utf-8')


# =============================================================================
# Backward-compat wrapper functions
# =============================================================================

def get_all_tool_names() -> List[str]:
    """Return all registered tool names."""
    _ensure_discovered()
    return registry.get_all_tool_names()


def get_toolset_for_tool(tool_name: str) -> Optional[str]:
    """Return the toolset a tool belongs to."""
    _ensure_discovered()
    return registry.get_toolset_for_tool(tool_name)


def get_available_toolsets() -> Dict[str, dict]:
    """Return toolset availability info for UI display."""
    _ensure_discovered()
    return registry.get_available_toolsets()


def check_toolset_requirements() -> Dict[str, bool]:
    """Return {toolset: available_bool} for every registered toolset."""
    _ensure_discovered()
    return registry.check_toolset_requirements()


def check_tool_availability(quiet: bool = False) -> Tuple[List[str], List[dict]]:
    """Return (available_toolsets, unavailable_info)."""
    _ensure_discovered()
    return registry.check_tool_availability(quiet=quiet)
