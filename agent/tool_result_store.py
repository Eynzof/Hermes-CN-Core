"""Memory-bounded tool-result buffer with transparent disk spill.

Long-running gateway sessions can accumulate hundreds of tool results.
Most are small, but a handful of large ones (terminal dumps, whole-file
reads, ``web_extract`` payloads) dominate the resident-set footprint.
:class:`ToolResultStore` keeps small results in memory and *streams*
large ones to disk under ``HERMES_HOME`` so process memory stays bounded
regardless of how long a session runs.  Retrieval is transparent:
``store.get(i)`` returns the original result whether it lives in memory or
on disk, so callers never need to know where a result was parked.

How this differs from :mod:`tools.tool_result_storage`
------------------------------------------------------
``tools/tool_result_storage.py`` rewrites the *in-context message* with a
preview + sandbox path so the **model** sees a truncated result and the
conversation stays under the context window.  This module is orthogonal:
it bounds the **process** memory of a raw-result accumulator without
changing what the model sees.  The two compose — a caller can spill an
oversized result out of the request *and* keep a bounded local copy for
diagnostics, trajectories, or replay without paying for it in RSS.

Design notes
------------
* ``__slots__`` on both classes keeps per-instance overhead low.  Tool
  results are among the most frequently allocated agent-loop objects
  (a single turn can fan out into dozens of parallel tool calls), and
  dropping the per-object ``__dict__`` saves memory *and* GC scan time.
* Spilled results do **not** count against the in-memory ceiling — only
  the tiny bookkeeping slot stays resident.  (An earlier sketch of this
  store incremented the memory counter by an approximate path size on
  spill and never decremented it, so a busy session's accounting drifted
  until nothing more could ever be admitted to memory.)
* Thread-safe: a single ``RLock`` guards all mutation so a background
  review fork and the main loop can share one store.
"""

from __future__ import annotations

import pybase64 as base64
import orjson
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

# A single result at or above this many bytes is streamed straight to disk
# instead of being held in memory.  100 KB matches the default per-tool
# output cap used elsewhere in the agent, so anything a tool is allowed to
# return whole is small enough to keep resident.
DEFAULT_SPILL_THRESHOLD_BYTES = 100 * 1024  # 100 KB

# Aggregate in-memory ceiling.  Once the resident results exceed this,
# subsequent additions spill to disk even when the individual result is
# below the per-result threshold.  Keeps a long run of medium results from
# combining into an unbounded footprint.
DEFAULT_MAX_MEMORY_BYTES = 10 * 1024 * 1024  # 10 MB


def measure_result_bytes(result: Any) -> int:
    """Best-effort UTF-8 byte size of a tool-result payload.

    Strings and bytes are measured directly; everything else is measured
    through its JSON encoding (the shape tool results actually take on the
    wire).  Falls back to ``str()`` for the rare non-JSON-serialisable
    object so measurement never raises.
    """
    if isinstance(result, (bytes, bytearray)):
        return len(result)
    if isinstance(result, str):
        return len(result.encode("utf-8", "replace"))
    try:
        return len(orjson.dumps(result).decode('utf-8').encode("utf-8", "replace"))
    except (TypeError, ValueError):
        return len(str(result).encode("utf-8", "replace"))


