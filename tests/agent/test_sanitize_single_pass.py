"""Equivalence tests for the single-pass ``sanitize_api_messages``.

``sanitize_api_messages`` was folded from four separate O(n) scans (role
allowlist, empty-name repair, surviving-call-id collection, result-id
collection) plus an empty-content filter into one fused walk with
copy-on-first-write list handling. These tests pin the behaviour contract:
the fast single pass must produce output identical to the original multi-pass
algorithm, and must satisfy the same post-conditions, without ever corrupting
the caller's input list.

A compact, faithful reproduction of that algorithm — extended with the three
passes the sanitizer gained afterwards (name coercion, empty-id result drop,
positional reconciliation, all documented in ``_multi_pass_reference``) — lives
below; the tests assert the two agree across curated edge cases and a
deterministic fuzz battery. This is a differential/behaviour
contract, not a change-detector snapshot — it stays valid as long as the two
implementations agree on semantics.
"""

import copy
import random
import types

from agent.agent_runtime_helpers import _INTERRUPTED_PLACEHOLDER, _msg_has_payload
from agent.message_sanitization import coerce_tool_name
from agent.message_utils import (
    EMPTY_NAME_SENTINEL,
    STUB_RESULT_CONTENT,
    is_empty_content_droppable,
)
from run_agent import AIAgent

sanitize = AIAgent._sanitize_api_messages
_VALID = AIAgent._VALID_API_ROLES
_get_id = AIAgent._get_tool_call_id_static
_get_name = AIAgent._get_tool_call_name_static
# The injected-stub text and the coerced-name sentinel are part of the contract,
# so the reference takes them from the product modules rather than re-typing them.
_STUB = STUB_RESULT_CONTENT
_SENTINEL = EMPTY_NAME_SENTINEL


# ---------------------------------------------------------------------------
# Reference implementation of the sanitize contract.
#
# It is the original multi-pass algorithm (role allowlist -> name repair ->
# tool-call/result reconciliation -> empty-content filter -> tool_calls[]
# normalization -> dedup) EXTENDED with the three passes the sanitizer grew
# after that refactor, all of which landed upstream and are deliberately kept
# by this fork:
#
#   * tool-call names are coerced to the provider-safe form (#51944) and an SDK
#     tool-call object is rewritten as a plain dict (copy-on-write, so the
#     per-call copy never edits persisted history);
#   * tool results with a missing/empty ``tool_call_id`` are dropped;
#   * reconciliation is POSITIONAL (#94704): a result must immediately follow
#     the assistant message that declared its id, an unanswered declaration
#     gets a stub at the end of its run, and a replayed call is a legitimate
#     new round instead of a dedup casualty.
#
# The fork-specific half of the contract (P-024) is the DROP of empty-content
# ``assistant``/``user``/``function`` turns (pass 3). Upstream's
# "[response interrupted]" substitution heal stays wired as the LAST pass, so it
# can only ever fire on a turn that is empty in another way (``content`` ``None``
# or missing) — it can never re-inflate a turn the drop already removed.
#
# The tests assert the fused single pass agrees with this reference across
# curated edge cases and a deterministic fuzz battery. This is a
# differential/behaviour contract, not a change-detector snapshot — it stays
# valid as long as the two implementations agree on semantics.
# ---------------------------------------------------------------------------

