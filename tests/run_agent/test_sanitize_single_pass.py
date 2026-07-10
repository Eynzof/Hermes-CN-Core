"""Equivalence tests for the single-pass ``sanitize_api_messages``.

``sanitize_api_messages`` was folded from four separate O(n) scans (role
allowlist, empty-name repair, surviving-call-id collection, result-id
collection) plus an empty-content filter into one fused walk with
copy-on-first-write list handling. These tests pin the behaviour contract:
the fast single pass must produce output identical to the original multi-pass
algorithm, and must satisfy the same post-conditions, without ever corrupting
the caller's input list.

A compact, faithful reproduction of the original multi-pass algorithm lives in
``_multi_pass_reference`` below; the tests assert the two agree across curated
edge cases and a deterministic fuzz battery. This is a differential/behaviour
contract, not a change-detector snapshot — it stays valid as long as the two
implementations agree on semantics.
"""

import copy
import random
import types

from run_agent import AIAgent

sanitize = AIAgent._sanitize_api_messages
_VALID = AIAgent._VALID_API_ROLES
_get_id = AIAgent._get_tool_call_id_static
_get_name = AIAgent._get_tool_call_name_static
_STUB = "[Result unavailable — see context summary above]"
_SENTINEL = "invalid_tool_call"


# ---------------------------------------------------------------------------
# Faithful reproduction of the pre-optimization multi-pass implementation.
# ---------------------------------------------------------------------------

def _multi_pass_reference(messages):
    # Pass 1: role allowlist.
    messages = [m for m in messages if m.get("role") in _VALID]

    # Pass 2: repair empty tool_call function names (in place).
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function")
                name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None) if fn else None
            if isinstance(name, str) and name.strip():
                continue
            if isinstance(fn, dict):
                fn["name"] = _SENTINEL
            elif fn is not None and hasattr(fn, "name"):
                try:
                    fn.name = _SENTINEL
                except Exception:
                    pass
            elif isinstance(tc, dict):
                tc["function"] = {"name": _SENTINEL, "arguments": "{}"}

    # Pass 3 + 4: collect surviving call ids and result ids.
    surviving = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                cid = _get_id(tc)
                if cid:
                    surviving.add(cid)
    results = set()
    for msg in messages:
        if msg.get("role") == "tool":
            cid = (msg.get("tool_call_id") or "").strip()
            if cid:
                results.add(cid)

    # Drop orphaned tool results.
    orphaned = results - surviving
    if orphaned:
        messages = [
            m for m in messages
            if not (m.get("role") == "tool"
                    and (m.get("tool_call_id") or "").strip() in orphaned)
        ]

    # Inject stub results for missing.
    missing = surviving - results
    if missing:
        patched = []
        for msg in messages:
            patched.append(msg)
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = _get_id(tc)
                    if cid in missing:
                        patched.append({
                            "role": "tool",
                            "name": _get_name(tc),
                            "content": _STUB,
                            "tool_call_id": cid,
                        })
        messages = patched

    # Drop empty-content assistant/user/function with no payload.
    ecr = {"assistant", "user", "function"}

    def _payload(m):
        return bool(m.get("tool_calls") or m.get("codex_reasoning_items")
                    or m.get("codex_message_items") or m.get("reasoning_content"))

    messages = [
        m for m in messages
        if not (m.get("role") in ecr and m.get("content") == ""
                and not (m.get("role") == "assistant" and _payload(m)))
    ]

    # Normalize empty/malformed ``tool_calls`` arrays on assistant messages
    # (#58755): drop the key on a shallow copy. Mirrors the fused pass's (b2)
    # step; ordering never matters for id collection since an empty/non-list
    # value contributes no call ids.
    normalized = []
    for m in messages:
        if (m.get("role") == "assistant" and "tool_calls" in m
                and not (isinstance(m["tool_calls"], list) and m["tool_calls"])):
            m = {k: v for k, v in m.items() if k != "tool_calls"}
        normalized.append(m)
    messages = normalized

    # Deduplicate tool_call_ids (#58327): collapse duplicate tool_calls within
    # assistant messages, drop later tool results reusing a seen id. Mirrors
    # the fused pass's terminal step 3.
    seen_a, seen_r = set(), set()
    deduped = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            kept = []
            for tc in m.get("tool_calls") or []:
                cid = _get_id(tc)
                if cid and cid in seen_a:
                    continue
                if cid:
                    seen_a.add(cid)
                kept.append(tc)
            if len(kept) != len(m.get("tool_calls") or []):
                m = {**m, "tool_calls": kept}
            deduped.append(m)
        elif role == "tool":
            cid = (m.get("tool_call_id") or "").strip()
            if cid and cid in seen_r:
                continue
            if cid:
                seen_r.add(cid)
            deduped.append(m)
        else:
            deduped.append(m)
    messages = deduped
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
