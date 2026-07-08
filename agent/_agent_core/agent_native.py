"""Cross-platform Python FFI wrapper for the agent-basic-library native API.

This module loads the unified ``agent_core`` shared library produced by the
xmake build and exposes every public C function as a plain Python callable.
All memory management (strings returned by the native library) is handled
through the library's own ``agent_free`` entry point so the same C runtime
heap is used on Windows.

Usage:
    from agent_native import (
        sanitize_messages_surrogates,
        truncate_content,
        sanitize_api_messages,
        find_tail_cut_by_tokens,
    )

    out = sanitize_messages_surrogates('[{"role":"user","content":"hello"}]')

The module can find the library in a few ways, in order of priority:

1. ``AGENT_NATIVE_LIB_PATH`` environment variable (absolute path to the
   shared library file).
2. ``AGENT_NATIVE_BUILD_DIR`` environment variable (directory containing the
   shared library file).
3. Common xmake output directories relative to this file's location.
4. ``PATH`` / ``LD_LIBRARY_PATH`` / ``DYLD_LIBRARY_PATH`` lookup by bare name.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
from ctypes import c_char_p, c_int, c_size_t, POINTER
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

__all__ = [
    "AgentNativeError",
    "load_library",
    "agent_free",
    # message_sanitization
    "sanitize_messages_surrogates",
    "repair_tool_call_arguments",
    "escape_invalid_chars_in_json_strings",
    "sanitize_messages_non_ascii",
    "strip_images_from_messages",
    # prompt_builder
    "truncate_content",
    "strip_yaml_frontmatter",
    "scan_context_content",
    "build_context_files_prompt",
    # conversation_loop
    "sanitize_api_messages",
    "repair_message_sequence",
    "sanitize_and_repair_messages",
    # context_compressor
    "sanitize_tool_pairs",
    "find_tail_cut_by_tokens",
    "build_static_fallback_summary",
    "prune_old_tool_results",
]


class AgentNativeError(Exception):
    """Raised when the native library cannot be loaded or a call fails."""


# ---------------------------------------------------------------------------
# Library discovery
# ---------------------------------------------------------------------------

def _lib_name() -> str:
    """Return the platform-specific shared-library basename for agent_core."""
    system = platform.system()
    if system == "Windows":
        return "agent_core.dll"
    if system == "Darwin":
        return "libagent_core.dylib"
    return "libagent_core.so"


def _possible_build_dirs(project_root: Path) -> List[Path]:
    """Return candidate xmake output directories, most specific first."""
    system = platform.system()
    arch = platform.machine().lower()
    # Normalize architecture names to xmake conventions.
    if arch in ("amd64", "x86_64"):
        arch = "x64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"

    build_root = project_root / "build"
    candidates: List[Path] = []

    # xmake default layout: build/<plat>/<arch>/<mode>
    # Prefer release builds for Python FFI because debug builds may link the
    # AddressSanitizer runtime, which is not generally available to Python.
    for mode in ("release", "releasedbg", "debug"):
        candidates.append(build_root / system.lower() / arch / mode)

    # Simpler layouts used by some configurations.
    for mode in ("release", "debug"):
        candidates.append(build_root / mode)
        candidates.append(build_root / system.lower() / mode)

    candidates.append(build_root)
    return candidates


def _find_library_path(project_root: Path) -> Path:
    """Locate the agent_core shared library or raise AgentNativeError."""
    name = _lib_name()

    # 1. Explicit full path.
    env_path = os.environ.get("AGENT_NATIVE_LIB_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        raise AgentNativeError(
            f"AGENT_NATIVE_LIB_PATH points to a missing file: {env_path}"
        )

    # 2. Explicit build directory.
    env_dir = os.environ.get("AGENT_NATIVE_BUILD_DIR")
    if env_dir:
        p = Path(env_dir) / name
        if p.is_file():
            return p
        raise AgentNativeError(
            f"AGENT_NATIVE_BUILD_DIR does not contain {name}: {env_dir}"
        )

    # 3. Relative to this script (../build/...).
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    for directory in _possible_build_dirs(project_root):
        candidate = directory / name
        if candidate.is_file():
            return candidate

    # 4. Library placed next to this script (e.g. a self-contained dist/ layout).
    candidate = script_dir / name
    if candidate.is_file():
        return candidate

    # 5. Let ctypes try to resolve the bare name from system search paths.
    return Path(name)


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_lib: Optional[ctypes.CDLL] = None


def load_library(path: Optional[Path] = None) -> ctypes.CDLL:
    """Load (or reload) the agent_core shared library.

    Args:
        path: Optional explicit path to the shared library. If omitted, the
            library is discovered automatically.

    Returns:
        The loaded ctypes library handle.
    """
    global _lib

    if path is None:
        path = _find_library_path(Path(__file__).resolve().parent.parent)

    # Use the platform-specific loader class. WinDLL is required when the
    # library uses __stdcall (ours uses cdecl), but CDLL works on Windows as
    # well for cdecl. We still prefer CDLL for a single cross-platform path.
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        raise AgentNativeError(
            f"Failed to load agent_core native library from {path}: {exc}"
        ) from exc

    # Configure the helper used to free native-allocated strings.
    lib.agent_free.argtypes = [ctypes.c_void_p]
    lib.agent_free.restype = None

    # String-returning functions use c_void_p as restype so the original
    # native pointer can be freed with agent_free.

    # message_sanitization
    lib.sanitize_messages_surrogates.argtypes = [c_char_p]
    lib.sanitize_messages_surrogates.restype = ctypes.c_void_p

    lib.repair_tool_call_arguments.argtypes = [c_char_p, c_char_p]
    lib.repair_tool_call_arguments.restype = ctypes.c_void_p

    lib.escape_invalid_chars_in_json_strings.argtypes = [c_char_p]
    lib.escape_invalid_chars_in_json_strings.restype = ctypes.c_void_p

    lib.sanitize_messages_non_ascii.argtypes = [c_char_p]
    lib.sanitize_messages_non_ascii.restype = ctypes.c_void_p

    lib.strip_images_from_messages.argtypes = [c_char_p]
    lib.strip_images_from_messages.restype = ctypes.c_void_p

    # prompt_builder
    lib.truncate_content.argtypes = [c_char_p, c_char_p, c_size_t, c_char_p]
    lib.truncate_content.restype = ctypes.c_void_p

    lib.strip_yaml_frontmatter.argtypes = [c_char_p]
    lib.strip_yaml_frontmatter.restype = ctypes.c_void_p

    lib.scan_context_content.argtypes = [c_char_p, c_char_p]
    lib.scan_context_content.restype = c_int

    lib.build_context_files_prompt.argtypes = [POINTER(c_char_p), c_size_t]
    lib.build_context_files_prompt.restype = ctypes.c_void_p

    # conversation_loop
    lib.sanitize_api_messages.argtypes = [c_char_p]
    lib.sanitize_api_messages.restype = ctypes.c_void_p

    lib.repair_message_sequence.argtypes = [c_char_p]
    lib.repair_message_sequence.restype = ctypes.c_void_p

    lib.sanitize_and_repair_messages.argtypes = [c_char_p]
    lib.sanitize_and_repair_messages.restype = ctypes.c_void_p

    # context_compressor
    lib.sanitize_tool_pairs.argtypes = [c_char_p]
    lib.sanitize_tool_pairs.restype = ctypes.c_void_p

    lib.find_tail_cut_by_tokens.argtypes = [
        c_char_p,
        c_size_t,
        c_size_t,
        POINTER(c_size_t),
        POINTER(c_size_t),
    ]
    lib.find_tail_cut_by_tokens.restype = None

    lib.build_static_fallback_summary.argtypes = [c_char_p, c_size_t]
    lib.build_static_fallback_summary.restype = ctypes.c_void_p

    lib.prune_old_tool_results.argtypes = [c_char_p]
    lib.prune_old_tool_results.restype = ctypes.c_void_p

    _lib = lib
    return lib


def _lib_handle() -> ctypes.CDLL:
    if _lib is None:
        return load_library()
    return _lib


def agent_free(ptr: ctypes.c_void_p) -> None:
    """Release memory allocated by the native library.

    Most users do not need to call this directly because the high-level
    wrappers free returned strings automatically.
    """
    _lib_handle().agent_free(ptr)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_arg(arg: object) -> object:
    if isinstance(arg, str):
        return arg.encode("utf-8")
    if isinstance(arg, bytes):
        return arg
    if arg is None:
        return None
    return arg


def _call_string_fn(name: str, *args: object) -> str:
    """Call a C function returning a malloc'd string and return a Python str.

    The native pointer is preserved so it can be released with agent_free.
    Raises AgentNativeError if the native function returns NULL.
    """
    lib = _lib_handle()
    fn = getattr(lib, name)
    encoded = [_encode_arg(arg) for arg in args]

    ptr = fn(*encoded)
    if ptr is None:
        raise AgentNativeError(f"{name} returned NULL")
    value = ctypes.string_at(ptr).decode("utf-8")
    lib.agent_free(ptr)
    return value


def _maybe_call_string_fn(name: str, *args: object) -> Optional[str]:
    """Like _call_string_fn but returns None on NULL (for optional inputs)."""
    lib = _lib_handle()
    fn = getattr(lib, name)
    encoded = [_encode_arg(arg) for arg in args]
    ptr = fn(*encoded)
    if ptr is None:
        return None
    value = ctypes.string_at(ptr).decode("utf-8")
    lib.agent_free(ptr)
    return value


# ---------------------------------------------------------------------------
# Public API: message_sanitization
# ---------------------------------------------------------------------------

def sanitize_messages_surrogates(messages_json: str) -> str:
    """Replace surrogate code points with U+FFFD in a JSON message array."""
    return _call_string_fn("sanitize_messages_surrogates", messages_json)


def repair_tool_call_arguments(raw_args: str, tool_name: Optional[str] = None) -> str:
    """Repair malformed JSON tool-call arguments."""
    return _call_string_fn("repair_tool_call_arguments", raw_args, tool_name)


def escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Escape unescaped control characters inside JSON string values."""
    return _call_string_fn("escape_invalid_chars_in_json_strings", raw)


