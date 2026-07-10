"""Tests for ``agent.message_utils`` — the dependency-free message primitives.

Two things are pinned here as *behaviour contracts* (not change-detectors):

1. The primitives behave correctly across dict / SDK-object / malformed inputs.
2. ``run_agent.AIAgent`` re-exports them faithfully, so moving the canonical
   implementation into a leaf module did not change any observable behaviour.

Plus the load-bearing performance guarantee that motivated this module: the hot
``sanitize_api_messages`` path must NOT drag in the heavy ``run_agent`` import
cascade.  The conversation-loop flame graph attributed 44.28% of the run to
``sanitize_api_messages`` solely because its old ``_ra()`` helper triggered the
first ``import run_agent`` (which pulls in the entire tool tree).  A subprocess
test proves that link is severed.
"""

import subprocess
import sys
import types
from pathlib import Path

import pytest

from agent.message_utils import (
    EMPTY_CONTENT_ROLES,
    EMPTY_NAME_SENTINEL,
    STUB_RESULT_CONTENT,
    VALID_API_ROLES,
    assistant_has_payload,
    get_tool_call_function,
    get_tool_call_function_and_id,
    get_tool_call_id,
    get_tool_call_name,
    is_blank_name,
    is_empty_content_droppable,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _obj_tc(cid, name, *, use_call_id=False):
    fn = types.SimpleNamespace(name=name, arguments="{}")
    if use_call_id:
        return types.SimpleNamespace(call_id=cid, function=fn)
    return types.SimpleNamespace(id=cid, function=fn)


# ---------------------------------------------------------------------------
# get_tool_call_id
# ---------------------------------------------------------------------------

class TestGetToolCallId:
    def test_dict_prefers_call_id_over_id(self):
        assert get_tool_call_id({"call_id": "cx", "id": "iy"}) == "cx"

    def test_dict_falls_back_to_id(self):
        assert get_tool_call_id({"id": "call_123"}) == "call_123"

    def test_dict_strips_whitespace(self):
        assert get_tool_call_id({"id": "  call_9 "}) == "call_9"

    def test_dict_none_and_missing_yield_empty(self):
        assert get_tool_call_id({"id": None}) == ""
        assert get_tool_call_id({"function": {}}) == ""

    def test_object_id_and_call_id(self):
        assert get_tool_call_id(_obj_tc("c1", "t")) == "c1"
        assert get_tool_call_id(_obj_tc("c2", "t", use_call_id=True)) == "c2"

    def test_object_missing_yields_empty(self):
        assert get_tool_call_id(types.SimpleNamespace(function=None)) == ""


# ---------------------------------------------------------------------------
# get_tool_call_name / get_tool_call_function / is_blank_name
# ---------------------------------------------------------------------------

class TestGetToolCallName:
    def test_dict_function_name(self):
        assert get_tool_call_name({"function": {"name": "read_file"}}) == "read_file"

    def test_dict_no_function_or_bad_function(self):
        assert get_tool_call_name({"id": "c"}) == ""
        assert get_tool_call_name({"id": "c", "function": None}) == ""

    def test_object_function_name(self):
        assert get_tool_call_name(_obj_tc("c", "terminal")) == "terminal"

    def test_object_missing_function_name(self):
        assert get_tool_call_name(types.SimpleNamespace(function=None)) == ""

    def test_function_extraction_returns_container_and_raw_name(self):
        fn, name = get_tool_call_function({"function": {"name": "  "}})
        assert name == "  "
        assert isinstance(fn, dict)

    @pytest.mark.parametrize("name,blank", [
        ("", True), ("   ", True), (None, True), ("x", False), (" x ", False),
    ])
    def test_is_blank_name(self, name, blank):
        assert is_blank_name(name) is blank


# ---------------------------------------------------------------------------
# get_tool_call_function_and_id  (fused single-dispatch hot-path accessor)
#
# ``sanitize_api_messages`` runs before every LLM request and used to pay two
# separate ``isinstance(tc, dict)`` dispatches per tool_call (get_tool_call_
# function + get_tool_call_id). The fused accessor folds them into ONE dispatch;
# these tests pin that it stays byte-for-byte equivalent to calling both — a
# performance optimization that must never change observable behaviour.
# ---------------------------------------------------------------------------

class TestGetToolCallFunctionAndId:
    @pytest.mark.parametrize("tc", [
        {"call_id": "c1", "id": "i1", "function": {"name": "foo", "arguments": "{}"}},
        {"id": "i2", "function": {"name": "", "arguments": "{}"}},
        {"function": {"name": "bar"}},
        {"id": "  pad  ", "function": None},
        {"id": None, "function": {}},
        {},
        _obj_tc("oc", "baz", use_call_id=True),
        _obj_tc("oi", "  "),
        types.SimpleNamespace(function=None),
        types.SimpleNamespace(id="only", function=None),
        "not-a-tool-call",
        123,
        None,
    ])
    def test_isinstance_caching(self, tc):
        """The fused type-dispatch accessor returns exactly what the two separate
        accessors return, across dict / SDK-object / malformed inputs."""
        fn_sep, name_sep = get_tool_call_function(tc)
        cid_sep = get_tool_call_id(tc)
        fn, name, cid = get_tool_call_function_and_id(tc)
        assert fn is fn_sep or fn == fn_sep
        assert name == name_sep
        assert cid == cid_sep

    def test_dict_extracts_all_three(self):
        fn, name, cid = get_tool_call_function_and_id(
            {"call_id": "c", "function": {"name": "read_file"}}
        )
        assert isinstance(fn, dict) and name == "read_file" and cid == "c"

    def test_object_prefers_call_id_and_strips(self):
        fn, name, cid = get_tool_call_function_and_id(
            _obj_tc("  cx  ", "terminal", use_call_id=True)
        )
        assert name == "terminal" and cid == "cx"


# ---------------------------------------------------------------------------
# assistant_has_payload / is_empty_content_droppable
# ---------------------------------------------------------------------------

class TestPayloadAndEmptyContent:
    @pytest.mark.parametrize("field", [
        "tool_calls", "codex_reasoning_items", "codex_message_items", "reasoning_content",
    ])
    def test_payload_field_keeps_assistant_alive(self, field):
        assert assistant_has_payload({"role": "assistant", "content": "", field: [1]}) is True

    def test_no_payload(self):
        assert assistant_has_payload({"role": "assistant", "content": ""}) is False
        # Falsy payload values do not count as payload.
        assert assistant_has_payload({"role": "assistant", "tool_calls": []}) is False

    def test_empty_droppable_true_for_bare_empty_roles(self):
        for role in ("assistant", "user", "function"):
            assert is_empty_content_droppable({"role": role, "content": ""}) is True

    def test_empty_droppable_false_for_other_roles(self):
        for role in ("system", "tool", "developer"):
            assert is_empty_content_droppable({"role": role, "content": ""}) is False

    def test_none_content_is_not_droppable(self):
        # ``None`` content carries tool_calls and must survive.
        msg = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "c", "function": {"name": "t"}}]}
        assert is_empty_content_droppable(msg) is False

    def test_assistant_with_payload_is_not_droppable(self):
        msg = {"role": "assistant", "content": "",
               "tool_calls": [{"id": "c", "function": {"name": "t"}}]}
        assert is_empty_content_droppable(msg) is False

    def test_role_argument_avoids_relookup(self):
        # Passing role in must agree with reading it from the dict.
        msg = {"role": "user", "content": ""}
        assert is_empty_content_droppable(msg, "user") is True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_constants_are_sane():
    assert "system" in VALID_API_ROLES and "assistant" in VALID_API_ROLES
    assert EMPTY_CONTENT_ROLES <= VALID_API_ROLES
    assert "tool" not in EMPTY_CONTENT_ROLES  # tool results are reconciled, not dropped
    assert EMPTY_NAME_SENTINEL and isinstance(EMPTY_NAME_SENTINEL, str)
    assert STUB_RESULT_CONTENT and isinstance(STUB_RESULT_CONTENT, str)


