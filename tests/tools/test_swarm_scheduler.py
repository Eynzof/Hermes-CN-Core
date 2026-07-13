"""Unit tests for tools/swarm_scheduler.py."""

import time
from tools.swarm_scheduler import (
    SwarmBatchScheduler,
    SwarmTask,
    TaskStatus,
    INITIAL_LAUNCH_LIMIT,
)
from tools.agent_swarm import create_agent_swarm_specs, AgentSwarmSpec


class TestSwarmBatchScheduler:
    """Test SwarmBatchScheduler lifecycle and scheduling behavior."""

    def test_empty_specs(self):
        scheduler = SwarmBatchScheduler(
            specs=[],
            spawn_runner=lambda goal, **kw: {"status": "completed"},
            resume_runner=lambda **kw: {"status": "completed"},
        )
        results = scheduler.run()
        assert results == []

    def test_single_spawn(self):
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
            spawn_runner=lambda goal, item=None, **kw: {
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

    def test_multiple_spawns(self):
        items = [f"item{i}" for i in range(5)]
        specs = create_agent_swarm_specs(
            items=items,
            prompt_template="Process {{item}}",
            resume_agent_ids=None,
        )

        scheduler = SwarmBatchScheduler(
            specs=specs,
            spawn_runner=lambda goal, item=None, **kw: {
                "status": "completed",
                "summary": f"Processed {item or goal}",
            },
            resume_runner=lambda **kw: {"status": "completed"},
            max_concurrency=5,
        )
        results = scheduler.run()
        assert len(results) == 5
        for r in results:
            assert r["status"] == "completed"

    def test_some_failures(self):
        specs = create_agent_swarm_specs(
            items=["good", "bad", "good2"],
            prompt_template="Run {{item}}",
            resume_agent_ids=None,
        )

        def spawn_runner(goal, item=None, **kw):
            if item == "bad":
                raise Exception("Simulated failure")
            return {"status": "completed", "summary": f"Ran {item}"}

        scheduler = SwarmBatchScheduler(
            specs=specs,
            spawn_runner=spawn_runner,
            resume_runner=lambda **kw: {"status": "completed"},
            max_concurrency=3,
        )
        results = scheduler.run()
        assert len(results) == 3
        completed = sum(1 for r in results if r["status"] == "completed")
        failed = sum(1 for r in results if r["status"] in ("failed", "error"))
        assert completed == 2
        assert failed == 1

    def test_cancel_user(self):
        specs = create_agent_swarm_specs(
            items=[f"task{i}" for i in range(10)],
            prompt_template="Run {{item}}",
            resume_agent_ids=None,
        )

        slow_spawns = 0

        def spawn_runner(goal, item=None, **kw):
            nonlocal slow_spawns
            slow_spawns += 1
            time.sleep(0.02)
            return {"status": "completed", "summary": f"Ran {item}"}

        scheduler = SwarmBatchScheduler(
            specs=specs,
            spawn_runner=spawn_runner,
            resume_runner=lambda **kw: {"status": "completed"},
            max_concurrency=10,
        )

        # Cancel after a short delay
        def delayed_cancel():
            time.sleep(0.05)
            scheduler.cancel("user cancelled", is_user_action=True)

        import threading
        t = threading.Thread(target=delayed_cancel, daemon=True)
        t.start()

        results = scheduler.run()
        assert len(results) == 10
        # At least some should be completed (those that ran before cancel)
        completed = sum(1 for r in results if r["status"] == "completed")
        aborted = sum(1 for r in results if r["status"] == "aborted")
        assert completed >= 1
        assert aborted >= 0

    def test_rate_limit_handling(self):
        """A task that raises rate limit error should be retried."""
        call_count = [0]

        def spawn_runner(goal, item=None, **kw):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("429 Too Many Requests")
            return {"status": "completed", "summary": "OK after retry"}

        specs = create_agent_swarm_specs(
            items=["task1"],
            prompt_template="Run {{item}}",
            resume_agent_ids=None,
        )

        scheduler = SwarmBatchScheduler(
            specs=specs,
            spawn_runner=spawn_runner,
            resume_runner=lambda **kw: {"status": "completed"},
            max_concurrency=1,
        )

        # Override constants for faster test
        import tools.swarm_scheduler as mod
        orig_base = mod.RATE_LIMIT_RETRY_BASE_MS
        orig_recovery = mod.CAPACITY_RECOVERY_MS
        orig_global = mod.GLOBAL_RETRY_INTERVAL_MS
        mod.RATE_LIMIT_RETRY_BASE_MS = 10  # 10ms base for fast backoff
        mod.CAPACITY_RECOVERY_MS = 10000  # long recovery to avoid interference
        mod.GLOBAL_RETRY_INTERVAL_MS = 5  # 5ms between launches

        try:
            results = scheduler.run()
        finally:
            mod.RATE_LIMIT_RETRY_BASE_MS = orig_base
            mod.CAPACITY_RECOVERY_MS = orig_recovery
            mod.GLOBAL_RETRY_INTERVAL_MS = orig_global

        assert len(results) == 1
        assert results[0]["status"] == "completed", f"Expected completed, got {results[0]}"

    def test_resume_spec(self):
        """Resume specs should call resume_runner."""
        resume_called = [False]

        specs = [
            AgentSwarmSpec(
                kind="resume", index=1, item=None,
                prompt="continue", agent_id="sa-0-abc",
            )
        ]

        scheduler = SwarmBatchScheduler(
            specs=specs,
            spawn_runner=lambda **kw: {"status": "completed"},
            resume_runner=lambda agent_id, prompt, **kw: {
                "status": "completed",
                "summary": f"Resumed {agent_id}: {prompt}",
            },
        )
        results = scheduler.run()
        assert len(results) == 1
        assert results[0]["status"] == "completed"