def sanitize_messages_non_ascii(messages_json: str) -> str:
    """Replace non-ASCII characters in message content (last-resort mode)."""
    return _call_string_fn("sanitize_messages_non_ascii", messages_json)


def strip_images_from_messages(messages_json: str) -> str:
    """Strip image_url/image/input_image content parts from messages."""
    return _call_string_fn("strip_images_from_messages", messages_json)


# ---------------------------------------------------------------------------
# Public API: prompt_builder
# ---------------------------------------------------------------------------

def truncate_content(
    content: str,
    filename: Optional[str] = None,
    max_chars: int = 0,
    read_path: Optional[str] = None,
) -> str:
    """Truncate content to fit within ``max_chars`` using head/tail split."""
    if max_chars <= 0:
        # Sensible default mirroring typical Python usage.
        max_chars = 8000
    return _call_string_fn(
        "truncate_content", content, filename, max_chars, read_path
    )


def strip_yaml_frontmatter(content: str) -> str:
    """Strip YAML frontmatter (``---`` delimited) from content."""
    return _call_string_fn("strip_yaml_frontmatter", content)


def scan_context_content(content: str, filename: Optional[str] = None) -> int:
    """Scan context content for threat patterns.

    Returns 0 if clean, non-zero if a pattern was found.
    """
    lib = _lib_handle()
    content_bytes = content.encode("utf-8")
    filename_bytes = filename.encode("utf-8") if filename is not None else None
    return int(
        lib.scan_context_content(
            c_char_p(content_bytes), c_char_p(filename_bytes)
        )
    )