# ---------------------------------------------------------------------------
# Invariant: AIAgent re-exports agree with the leaf module (single source).
# ---------------------------------------------------------------------------

class TestAIAgentReexportAgreement:
    """Importing ``run_agent`` here is fine — it runs as its own test; the
    subprocess guarantee below covers the perf-critical no-import property."""

    def test_valid_roles_alias(self):
        from run_agent import AIAgent
        assert AIAgent._VALID_API_ROLES is VALID_API_ROLES

    def test_static_forwarders_match(self):
        from run_agent import AIAgent
        samples = [
            {"call_id": "cx", "id": "iy", "function": {"name": "read_file"}},
            {"id": "call_1", "function": None},
            _obj_tc("c9", "terminal"),
            types.SimpleNamespace(function=None),
        ]
        for tc in samples:
            assert AIAgent._get_tool_call_id_static(tc) == get_tool_call_id(tc)
            assert AIAgent._get_tool_call_name_static(tc) == get_tool_call_name(tc)


# ---------------------------------------------------------------------------
# Performance guarantee: the sanitizer must not import run_agent.
# ---------------------------------------------------------------------------

def test_message_utils_import_is_light():
    """Importing the leaf module in a fresh interpreter pulls in neither
    ``run_agent`` nor the tool tree (``model_tools``)."""
    code = (
        "import sys\n"
        "import agent.message_utils  # noqa: F401\n"
        "assert 'run_agent' not in sys.modules, 'message_utils imported run_agent'\n"
        "assert 'model_tools' not in sys.modules, 'message_utils imported model_tools'\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_sanitize_does_not_import_run_agent():
    """The hot pre-call sanitizer must run WITHOUT importing ``run_agent``.

    This is the regression guard for the conversation-loop flame-graph hotspot:
    ``sanitize_api_messages`` used to reach ``run_agent`` through ``_ra()`` and
    so the first call paid the full ``run_agent`` import cascade (44.28% of the
    profiled run).  We import the sanitizer, run it on a payload that exercises
    every branch (role filter, empty-name repair, orphan drop, stub inject,
    empty-content drop), and assert ``run_agent`` never landed in ``sys.modules``.
    """
    code = (
        "import sys\n"
        "from agent.agent_runtime_helpers import sanitize_api_messages\n"
        "msgs = [\n"
        "    {'role': 'session_meta', 'content': 'x'},\n"
        "    {'role': 'user', 'content': ''},\n"
        "    {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {'name': ''}}]},\n"
        "    {'role': 'tool', 'tool_call_id': 'c_orphan', 'content': 'r'},\n"
        "]\n"
        "out = sanitize_api_messages(msgs)\n"
        "assert 'run_agent' not in sys.modules, 'sanitize imported run_agent!'\n"
        "assert all(m.get('role') in "
        "{'system','user','assistant','tool','function','developer'} for m in out)\n"
        "print('OK', len(out))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout
