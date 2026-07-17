"""Quick smoke tests for agent_swarm and swarm_mode modules."""

from agent.swarm_mode import SwarmMode, SwarmTrigger
from tools.agent_swarm import (
    create_agent_swarm_specs,
    validate_swarm_args,
    render_swarm_results,
)

# ── SwarmMode tests ───────────────────────────────────────────────────

def test_swarm_mode():
    mode = SwarmMode()
    assert not mode.is_active
    assert mode.trigger is None
    assert mode.trigger_name() == "inactive"

    mode.enter(SwarmTrigger.MANUAL)
    assert mode.is_active
    assert mode.trigger == SwarmTrigger.MANUAL
    assert mode.trigger_name() == "manual"
    assert not mode.should_auto_exit

    mode.exit()
    assert not mode.is_active

    # Auto-exit triggers
    mode.enter(SwarmTrigger.TASK)
    assert mode.should_auto_exit
    mode.exit()

    mode.enter(SwarmTrigger.TOOL)
    assert mode.should_auto_exit
    mode.exit()

    # No-op double enter
    mode.enter(SwarmTrigger.MANUAL)
    mode.enter(SwarmTrigger.TASK)  # no-op
    assert mode.trigger == SwarmTrigger.MANUAL
    mode.exit()
    assert not mode.is_active


# ── Validation tests ──────────────────────────────────────────────────

def test_validate_swarm_args():
    # Both missing
    assert validate_swarm_args(None, None, None) is not None
    # Missing prompt_template
    assert validate_swarm_args(["a"], None, None) is not None
    # Missing {{item}}
    assert validate_swarm_args(["a"], "no placeholder", None) is not None
    # Valid spawn
    assert validate_swarm_args(["a"], "{{item}}", None) is None
    # Valid resume
    assert validate_swarm_args(None, None, {"id": "continue"}) is None


# ── Spec building tests ───────────────────────────────────────────────

def test_create_specs_spawn_only():
    specs = create_agent_swarm_specs(
        items=["file1.py", "file2.py"],
        prompt_template="Refactor {{item}}",
        resume_agent_ids=None,
    )
    assert len(specs) == 2
    assert specs[0].kind == "spawn"
    assert specs[0].prompt == "Refactor file1.py"
    assert specs[1].prompt == "Refactor file2.py"
    assert specs[0].index == 1
    assert specs[1].index == 2


def test_create_specs_resume_first():
    specs = create_agent_swarm_specs(
        items=["file2.py"],
        prompt_template="Refactor {{item}}",
        resume_agent_ids={"sa-0-abc": "continue fix"},
    )
    assert len(specs) == 2
    assert specs[0].kind == "resume"
    assert specs[0].agent_id == "sa-0-abc"
    assert specs[0].prompt == "continue fix"
    assert specs[1].kind == "spawn"
    assert specs[1].prompt == "Refactor file2.py"