def build_context_files_prompt(sections: Iterable[str]) -> str:
    """Build the "Project Context" section from a list of section strings."""
    section_list = list(sections)
    lib = _lib_handle()

    if not section_list:
        # The C routine returns NULL for an empty section list; the natural
        # Pythonic result is the header only.
        return "# Project Context\n\n"

    encoded = [s.encode("utf-8") for s in section_list]
    ptr_type = c_char_p * len(encoded)
    arr = ptr_type(*encoded)

    ptr = lib.build_context_files_prompt(arr, len(encoded))
    if ptr is None:
        raise AgentNativeError("build_context_files_prompt returned NULL")
    value = ctypes.string_at(ptr).decode("utf-8")
    lib.agent_free(ptr)
    return value


# ---------------------------------------------------------------------------
# Public API: conversation_loop
# ---------------------------------------------------------------------------

def sanitize_api_messages(messages_json: str) -> str:
    """Sanitize an API message list (role allowlist, tool-call repair, ...)."""
    return _call_string_fn("sanitize_api_messages", messages_json)


def repair_message_sequence(messages_json: str) -> str:
    """Repair message sequence (merge consecutive turns where allowed)."""
    return _call_string_fn("repair_message_sequence", messages_json)


def sanitize_and_repair_messages(messages_json: str) -> str:
    """Combined sanitize + repair pass."""
    return _call_string_fn("sanitize_and_repair_messages", messages_json)


# ---------------------------------------------------------------------------
# Public API: context_compressor
# ---------------------------------------------------------------------------

def sanitize_tool_pairs(messages_json: str) -> str:
    """Sanitize tool pairs (drop orphans, inject stubs)."""
    return _call_string_fn("sanitize_tool_pairs", messages_json)


def find_tail_cut_by_tokens(
    messages_json: str, head_end: int, token_budget: int
) -> Tuple[int, int]:
    """Find the tail-cut index by walking backward from the message list end.

    Returns:
        ``(tail_start, tail_tokens)``.
    """
    lib = _lib_handle()
    encoded = messages_json.encode("utf-8")
    tail_start = c_size_t()
    tail_tokens = c_size_t()
    lib.find_tail_cut_by_tokens(
        c_char_p(encoded),
        c_size_t(head_end),
        c_size_t(token_budget),
        ctypes.byref(tail_start),
        ctypes.byref(tail_tokens),
    )
    return int(tail_start.value), int(tail_tokens.value)


def build_static_fallback_summary(messages_json: str, tail_start: int) -> str:
    """Build a static fallback summary string from pruned messages."""
    return _call_string_fn(
        "build_static_fallback_summary", messages_json, tail_start
    )


def prune_old_tool_results(messages_json: str) -> str:
    """Prune old tool results from the message list."""
    return _call_string_fn("prune_old_tool_results", messages_json)
