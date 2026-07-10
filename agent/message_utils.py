"""Dependency-free message primitives shared by the API-message sanitizer.

Why this module exists
----------------------
``sanitize_api_messages`` runs **before every LLM request** — it is one of the
hottest functions in the conversation loop.  It only needs a handful of tiny,
stateless primitives: the set of API-valid roles, two accessors that read a
tool-call's id / function name, and the "does this assistant turn still carry
payload" predicate.

Historically those primitives lived on ``run_agent.AIAgent`` and the sanitizer
reached them through a lazy ``import run_agent`` (``_ra()``).  Because
``run_agent`` drags in the entire tool tree (~5.5k LOC + every ``tools/*.py``
registration), the *first* sanitize call in a fresh process paid the full
``run_agent`` import cascade — which the conversation-loop flame graph attributed
to ``sanitize_api_messages`` as **44.28%** of the run.  Every subsequent call
still paid a function call + module dict lookup + several attribute reads.

Keeping these primitives in a leaf module with **zero heavy imports** (stdlib
only) lets the sanitizer depend on them directly, so it no longer imports
``run_agent`` at all.  ``run_agent.AIAgent`` re-exports them for backward
compatibility (``AIAgent._VALID_API_ROLES`` / ``_get_tool_call_id_static`` /
``_get_tool_call_name_static``), so existing callers and tests are unaffected.

Everything here is intentionally pure and allocation-light.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# Roles the chat-completions / responses APIs accept.  Anything else (e.g.
# Hermes' internal ``session_meta`` marker) is stripped before a request.
VALID_API_ROLES = frozenset(
    {"system", "user", "assistant", "tool", "function", "developer"}
)

# Roles whose empty-string ``content`` makes the message droppable (unless an
# assistant turn still carries payload — see ``assistant_has_payload``).  Some
# strict OpenAI-compatible gateways (MiMo v2.5, ...) reject empty content.
EMPTY_CONTENT_ROLES = frozenset({"assistant", "user", "function"})

# Non-empty stand-in written over a tool_call whose ``function.name`` arrived
# blank, so the call and its result stay paired (see the sanitizer for the full
# rationale behind #47967 / the Responses-API orphan 400).
EMPTY_NAME_SENTINEL = "invalid_tool_call"

# Placeholder result injected when an assistant tool_call has no matching result
# (e.g. after context compression dropped it).
STUB_RESULT_CONTENT = "[Result unavailable — see context summary above]"

# Assistant payload fields that must keep an otherwise-empty assistant turn
# alive (reasoning replay + tool-call chains for codex/DeepSeek/etc.).
_ASSISTANT_PAYLOAD_FIELDS = (
    "tool_calls",
    "codex_reasoning_items",
    "codex_message_items",
    "reasoning_content",
)


def get_tool_call_id(tc: Any) -> str:
    """Extract the call id from a tool_call entry (dict or SDK object).

    Mirrors ``AIAgent._get_tool_call_id_static`` exactly: prefer ``call_id``
    (Codex Responses), fall back to ``id`` (chat-completions), strip whitespace,
    and return ``""`` when neither is present.
    """
    if isinstance(tc, dict):
        return (tc.get("call_id", "") or tc.get("id", "") or "").strip()
    return (getattr(tc, "call_id", "") or getattr(tc, "id", "") or "").strip()


def get_tool_call_name(tc: Any) -> str:
    """Extract the function name from a tool_call entry (dict or SDK object).

    Mirrors ``AIAgent._get_tool_call_name_static`` exactly.  Best-effort: some
    providers (Gemini's OpenAI-compat endpoint) require the matching function
    name on every ``role: tool`` message; others tolerate its absence, so
    callers fall back to ``""``.
    """
    if isinstance(tc, dict):
        fn = tc.get("function")
        if isinstance(fn, dict):
            return fn.get("name", "") or ""
        return ""
    fn = getattr(tc, "function", None)
    return getattr(fn, "name", "") or ""


def get_tool_call_function(tc: Any) -> Tuple[Any, Any]:
    """Return ``(function, name)`` for a tool_call, without normalizing.

    Unlike :func:`get_tool_call_name` this preserves the *raw* name (which may
    be ``None`` or all-whitespace) and hands back the ``function`` container so
    the sanitizer can repair a blank name in place.  ``function`` may be a dict,
    an SDK object, or ``None``.
    """
    if isinstance(tc, dict):
        fn = tc.get("function")
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
    else:
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", None) if fn else None
    return fn, name


def get_tool_call_function_and_id(tc: Any) -> Tuple[Any, Any, str]:
    """Return ``(function_container, raw_name, call_id)`` in a single dispatch.

    Hot-path helper for :func:`sanitize_api_messages`, which runs before *every*
    LLM request and otherwise pays TWO separate dict/attr dispatches per
    tool_call: one in :func:`get_tool_call_function` (for the ``function``
    container + raw name it may need to repair in place) and one in
    :func:`get_tool_call_id` (for the call id).  A conversation's full history is
    re-scanned on every request, so folding the two ``isinstance(tc, dict)``
    branches into one measurably trims the inner loop on tool-call-heavy
    sessions.

    Semantically identical to calling both:

    * ``(fn, name) == get_tool_call_function(tc)``
    * ``call_id == get_tool_call_id(tc)``

    Verified by ``test_get_tool_call_function_and_id_matches_separate_calls``.
    """
    if isinstance(tc, dict):
        fn = tc.get("function")
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        cid = (tc.get("call_id", "") or tc.get("id", "") or "").strip()
    else:
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", None) if fn else None
        cid = (getattr(tc, "call_id", "") or getattr(tc, "id", "") or "").strip()
    return fn, name, cid


def is_blank_name(name: Any) -> bool:
    """True when a tool_call name is missing / empty / all-whitespace."""
    return not (isinstance(name, str) and name.strip())


def assistant_has_payload(msg: Dict[str, Any]) -> bool:
    """True if an assistant message still carries API-relevant payload.

    Such a message must survive the empty-content filter so reasoning replay
    (codex/DeepSeek) and tool-call chains stay intact even when ``content`` is
    an empty string.
    """
    for field in _ASSISTANT_PAYLOAD_FIELDS:
        if msg.get(field):
            return True
    return False


def is_empty_content_droppable(msg: Dict[str, Any], role: Any = None) -> bool:
    """True when ``msg`` is an empty-content message the API would reject.

    A message is droppable iff its role is in :data:`EMPTY_CONTENT_ROLES`, its
    ``content`` is exactly ``""`` (not ``None`` — a ``None`` content carries
    tool_calls and must survive), and — for assistant turns — it has no
    surviving payload.  ``role`` may be passed in to avoid a redundant lookup.
    """
    if role is None:
        role = msg.get("role")
    if role not in EMPTY_CONTENT_ROLES:
        return False
    if msg.get("content") != "":
        return False
    if role == "assistant" and assistant_has_payload(msg):
        return False
    return True
