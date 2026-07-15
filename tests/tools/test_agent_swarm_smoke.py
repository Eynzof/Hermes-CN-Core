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

    print("\nAll smoke tests passed! 🐝")