def _multi_pass_reference(messages):
    # Pass 1: role allowlist.
    messages = [m for m in messages if m.get("role") in _VALID]

    # Pass 2: coerce tool-call function names to the provider-safe form.
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue
        for idx, tc in enumerate(tool_calls):
            if isinstance(tc, dict):
                fn = tc.get("function")
                name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None) if fn else None
            coerced = coerce_tool_name(name)
            if coerced == name:
                continue
            if isinstance(tc, dict):
                tool_calls[idx] = {
                    **tc,
                    "function": ({**fn, "name": coerced} if isinstance(fn, dict)
                                 else {"name": coerced, "arguments": "{}"}),
                }
            else:
                args = getattr(fn, "arguments", None) if fn is not None else None
                tool_calls[idx] = {
                    "id": _get_id(tc),
                    "type": "function",
                    "function": {
                        "name": coerced,
                        "arguments": args if isinstance(args, str) else "{}",
                    },
                }

    # Pass 3: drop empty-content assistant/user/function with no payload (fork P-024).
    messages = [m for m in messages if not is_empty_content_droppable(m)]

    # Pass 4: drop tool results with a missing/empty tool_call_id.
    messages = [
        m for m in messages
        if not (m.get("role") == "tool" and not (m.get("tool_call_id") or "").strip())
    ]

    # Pass 5: positional tool_call/tool_result pairing.
    paired = []
    declared = {}

    def _flush_unanswered_stubs():
        # Declaration order, so the stubs read in the order the calls were made.
        for cid, tc in declared.items():
            paired.append({
                "role": "tool", "name": _get_name(tc), "content": _STUB, "tool_call_id": cid,
            })
        declared.clear()

    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            _flush_unanswered_stubs()
            for tc in msg.get("tool_calls") or []:
                cid = (_get_id(tc) or "").strip()
                if cid:
                    declared[cid] = tc
        elif role == "tool":
            cid = (msg.get("tool_call_id") or "").strip()
            if declared.pop(cid, None) is None:
                continue  # positional orphan: no immediately preceding call for this id
        else:
            _flush_unanswered_stubs()
        paired.append(msg)
    _flush_unanswered_stubs()
    messages = paired

    # Pass 6: deduplicate tool_call_ids (#58327) — collapse duplicates within an assistant
    # message, drop a result that answers no OUTSTANDING call.
    outstanding = set()
    deduped = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            kept = []
            for tc in msg.get("tool_calls") or []:
                cid = (_get_id(tc) or "").strip()
                if cid and cid in outstanding:
                    continue
                if cid:
                    outstanding.add(cid)
                kept.append(tc)
            if len(kept) != len(msg.get("tool_calls") or []):
                msg = {**msg, "tool_calls": kept}
            deduped.append(msg)
        elif role == "tool":
            cid = (msg.get("tool_call_id") or "").strip()
            if cid:
                if cid not in outstanding:
                    continue
                outstanding.discard(cid)
            deduped.append(msg)
        else:
            deduped.append(msg)
    messages = deduped

    # Pass 7: align each result's wire ``name`` with its call's function name.
    call_names = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                cid = (_get_id(tc) or "").strip()
                name = _get_name(tc)
                if cid and name:
                    call_names[cid] = name
    realigned = []
    for msg in messages:
        if msg.get("role") == "tool":
            expected = call_names.get((msg.get("tool_call_id") or "").strip())
            current = msg.get("name")
            if expected and current and current != expected:
                msg = {**msg, "name": expected}
        realigned.append(msg)
    messages = realigned

    # Pass 8: upstream's send-boundary net, kept wired AFTER the fork's drop: a non-final
    # user/assistant turn that is empty in some other way (``content`` missing or ``None``
    # — ``""`` was already dropped by pass 3) gets the neutral interrupted placeholder
    # instead of being replayed as an invalid turn.
    if len(messages) >= 2:
        healed = []
        last_idx = len(messages) - 1
        for idx, msg in enumerate(messages):
            if (idx != last_idx and isinstance(msg, dict)
                    and msg.get("role") in ("assistant", "user")
                    and not _msg_has_payload(msg)):
                msg = {**msg, "content": _INTERRUPTED_PLACEHOLDER}
            healed.append(msg)
        messages = healed
    return messages


# ---------------------------------------------------------------------------
# Curated cases
# ---------------------------------------------------------------------------

def _obj_tc(cid, name):
    return types.SimpleNamespace(id=cid, function=types.SimpleNamespace(name=name, arguments="{}"))


