"""Rate-limit-aware batch scheduler for parallel subagent execution.

``SwarmBatchScheduler`` is the core scheduling engine that manages the full
lifecycle of concurrent agent launches with rate-limit recovery.

Normal phase:
  - Burst up to ``INITIAL_LAUNCH_LIMIT`` (5) tasks immediately.
  - Then throttle to 1 launch every ``INITIAL_LAUNCH_INTERVAL_MS`` (700 ms).
  - Optional concurrency cap via ``max_concurrency`` config.

Rate-limit phase (triggered by provider 429):
  - Exponential backoff: ``RATE_LIMIT_RETRY_BASE_MS`` (3 s) * 2^retryCount.
  - Capacity shrinks on each 429, recovers after 3 minutes of quiet.
  - Rate-limited task is requeued at front (not back).
  - If only one task remains unfinished, fail it (don't hang).

Cancellation:
  - User cancel: preserve completed results, mark in-flight / unstarted as aborted.
  - Non-user cancel: reject the entire batch.
  - Per-task timeout: fail only that task; does not enter rate-limit phase.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

# ── Constants ─────────────────────────────────────────────────────────────

INITIAL_LAUNCH_LIMIT = 5
INITIAL_LAUNCH_INTERVAL_MS = 700  # ms between normal-phase launches
RATE_LIMIT_RETRY_BASE_MS = 3000  # base exponential backoff (3 s)
RATE_LIMIT_CAPACITY_SHRINK_INTERVAL_MS = 60000  # shrink capacity once per min
CAPACITY_RECOVERY_MS = 180000  # 3 minutes — capacity recovery window
GLOBAL_RETRY_INTERVAL_MS = 1500  # min spacing between rate-limit-phase launches


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    RATE_LIMITED = "rate_limited"


@dataclass
class SwarmTask:
    """A single task in the swarm batch."""

    spec_index: int
    kind: str  # "spawn" | "resume"
    prompt: str
    item: Optional[str] = None
    agent_id: Optional[str] = None
    subagent_type: Optional[str] = None

    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    retry_ready_at: float = 0.0  # time.time() when retry is allowed
    result: Optional[dict] = None
    abort_signal: Optional[threading.Event] = None


@dataclass
class SwarmBatchState:
    """Mutable state for one swarm batch execution."""

    tasks: list[SwarmTask] = field(default_factory=list)
    active_count: int = 0
    capacity: int = INITIAL_LAUNCH_LIMIT
    retry_count_total: int = 0
    last_rate_limit_at: float = 0.0
    next_launch_at: float = 0.0
    started: bool = False
    cancelled: bool = False
    cancel_reason: str = ""
    last_capacity_shrink_at: float = 0.0
    completed_results: list[dict] = field(default_factory=list)
    launch_count: int = 0
    normal_phase: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)


class SwarmBatchScheduler:
    """Manages concurrent subagent execution with rate-limit recovery.

    Usage::

        scheduler = SwarmBatchScheduler(
            specs=[...],
            spawn_runner=my_spawn_fn,
            resume_runner=my_resume_fn,
            on_result=my_result_callback,
            max_concurrency=8,
        )
        results = scheduler.run()
    """

    def __init__(
        self,
        specs: list["AgentSwarmSpec"],  # noqa: F821
        spawn_runner: Callable,
        resume_runner: Callable,
        on_result: Optional[Callable[[int, dict], None]] = None,
        max_concurrency: int = 3,
        max_subagents: int = 128,
        abort_signal: Optional[threading.Event] = None,
    ):
        self._specs = specs
        self._spawn_runner = spawn_runner
        self._resume_runner = resume_runner
        self._on_result = on_result
        self._max_concurrency = max(max_concurrency, 1)
        self._max_subagents = max_subagents
        self._abort_signal = abort_signal or threading.Event()

        # Build task list
        tasks: list[SwarmTask] = []
        for spec in specs:
            tasks.append(
                SwarmTask(
                    spec_index=spec.index,
                    kind=spec.kind,
                    prompt=spec.prompt,
                    item=spec.item,
                    agent_id=spec.agent_id,
                    subagent_type=spec.subagent_type,
                    abort_signal=threading.Event(),
                )
            )
        self._state = SwarmBatchState(tasks=tasks)
        self._pending: OrderedDict[int, SwarmTask] = OrderedDict()
        for t in tasks:
            self._pending[t.spec_index] = t

        self._logger = None

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def is_done(self) -> bool:
        """True when all tasks have completed, failed, or been aborted."""
        return len(self._state.completed_results) >= len(self._specs)

    # ── Public API ──────────────────────────────────────────────────────

    def run(self) -> list[dict]:
        """Execute all swarm tasks and return ordered results.

        Blocks until all tasks complete, fail, or are cancelled.
        """
        self._state.started = True
        self._schedule_loop()
        return self._ordered_results()

    def cancel(self, reason: str = "cancelled", is_user_action: bool = True):
        """Cancel the batch.

        *User* cancellation (``is_user_action=True``): preserve completed
        results, abort in-flight and pending tasks.

        *Non-user* cancellation (``is_user_action=False``): reject the
        entire batch.
        """
        with self._state.lock:
            if self._state.cancelled:
                return
            self._state.cancelled = True
            self._state.cancel_reason = reason

            for task in self._state.tasks:
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    if is_user_action and task.status == TaskStatus.COMPLETED:
                        continue
                    task.status = TaskStatus.ABORTED
                    task.result = {
                        "status": "aborted",
                        "error": reason,
                        "summary": None,
                    }
                    if task.abort_signal:
                        task.abort_signal.set()
                    if task.spec_index not in {r.get("index") for r in self._state.completed_results}:
                        self._state.completed_results.append(task.result or {
                            "status": "aborted",
                            "error": reason,
                        })

            self._pending.clear()

    # ── Internal: scheduling loop ──────────────────────────────────────

    def _schedule_loop(self):
        """Main scheduling loop.  Blocks until all tasks are done."""
        while not self.is_done:
            if self._abort_signal.is_set():
                self.cancel("parent abort", is_user_action=True)
                break

            with self._state.lock:
                if self._state.cancelled:
                    break

                self._maybe_recover_capacity()
                self._maybe_exit_rate_limit_phase()

                if not self._state.normal_phase:
                    self._rate_limit_cycle()
                else:
                    self._normal_cycle()

            # If there are still pending tasks and we're not launching any
            # right now, sleep briefly to avoid a busy-wait.
            if not self.is_done and self._state.next_launch_at > time.time():
                sleep_for = min(
                    self._state.next_launch_at - time.time(),
                    0.5,  # Max 500 ms sleep
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)

    def _normal_cycle(self):
        """Execute one normal-phase launch cycle."""
        if not self._pending:
            return

        now = time.time()

        # Burst: launch up to INITIAL_LAUNCH_LIMIT immediately
        while (
            self._state.launch_count < INITIAL_LAUNCH_LIMIT
            and self._pending
            and self._state.active_count < self._max_concurrency
        ):
            idx = next(iter(self._pending))
            self._launch_task(idx)
            if not self._pending:
                break

        # Throttle: one launch per interval
        if (
            self._pending
            and self._state.active_count < self._max_concurrency
            and now >= self._state.next_launch_at
        ):
            idx = next(iter(self._pending))
            self._launch_task(idx)
            self._state.next_launch_at = now + (INITIAL_LAUNCH_INTERVAL_MS / 1000.0)

    def _rate_limit_cycle(self):
        """Execute one rate-limit-phase launch cycle."""
        if not self._pending:
            return

        now = time.time()

        # Find the first task whose retry_ready_at has elapsed
        ready_task: Optional[SwarmTask] = None
        ready_idx: Optional[int] = None
        for idx, task in list(self._pending.items()):
            if task.retry_ready_at <= now:
                ready_task = task
                ready_idx = idx
                break

        if ready_task is not None and now >= self._state.next_launch_at:
            if self._state.active_count < self._state.capacity:
                self._launch_task(ready_idx)  # type: ignore[arg-type]
                self._state.next_launch_at = now + (GLOBAL_RETRY_INTERVAL_MS / 1000.0)

    # ── Internal: task launch ──────────────────────────────────────────

    def _launch_task(self, idx: int):
        """Launch a single task in a new thread."""
        task = self._pending.pop(idx, None)
        if task is None:
            return

        task.status = TaskStatus.RUNNING
        self._state.active_count += 1
        self._state.launch_count += 1

        thread = threading.Thread(
            target=self._run_task,
            args=(task,),
            daemon=True,
        )
        thread.start()

    def _run_task(self, task: SwarmTask):
        """Execute a single swarm task."""
        is_resume = task.kind == "resume"
        runner = self._resume_runner if is_resume else self._spawn_runner

        try:
            if is_resume:
                result = runner(
                    agent_id=task.agent_id,
                    prompt=task.prompt,
                )
            else:
                result = runner(
                    goal=task.prompt,
                    item=task.item,
                    subagent_type=task.subagent_type,
                )
            if not isinstance(result, dict):
                result = {"status": "completed", "summary": str(result)[:500]}
        except Exception as e:
            error_msg = str(e)
            # Detect rate-limit / 429
            if "429" in error_msg or "rate_limit" in error_msg.lower():
                self._handle_rate_limit(task, error_msg)
                return
            result = {"status": "error", "error": error_msg}

        self._finalize_task(task, result)

    def _handle_rate_limit(self, task: SwarmTask, error_msg: str):
        """Handle a rate-limited task: backoff and requeue."""
        now = time.time()
        with self._state.lock:
            self._state.retry_count_total += 1
            task.retry_count += 1
            self._state.last_rate_limit_at = now
            self._state.normal_phase = False

            # Exponential backoff: 3s, 6s, 12s, ...
            backoff_ms = RATE_LIMIT_RETRY_BASE_MS * (2 ** (task.retry_count - 1))
            task.retry_ready_at = now + (backoff_ms / 1000.0)

            # Shrink capacity (once per shrink interval)
            if now - self._state.last_capacity_shrink_at >= (
                RATE_LIMIT_CAPACITY_SHRINK_INTERVAL_MS / 1000.0
            ):
                self._state.capacity = max(1, self._state.capacity - 1)
                self._state.last_capacity_shrink_at = now

            self._state.active_count = max(0, self._state.active_count - 1)

            # If this is the only unfinished task, fail it instead of requeuing
            unfinished = sum(
                1
                for t in self._state.tasks
                if t.status
                in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RATE_LIMITED)
            )
            if unfinished <= 1 and len(self._state.tasks) > 1:
                self._finalize_task(
                    task,
                    {
                        "status": "error",
                        "error": (
                            f"Rate-limited and only unfinished task. "
                            f"Retried {task.retry_count} time(s). "
                            f"Last error: {error_msg}"
                        ),
                    },
                )
                return

            # Requeue at front
            task.status = TaskStatus.RATE_LIMITED
            self._pending[task.spec_index] = task

            # Stagger retry launches
            self._state.next_launch_at = max(
                self._state.next_launch_at,
                now + (GLOBAL_RETRY_INTERVAL_MS / 1000.0),
            )

    def _finalize_task(self, task: SwarmTask, result: dict):
        """Record a completed/failed task result."""
        with self._state.lock:
            task.status = (
                TaskStatus.COMPLETED
                if result.get("status") in ("completed", "success")
                else TaskStatus.FAILED
                if result.get("status") in ("error", "failed")
                else TaskStatus.ABORTED
            )
            task.result = result
            result.setdefault("index", task.spec_index)
            result.setdefault("item", task.item)
            result.setdefault("kind", task.kind)
            result.setdefault("agent_id", task.agent_id or result.get("agent_id"))
            self._state.active_count = max(0, self._state.active_count - 1)

            if task.spec_index not in {r.get("index") for r in self._state.completed_results}:
                self._state.completed_results.append(result)

            if self._on_result:
                try:
                    self._on_result(task.spec_index, result)
                except Exception:
                    pass

    # ── Internal: helpers ──────────────────────────────────────────────

    def _maybe_recover_capacity(self):
        """Gradually recover capacity after a quiet period."""
        if self._state.retry_count_total == 0:
            return
        now = time.time()
        if now - self._state.last_rate_limit_at >= (CAPACITY_RECOVERY_MS / 1000.0):
            self._state.capacity = min(
                self._max_concurrency,
                self._state.capacity + 1,
            )

    def _maybe_exit_rate_limit_phase(self):
        """Return to normal phase if the rate-limit storm has passed."""
        if self._state.normal_phase:
            return
        now = time.time()
        # If no rate limit for 2x recovery window and capacity is fully restored
        if now - self._state.last_rate_limit_at >= (CAPACITY_RECOVERY_MS * 2 / 1000.0):
            if self._state.capacity >= INITIAL_LAUNCH_LIMIT:
                self._state.normal_phase = True

    def _ordered_results(self) -> list[dict]:
        """Return results in spec order."""
        ordered = []
        for spec in self._specs:
            for r in self._state.completed_results:
                if r.get("index") == spec.index:
                    ordered.append(r)
                    break
            else:
                ordered.append({
                    "status": "aborted",
                    "error": self._state.cancel_reason or "unknown",
                    "index": spec.index,
                    "item": spec.item,
                    "kind": spec.kind,
                    "summary": None,
                })
        return ordered