class ToolCallResult:
    """Lightweight record of a single tool invocation.

    ``__slots__`` keeps per-instance overhead low: these records are among
    the most frequently allocated agent-loop objects, so dropping the
    per-object ``__dict__`` (~0.5 KB on CPython) both shrinks the footprint
    and speeds attribute access and GC scanning.
    """

    __slots__ = ("name", "arguments", "result", "duration_ms", "error")

    def __init__(
        self,
        name: str,
        arguments: Any = None,
        result: Any = None,
        duration_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        self.name = name
        self.arguments = arguments
        self.result = result
        self.duration_ms = duration_ms
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - trivial
        status = "error" if self.error else "ok"
        return (
            f"ToolCallResult(name={self.name!r}, {status}, "
            f"{self.duration_ms:.0f}ms)"
        )


class _Slot:
    """Bookkeeping for one stored result. Slotted to stay tiny in memory."""

    __slots__ = ("location", "size", "value", "path")

    def __init__(
        self,
        location: str,
        size: int,
        value: Any = None,
        path: Optional[Path] = None,
    ) -> None:
        self.location = location  # "memory" | "disk"
        self.size = size
        self.value = value        # populated only for in-memory slots
        self.path = path          # populated only for spilled slots


class ToolResultStore:
    """A memory-bounded, disk-spilling append store for tool results.

    Parameters
    ----------
    max_memory_bytes:
        Aggregate ceiling for resident results.  When adding a result would
        push the resident total past this, the result spills to disk.  Pass
        ``0`` to disable the aggregate cap.
    spill_threshold_bytes:
        Any single result at or above this size spills immediately,
        regardless of the aggregate cap.  Pass ``0`` to disable the
        per-result trigger.
    spill_dir:
        Directory for spilled payloads.  Defaults to a unique subdirectory
        under ``get_hermes_home()/cache/tool-results`` (profile-safe).  The
        directory is created lazily on first spill and removed by
        :meth:`close`.
    """

    __slots__ = (
        "_slots",
        "_max_memory",
        "_spill_threshold",
        "_memory_bytes",
        "_spilled_bytes",
        "_spill_dir",
        "_spill_dir_ready",
        "_seq",
        "_closed",
        "_lock",
    )

    def __init__(
        self,
        max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES,
        spill_threshold_bytes: int = DEFAULT_SPILL_THRESHOLD_BYTES,
        spill_dir: Optional[os.PathLike] = None,
    ) -> None:
        self._slots: List[_Slot] = []
        self._max_memory = max(0, int(max_memory_bytes))
        self._spill_threshold = max(0, int(spill_threshold_bytes))
        self._memory_bytes = 0
        self._spilled_bytes = 0
        self._spill_dir: Optional[Path] = Path(spill_dir) if spill_dir else None
        self._spill_dir_ready = False
        self._seq = 0
        self._closed = False
        self._lock = threading.RLock()

    # ── introspection ────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._slots)

    @property
    def memory_bytes(self) -> int:
        """Bytes of result payload currently held in memory."""
        return self._memory_bytes

    @property
    def spilled_bytes(self) -> int:
        """Bytes of result payload currently parked on disk."""
        return self._spilled_bytes

    @property
    def total_bytes(self) -> int:
        """Total payload bytes tracked (memory + disk)."""
        return self._memory_bytes + self._spilled_bytes

    @property
    def memory_count(self) -> int:
        return sum(1 for s in self._slots if s.location == "memory")

    @property
    def spilled_count(self) -> int:
        return sum(1 for s in self._slots if s.location == "disk")

    # ── mutation ─────────────────────────────────────────────────────────
    def add(self, result: Any) -> int:
        """Append ``result`` and return its index.

        The result is held in memory when it is small and the aggregate cap
        has headroom; otherwise it streams to disk.  Either way, ``get()``
        returns the original object.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("ToolResultStore is closed")
            size = measure_result_bytes(result)

            spill_by_size = self._spill_threshold and size >= self._spill_threshold
            spill_by_cap = (
                self._max_memory and self._memory_bytes + size > self._max_memory
            )
            # A result larger than the whole cap must spill (it can never
            # fit), even if the per-result trigger is disabled.
            if spill_by_size or spill_by_cap:
                path = self._write_to_disk(result)
                self._slots.append(_Slot("disk", size, path=path))
                self._spilled_bytes += size
            else:
                self._slots.append(_Slot("memory", size, value=result))
                self._memory_bytes += size
            return len(self._slots) - 1

    def get(self, index: int) -> Any:
        """Return the stored result at ``index`` (reading back from disk if spilled)."""
        with self._lock:
            slot = self._slots[index]
            if slot.location == "memory":
                return slot.value
            return self._read_from_disk(slot.path)

    def iter_results(self) -> Iterator[Any]:
        """Yield every stored result in insertion order."""
        # Snapshot indices under the lock, then read outside to avoid holding
        # it across disk I/O for a large spilled set.
        with self._lock:
            n = len(self._slots)
        for i in range(n):
            yield self.get(i)

    def clear(self) -> None:
        """Drop all results and delete any spilled files (store stays usable)."""
        with self._lock:
            for slot in self._slots:
                if slot.location == "disk" and slot.path is not None:
                    try:
                        Path(slot.path).unlink(missing_ok=True)
                    except OSError as exc:  # pragma: no cover - best effort
                        logger.debug("Could not remove spilled result %s: %s", slot.path, exc)
            self._slots.clear()
            self._memory_bytes = 0
            self._spilled_bytes = 0

    def close(self) -> None:
        """Clear the store and remove its spill directory."""
        with self._lock:
            self.clear()
            self._closed = True
            if self._spill_dir_ready and self._spill_dir is not None:
                try:
                    # Only remove the directory we created; ignore if callers
                    # dropped other files in a shared dir.
                    self._spill_dir.rmdir()
                except OSError:
                    pass
                self._spill_dir_ready = False

    def __enter__(self) -> "ToolResultStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── disk backing ─────────────────────────────────────────────────────
    def _ensure_spill_dir(self) -> Path:
        if self._spill_dir is None:
            # Import lazily so the module stays cheap to import and honours
            # the active profile's HERMES_HOME set at process start.
            from hermes_constants import get_hermes_home

            self._spill_dir = (
                get_hermes_home()
                / "cache"
                / "tool-results"
                / f"store-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            )
        if not self._spill_dir_ready:
            self._spill_dir.mkdir(parents=True, exist_ok=True)
            self._spill_dir_ready = True
        return self._spill_dir

    def _write_to_disk(self, result: Any) -> Path:
        """Serialise ``result`` to a self-describing JSON envelope on disk."""
        directory = self._ensure_spill_dir()
        self._seq += 1
        path = directory / f"result-{self._seq:06d}.json"

        if isinstance(result, (bytes, bytearray)):
            envelope = {"kind": "bytes", "payload": base64.b64encode(bytes(result)).decode("ascii")}
        elif isinstance(result, str):
            envelope = {"kind": "str", "payload": result}
        else:
            try:
                orjson.dumps(result).decode('utf-8')
                envelope = {"kind": "json", "payload": result}
            except (TypeError, ValueError):
                envelope = {"kind": "str", "payload": str(result)}

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(orjson.dumps(envelope).decode('utf-8'), encoding="utf-8")
        # Atomic-ish publish so a crashed write never leaves a half file that
        # a later read would choke on.
        os.replace(tmp, path)
        return path

    @staticmethod
    def _read_from_disk(path: Optional[Path]) -> Any:
        if path is None:
            return None
        envelope = orjson.loads(Path(path).read_text(encoding="utf-8"))
        kind = envelope.get("kind")
        payload = envelope.get("payload")
        if kind == "bytes":
            return base64.b64decode(payload.encode("ascii"))
        # "str" and "json" both round-trip through the payload as-is.
        return payload