_CURATED = [
    [],
    [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
    # orphaned result
    [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]},
     {"role": "tool", "tool_call_id": "c1", "content": "ok"},
     {"role": "tool", "tool_call_id": "c_orphan", "content": "ok"}],
    # missing result -> stub
    [{"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "t", "arguments": "{}"}}]}],
    # invalid roles interleaved
    [{"role": "session_meta", "content": "x"},
     {"role": "user", "content": "hi"},
     {"role": "weird", "content": "y"},
     {"role": "assistant", "content": "yo"}],
    # empty-name repair (dict + object) + whitespace ids
    [{"role": "assistant", "tool_calls": [{"id": " c3 ", "function": {"name": "", "arguments": "{}"}}]},
     {"role": "tool", "tool_call_id": "c3", "content": "r"}],
    [{"role": "assistant", "tool_calls": [_obj_tc("c4", "  ")]},
     {"role": "tool", "tool_call_id": "c4", "content": "r"}],
    # empty content dropping / preservation
    [{"role": "user", "content": ""}, {"role": "assistant", "content": ""},
     {"role": "system", "content": ""},
     {"role": "assistant", "content": "", "reasoning_content": "t"},
     {"role": "assistant", "content": "", "tool_calls": [{"id": "c5", "function": {"name": "t", "arguments": "{}"}}]},
     {"role": "tool", "tool_call_id": "c5", "content": "r"}],
    # content None must NOT be dropped by the empty-content filter
    [{"role": "assistant", "content": None,
      "tool_calls": [{"id": "c6", "function": {"name": "t", "arguments": "{}"}}]},
     {"role": "tool", "tool_call_id": "c6", "content": "r"}],
]


def test_sanitize_single_pass_matches_multi_pass_curated():
    for case in _CURATED:
        expected = _multi_pass_reference(copy.deepcopy(case))
        actual = sanitize(copy.deepcopy(case))
        assert actual == expected, case


def test_sanitize_single_pass_matches_multi_pass_fuzz():
    """Deterministic fuzz: the fused single pass must agree with the reference
    multi-pass algorithm on every generated message list."""
    roles = ["user", "assistant", "system", "function", "developer",
             "tool", "session_meta", "weird", "tool_result"]
    names = ["terminal", "web_search", "", "  ", None]
    cids = ["c1", "c2", " c3 ", "c_orphan", ""]
    contents = ["", "text", None, "hello world"]

    def rand_msg(r):
        role = r.choice(roles)
        m = {"role": role}
        if role == "tool":
            m["tool_call_id"] = r.choice(cids)
            m["content"] = r.choice(contents)
            return m
        m["content"] = r.choice(contents)
        if role in ("assistant",) and r.random() < 0.55:
            tcs = []
            for _ in range(r.randint(1, 2)):
                cid = r.choice(cids)
                name = r.choice(names)
                if r.random() < 0.5:
                    tcs.append({"id": cid, "function": {"name": name, "arguments": "{}"}}
                               if name is not None else {"id": cid, "function": {}})
                else:
                    tcs.append(_obj_tc(cid, name if name is not None else ""))
            m["tool_calls"] = tcs
        if r.random() < 0.15:
            m["reasoning_content"] = r.choice(["", "thinking"])
        if r.random() < 0.1:
            m["codex_reasoning_items"] = [{"id": "rs"}]
        return m

    mismatches = 0
    for trial in range(1500):
        r = random.Random(trial)
        case = [rand_msg(r) for _ in range(r.randint(0, 9))]
        expected = _multi_pass_reference(copy.deepcopy(case))
        actual = sanitize(copy.deepcopy(case))
        if actual != expected:
            mismatches += 1
    assert mismatches == 0


# ---------------------------------------------------------------------------
# Post-conditions the single pass must guarantee
# ---------------------------------------------------------------------------

def _postconditions_hold(out):
    # 1. No invalid roles.
    assert all(m.get("role") in _VALID for m in out)
    surviving = set()
    for m in out:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                # 5. No empty tool-call function name survives.
                assert (_get_name(tc) or "").strip() != ""
                cid = _get_id(tc)
                if cid:
                    surviving.add(cid)
    results = set()
    for m in out:
        if m.get("role") == "tool":
            cid = (m.get("tool_call_id") or "").strip()
            if cid:
                results.add(cid)
    # 2. No orphaned tool results, 3. no missing results.
    assert results - surviving == set()
    assert surviving - results == set()
    # 4. No empty-content assistant/user/function without payload.
    for m in out:
        if m.get("role") in {"assistant", "user", "function"} and m.get("content") == "":
            assert m.get("role") == "assistant" and (
                m.get("tool_calls") or m.get("codex_reasoning_items")
                or m.get("codex_message_items") or m.get("reasoning_content")
            )


def test_sanitize_single_pass_postconditions():
    for case in _CURATED:
        _postconditions_hold(sanitize(copy.deepcopy(case)))


def test_sanitize_single_pass_is_idempotent():
    for case in _CURATED:
        once = sanitize(copy.deepcopy(case))
        twice = sanitize(copy.deepcopy(once))
        assert once == twice


def test_sanitize_single_pass_does_not_corrupt_input_list():
    """Copy-on-first-write must not append to / shrink the caller's list."""
    original = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    snapshot = copy.deepcopy(original)
    out = sanitize(original)
    # The caller's list is unchanged in length/content by the happy path.
    assert original == snapshot
    assert out == snapshot

    # A drop case must not shrink the caller's list either.
    original2 = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},   # dropped (empty, no payload)
        {"role": "assistant", "content": "bye"},
    ]
    len_before = len(original2)
    out2 = sanitize(original2)
    assert len(original2) == len_before          # input not shrunk in place
    assert len(out2) == 2