def test_create_specs_duplicate_detection():
    try:
        create_agent_swarm_specs(
            items=["same", "same"],
            prompt_template="Refactor {{item}}",
            resume_agent_ids=None,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


# ── XML rendering tests ───────────────────────────────────────────────

def test_render_swarm_results():
    results = [
        {
            "status": "completed",
            "summary": "Done!",
            "agent_id": "sa-0-a1b2",
            "item": "file1.py",
            "kind": "spawn",
            "index": 1,
        },
        {
            "status": "error",
            "error": "Timeout",
            "agent_id": "sa-1-c3d4",
            "item": "file2.py",
            "kind": "spawn",
            "index": 2,
        },
    ]
    xml = render_swarm_results(results)
    assert "<agent_swarm_result>" in xml
    assert "<summary>total: 2, completed: 1, failed: 1, aborted: 0</summary>" in xml
    assert "<resume_hint>" in xml
    assert "sa-0-a1b2" in xml
    assert "sa-1-c3d4" in xml
    assert 'outcome="completed"' in xml
    assert 'outcome="error"' in xml


# ── Swarm scheduler tests ─────────────────────────────────────────────

def test_swarm_scheduler_empty():
    """SwarmBatchScheduler with no specs should return empty results."""
    from tools.swarm_scheduler import SwarmBatchScheduler

    scheduler = SwarmBatchScheduler(
        specs=[],
        spawn_runner=lambda **kw: {"status": "completed"},
        resume_runner=lambda **kw: {"status": "completed"},
    )
    results = scheduler.run()
    assert results == []


def test_swarm_scheduler_single():
    """SwarmBatchScheduler with one spec."""
    from tools.swarm_scheduler import SwarmBatchScheduler
    from tools.agent_swarm import create_agent_swarm_specs

    specs = create_agent_swarm_specs(
        items=["hello"],
        prompt_template="Say {{item}}",
        resume_agent_ids=None,
    )
    results_store = []

    def on_result(idx, result):
        results_store.append((idx, result))

    scheduler = SwarmBatchScheduler(
        specs=specs,
        spawn_runner=lambda goal, **kw: {
            "status": "completed",
            "summary": f"Ran: {goal}",
        },
        resume_runner=lambda **kw: {"status": "completed"},
        on_result=on_result,
    )
    results = scheduler.run()
    assert len(results) == 1
    assert results[0]["status"] == "completed"
    assert len(results_store) == 1


# ── Subagent runner import guard tests ─────────────────────────────────
#
# Regression: agent_swarm's build_and_run_subagent() does a lazy import:
#   from tools.delegate_tool import _build_child_agent, _run_child_turn
# The function _run_child_turn was never implemented in delegate_tool.py,
# causing ImportError whenever agent_swarm tried to spawn a subagent.
# These tests verify the missing-function gap is closed.

import pytest


def test_build_and_run_subagent_imports_exist():
    """Verify _run_child_turn is importable from delegate_tool.

    Before the fix, this raised:
      ImportError: cannot import name '_run_child_turn' from 'tools.delegate_tool'
    """
    from tools.delegate_tool import (
        _build_child_agent,
        _run_child_turn,
    )
    # Both must be callable (not None)
    assert callable(_build_child_agent) or hasattr(_build_child_agent, "__wrapped__")
    assert callable(_run_child_turn) or hasattr(_run_child_turn, "__wrapped__")


def test_build_and_run_subagent_import_error_regression():
    """Trigger the exact lazy import path that was broken.

    Before the fix, calling build_and_run_subagent() would raise
    ImportError('cannot import name _run_child_turn'), halting agent_swarm.
    After the fix, the import succeeds and the function proceeds to the
    actual subagent-building logic (which may fail cleanly for other
    reasons like missing parent_agent, but NOT with ImportError).
    """
    from tools.subagent_runner import build_and_run_subagent

    try:
        build_and_run_subagent(goal="test", parent_agent=None)
    except ImportError as e:
        if "_run_child_turn" in str(e):
            pytest.fail(
                f"BUG STILL PRESENT: ImportError for _run_child_turn — "
                f"the missing function was never added to delegate_tool.py: {e}"
            )
        # Other ImportErrors (e.g. missing deps) are expected in unit test
        # context without a full agent environment
    except (TypeError, AttributeError, ValueError):
        pass  # Expected: _run_child_turn needs proper agent objects, but
        # import succeeded — the fix is confirmed for the import layer


# ── Credential inheritance parity tests ─────────────────────────────
#
# Regression: agent_swarm subagents must use the same credential resolution
# path as delegate_task subagents. Previously, agent_swarm did NOT call
# _resolve_delegation_credentials(), so delegation.base_url / delegation.provider
# config was silently ignored, causing HTTP 401 when the parent uses a custom
# base URL with a non-OpenAI key.


def test_build_and_run_subagent_calls_credential_resolution():
    """Verify build_and_run_subagent calls _resolve_delegation_credentials
    and passes the resolved overrides to _build_child_agent.

    Regression: agent_swarm previously ignored delegation.* config keys.
    """
    from unittest.mock import MagicMock, patch

    # Patch _load_config to return a delegation dict with a custom base_url
    mock_cfg = {"base_url": "https://api.deepseek.com/v1"}

    # We expect _resolve_delegation_credentials to receive cfg + parent_agent
    # and return a credential dict matching the deepseek endpoint.
    expected_creds = {
        "model": None,
        "provider": "custom",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": None,
        "api_mode": "chat_completions",
    }

    parent = MagicMock()
    parent.base_url = "https://api.openai.com/v1"
    parent.api_key = "sk-openai-real"
    parent.provider = "openai-api"
    parent.api_mode = "chat_completions"
    parent.model = "gpt-4"

    # _load_config and _resolve_delegation_credentials are lazily imported
    # inside build_and_run_subagent() from tools.delegate_tool, so we must
    # patch them at their definition site (tools.delegate_tool).
    with patch(
        "tools.delegate_tool._load_config", return_value=mock_cfg
    ):
        with patch(
            "tools.delegate_tool._resolve_delegation_credentials",
            return_value=expected_creds,
        ) as mock_resolve:
            with patch(
                "tools.delegate_tool._build_child_agent",
                return_value=MagicMock(),
            ) as mock_build:
                with patch(
                    "tools.delegate_tool._run_child_turn",
                    return_value={"status": "completed", "summary": "ok"},
                ):
                    from tools.subagent_runner import build_and_run_subagent

                    result = build_and_run_subagent(
                        goal="test goal",
                        parent_agent=parent,
                    )

    # _resolve_delegation_credentials MUST be called with cfg and parent_agent
    mock_resolve.assert_called_once()
    call_args = mock_resolve.call_args
    # First arg is the cfg dict, second is parent_agent
    assert call_args[0][0] == mock_cfg, (
        f"Expected cfg={mock_cfg!r}, got {call_args[0][0]!r}"
    )
    assert call_args[0][1] is parent, (
        f"Expected parent_agent=parent, got {call_args[0][1]!r}"
    )

    # _build_child_agent MUST receive the resolved override_* params
    mock_build.assert_called_once()
    build_kwargs = mock_build.call_args.kwargs
    assert build_kwargs.get("override_provider") == expected_creds["provider"], (
        f"override_provider: expected {expected_creds['provider']!r}, "
        f"got {build_kwargs.get('override_provider')!r}"
    )
    assert build_kwargs.get("override_base_url") == expected_creds["base_url"], (
        f"override_base_url: expected {expected_creds['base_url']!r}, "
        f"got {build_kwargs.get('override_base_url')!r}"
    )
    assert build_kwargs.get("override_api_key") == expected_creds["api_key"], (
        f"override_api_key: expected {expected_creds['api_key']!r}, "
        f"got {build_kwargs.get('override_api_key')!r}"
    )
    assert build_kwargs.get("override_api_mode") == expected_creds["api_mode"], (
        f"override_api_mode: expected {expected_creds['api_mode']!r}, "
        f"got {build_kwargs.get('override_api_mode')!r}"
    )


def test_build_and_run_subagent_no_delegation_config_inherits_parent():
    """When delegation config has no base_url/provider, child inherits
    from parent (all override_* values are None).

    This ensures the default (no delegation config) behaviour is unchanged.
    """
    from unittest.mock import MagicMock, patch

    # No delegation config = empty dict -> _resolve_delegation_credentials
    # returns None for all fields
    expected_creds = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    parent = MagicMock()
    parent.base_url = "https://api.openai.com/v1"
    parent.api_key = "sk-real-key"
    parent.provider = "openai-api"
    parent.api_mode = "chat_completions"
    parent.model = "gpt-4"

    with patch(
        "tools.delegate_tool._load_config", return_value={}
    ):
        with patch(
            "tools.delegate_tool._resolve_delegation_credentials",
            return_value=expected_creds,
        ):
            with patch(
                "tools.delegate_tool._build_child_agent",
                return_value=MagicMock(),
            ) as mock_build:
                with patch(
                    "tools.delegate_tool._run_child_turn",
                    return_value={"status": "completed", "summary": "ok"},
                ):
                    from tools.subagent_runner import build_and_run_subagent

                    result = build_and_run_subagent(
                        goal="test goal",
                        parent_agent=parent,
                    )

    mock_build.assert_called_once()
    build_kwargs = mock_build.call_args.kwargs
    # All overrides must be None (child inherits from parent)
    assert build_kwargs.get("override_provider") is None
    assert build_kwargs.get("override_base_url") is None
    assert build_kwargs.get("override_api_key") is None
    assert build_kwargs.get("override_api_mode") is None
    # Model should also be None (inherit from parent)
    assert build_kwargs.get("model") is None


# ── _inherit_parent_base_url tests ─────────────────────────────────────

def test_inherit_parent_base_url_empty_fallback_uses_client_kwargs():
    """When fallback_base_url is empty but parent._client_kwargs has a
    live URL, _inherit_parent_base_url returns the live URL and logs a
    warning.

    Regression: an empty agent.base_url could silently redirect subagents
    to the wrong endpoint (e.g. OpenAI default instead of DeepSeek).
    """
    from tools.delegate_tool import _inherit_parent_base_url
    from unittest.mock import MagicMock, PropertyMock

    parent = MagicMock()
    parent.base_url = ""  # empty/stale base_url
    # _client_kwargs has the ACTUAL endpoint the parent uses
    parent._client_kwargs = {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-deepseek-key",
    }

    # The fallback (from parent_agent.base_url) is empty
    result = _inherit_parent_base_url(parent, fallback_base_url="")

    # Should return the _client_kwargs URL, not the empty fallback
    assert result == "https://api.deepseek.com/v1", (
        f"Expected 'https://api.deepseek.com/v1', got {result!r}"
    )


def test_inherit_parent_base_url_no_mismatch_returns_fallback():
    """When parent._client_kwargs matches fallback_base_url, the
    fallback is returned unchanged (no override needed).
    """
    from tools.delegate_tool import _inherit_parent_base_url
    from unittest.mock import MagicMock

    parent = MagicMock()
    parent.base_url = "https://api.deepseek.com/v1"
    parent._client_kwargs = {
        "base_url": "https://api.deepseek.com/v1",
    }
    parent.client = None

    result = _inherit_parent_base_url(parent, fallback_base_url="https://api.deepseek.com/v1")
    assert result == "https://api.deepseek.com/v1"


def test_inherit_parent_base_url_no_client_kwargs_returns_fallback():
    """When parent has no _client_kwargs, the fallback is returned."""
    from tools.delegate_tool import _inherit_parent_base_url
    from unittest.mock import MagicMock

    parent = MagicMock()
    parent.base_url = "https://api.openai.com/v1"
    # No _client_kwargs attribute

    result = _inherit_parent_base_url(parent, fallback_base_url="https://api.openai.com/v1")
    assert result == "https://api.openai.com/v1"


if __name__ == "__main__":
    test_swarm_mode()
    print("OK: test_swarm_mode")

    test_validate_swarm_args()
    print("OK: test_validate_swarm_args")

    test_create_specs_spawn_only()
    print("OK: test_create_specs_spawn_only")

    test_create_specs_resume_first()
    print("OK: test_create_specs_resume_first")

    test_create_specs_duplicate_detection()
    print("OK: test_create_specs_duplicate_detection")

    test_render_swarm_results()
    print("OK: test_render_swarm_results")

    test_swarm_scheduler_empty()
    print("OK: test_swarm_scheduler_empty")

    test_swarm_scheduler_single()
    print("OK: test_swarm_scheduler_single")

    test_build_and_run_subagent_imports_exist()
    print("OK: test_build_and_run_subagent_imports_exist")

    test_build_and_run_subagent_import_error_regression()
    print("OK: test_build_and_run_subagent_import_error_regression")

    print("\nAll smoke tests passed! 🐝")