# ---------------------------------------------------------------------------
# Structural contract: the fused pass allocates nothing on the clean path.
# ---------------------------------------------------------------------------

def test_clean_lists_pass_through_by_identity():
    """A fully clean list is returned as the SAME object — the copy-on-write
    fused pass, the no-tool-call fast-exit, and tool reconciliation are all
    allocation-free when there is nothing to fix."""
    # No tool calls at all -> hits the fast-exit.
    text_only = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    assert sanitize(text_only) is text_only

    # Matched tool call/result -> passes reconciliation without a rewrite.
    with_tools = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert sanitize(with_tools) is with_tools


def test_empty_content_fold_matches_reference_after_tool_reconcile():
    """Empty-content dropping (folded into the fused pass) and tool-pair
    reconciliation are order-independent: interleaving empties with a
    missing-result assistant call still matches the multi-pass reference."""
    case = [
        {"role": "user", "content": ""},                      # dropped (empty)
        {"role": "assistant", "tool_calls": [{"id": "cM", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "function", "content": ""},                  # dropped (empty)
        {"role": "assistant", "content": ""},                 # dropped (empty, no payload)
    ]
    expected = _multi_pass_reference(copy.deepcopy(case))
    actual = sanitize(copy.deepcopy(case))
    assert actual == expected
    # Stub injected for the missing result; the three empties are gone.
    assert [m["role"] for m in actual] == ["assistant", "tool"]


# ---------------------------------------------------------------------------
# Incremental scenario: a conversation sanitized turn-by-turn as it grows must
# match a full stateless re-sanitize at every step.  The sanitizer is stateless,
# so "process only the new messages" reduces to "every growth step stays exact"
# — and a well-formed history is returned unchanged (no per-turn rewrite work).
# ---------------------------------------------------------------------------

def test_incremental_sanitize_growing_conversation():
    """Grow a realistic tool-using conversation three messages per turn; at
    every turn the sanitized output equals the multi-pass reference over the
    whole history, and clean turns return the input object unchanged."""
    history = [{"role": "system", "content": "You are helpful."}]
    for i in range(40):
        history.append({"role": "user", "content": f"Q{i}"})
        cid = f"call_{i}"
        history.append({
            "role": "assistant",
            "tool_calls": [{"id": cid, "function": {"name": "terminal", "arguments": "{}"}}],
        })
        history.append({"role": "tool", "tool_call_id": cid, "content": f"A{i}"})

        assert sanitize(copy.deepcopy(history)) == _multi_pass_reference(copy.deepcopy(history))
        # Well-formed history needs no repair -> identity pass-through.
        assert sanitize(history) is history


def test_incremental_sanitize_with_late_orphan_and_recovery():
    """A malformed turn (orphaned tool result) is fixed, and once the
    conversation returns to well-formed growth the output tracks the reference
    exactly at every subsequent step."""
    history = [{"role": "user", "content": "start"}]
    history.append({"role": "tool", "tool_call_id": "ghost", "content": "boo"})  # orphan
    out = sanitize(copy.deepcopy(history))
    assert out == _multi_pass_reference(copy.deepcopy(history))
    assert all(m.get("role") != "tool" for m in out)  # orphan removed

    for i in range(5):
        cid = f"ok_{i}"
        history.append({
            "role": "assistant",
            "tool_calls": [{"id": cid, "function": {"name": "t", "arguments": "{}"}}],
        })
        history.append({"role": "tool", "tool_call_id": cid, "content": "r"})
        assert sanitize(copy.deepcopy(history)) == _multi_pass_reference(copy.deepcopy(history))
