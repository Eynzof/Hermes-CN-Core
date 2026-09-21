"""Todo tool: in-memory, revisioned task list for multi-step work. State lives on the
AIAgent (one per session), is re-injected after context compression, and every write bumps
a monotonic revision so UI clients can reject stale updates. One ``todo_list`` tool: pass
``todos`` to write, omit to read; every call returns the full list. No system-prompt mutation."""

import os
import shutil
import subprocess
import sys

import orjson
import rapidfuzz
from typing import Any, Dict, List, Optional

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
# The list is re-read after every compression (format_for_injection), so unbounded
# content/count would defeat the compression it rides through. Caps apply equally to
# model-authored items and caller-replayed API history.
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
# Max single todo tool-result payload accepted during history hydration, so a forged
# oversized result is dropped before parsing (AIAgent._hydrate_todo_store).
MAX_TODO_RESULT_CHARS = 512_000
# Caps for the optional `notes` / `code` item fields. Same rationale as the
# content cap: oversized notes or verification code on a single item would
# otherwise inflate the post-compression re-injection block without bound.
MAX_TODO_NOTES_CHARS = 16000
MAX_TODO_CODE_CHARS = 16000
# Maximum number of archived (finished-and-dropped) todos kept in state;
# oldest are dropped first.
MAX_ARCHIVED_TODOS = 500
_TRUNCATION_MARKER = "… [truncated]"
# Persisted as ordinary message content; ContextCompressor keys on this stable header to
# tell the synthetic post-compaction row from a real user message.
TODO_INJECTION_HEADER = "[Your active task list was preserved across context compression]"
_STATUS_MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
_ACTIVE_STATUSES = {"pending", "in_progress"}

# Reminder surfaced as the result "message" once the active list is non-empty
# and every item is finished — exact Kimi TodoList wording.
_ALL_DONE_REMINDER = (
    "All todos are done. "
    "Please review the requirements again to ensure nothing is left unfinished."
)


class TodoStore:
    """In-memory todo list, one per AIAgent. List position is priority; items are
    ``{id, content, status, parent?}`` — ``parent`` nests a subtask."""

    def __init__(self):
        self._items: List[Dict[str, str]] = []
        self._revision = 0
        self._archived: List[Dict[str, str]] = []
        self._warnings: List[str] = []
        self._conflict_error: Optional[str] = None

    def _fresh_items(self, todos: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Validate, dedupe and order a whole new list (replace / restore)."""
        return self._normalize_order([self._validate(t) for t in self._dedupe_by_id(todos)])

    def write(
        self,
        todos: List[Dict[str, Any]],
        merge: bool = False,
        auto_fix: bool = True,
    ) -> List[Dict[str, str]]:
        """Replace the list (default) or merge by id; returns the full list after writing.

        ``auto_fix=True`` (default) keeps the first ``in_progress`` item and marks the
        extras completed; ``False`` leaves the conflict in place and records it
        (retrievable via ``pop_conflict_error()``).  Warnings accumulate on
        ``self._warnings`` (``pop_warnings()`` retrieves them).
        """
        self._warnings = []
        self._conflict_error = None
        before = [item.copy() for item in self._items]
        incoming = self._dedupe_by_id(todos)
        old_items = list(self._items)
        old_statuses = {item["id"]: item["status"] for item in old_items}

        # Non-blocking content guards (warnings only).
        self._warnings.extend(self._detect_fuzzy_warnings(incoming, old_items))
        self._warnings.extend(self._detect_duplicate_contents(incoming))

        if merge:
            self._merge(incoming)
        else:
            self._items = self._fresh_items(incoming)
            # Archive terminal items dropped by the replacement: finished
            # work is preserved (bounded), abandoned pending/in_progress
            # items are not.
            if old_items:
                kept_ids = {item["id"] for item in self._items}
                dropped_terminal = [
                    item
                    for item in old_items
                    if item["id"] not in kept_ids
                    and item["status"] in {"completed", "cancelled"}
                ]
                if dropped_terminal:
                    self._archived.extend(dropped_terminal)
            # Enforce the archive bound (oldest dropped first) on every
            # replace-mode write.
            if len(self._archived) > MAX_ARCHIVED_TODOS:
                self._archived = self._archived[-MAX_ARCHIVED_TODOS:]

        del self._items[MAX_TODO_ITEMS:]  # keep the priority head; replays can't grow unbounded
        self._sanitize_parents(self._items)

        # Regression guard: terminal items (completed/cancelled) cannot be
        # re-opened. Any item whose effective new status is pending or
        # in_progress is clamped back to its terminal status with a warning.
        for item in self._items:
            old_status = old_statuses.get(item["id"])
            if (
                old_status in {"completed", "cancelled"}
                and item["status"] in {"pending", "in_progress"}
            ):
                item["status"] = old_status
                self._warnings.append(
                    f"Item '{item['id']}' was {old_status} and cannot be "
                    f"re-opened; clamped back to {old_status}."
                )

        # Verification gate: when an item transitions TO completed and has
        # code, run it. On failure the item reverts to pending with the
        # error recorded in notes. Never raises.
        for item in self._items:
            if (
                item.get("code")
                and item["status"] == "completed"
                and old_statuses.get(item["id"]) != "completed"
            ):
                self._verify_completed_transition(item, self._warnings)

        # Single in_progress enforcement.
        in_progress_ids = [i["id"] for i in self._items if i["status"] == "in_progress"]
        if len(in_progress_ids) > 1:
            if auto_fix:
                fixed = 0
                seen_first = False
                for item in self._items:
                    if item["status"] == "in_progress":
                        if seen_first:
                            item["status"] = "completed"
                            fixed += 1
                        else:
                            seen_first = True
                self._warnings.append(
                    f"Auto-fixed {fixed} extra in_progress item(s) "
                    "(only one item may be in_progress)."
                )
            else:
                self._conflict_error = (
                    "Multiple items are in_progress: "
                    + ", ".join(in_progress_ids)
                    + ". Only one item may be in_progress at a time; mark the "
                    "current item completed before starting another (or set "
                    "auto_fix=True)."
                )

        if self._items != before:
            self._revision += 1
        return [item.copy() for item in self._items]

    def _merge(self, todos: List[Dict[str, Any]]) -> None:
        """Update existing items only in the fields provided; append new ones (validated)."""
        existing = {item["id"]: item for item in self._items}
        for t in self._dedupe_by_id(todos):
            item_id = str(t.get("id", "")).strip()
            if not item_id:
                continue  # can't merge without an id
            cur = existing.get(item_id)
            if cur is None:
                validated = self._validate(t)
                existing[validated["id"]] = validated
                self._items.append(validated)
                continue
            if t.get("content"):
                cur["content"] = self._cap_content(str(t["content"]).strip())
            if t.get("status") and str(t["status"]).strip().lower() in VALID_STATUSES:
                cur["status"] = str(t["status"]).strip().lower()
            if "parent" in t:
                parent = str(t["parent"] or "").strip()
                if parent:
                    cur["parent"] = parent
                else:
                    cur.pop("parent", None)
            # notes/code update only when the incoming value is truthy; None or
            # empty keeps the stored value.
            if t.get("notes"):
                cur["notes"] = self._cap_field(str(t["notes"]).strip(), MAX_TODO_NOTES_CHARS)
            if t.get("code"):
                cur["code"] = self._cap_field(str(t["code"]).strip(), MAX_TODO_CODE_CHARS)
        # Rebuild preserving original order for existing items (first occurrence wins).
        rebuilt = {item["id"]: existing.get(item["id"], item) for item in self._items}
        self._items = self._normalize_order(list(rebuilt.values()))

    def read(self) -> List[Dict[str, str]]:
        """Return a copy of the current list (clears any pending write warnings)."""
        self._warnings = []
        self._conflict_error = None
        return [item.copy() for item in self._items]

    def pop_warnings(self) -> List[str]:
        """Return and clear warnings accumulated by the last write."""
        warnings = list(self._warnings)
        self._warnings = []
        return warnings

    def pop_conflict_error(self) -> Optional[str]:
        """Return and clear the auto_fix=False in_progress conflict error."""
        conflict_error = self._conflict_error
        self._conflict_error = None
        return conflict_error

    def has_items(self) -> bool:
        return bool(self._items)

    def snapshot(self) -> Dict[str, Any]:
        """Full state clients can reconcile atomically."""
        return {"todos": self.read(), "revision": self._revision}

    def restore(self, todos: List[Dict[str, Any]], *, revision: Any = 0) -> List[Dict[str, str]]:
        """Restore a trusted snapshot without manufacturing a new revision."""
        self._items = self._fresh_items(todos)[:MAX_TODO_ITEMS]
        try:
            self._revision = max(0, int(revision or 0))
        except (TypeError, ValueError):
            self._revision = 0
        return self.read()

    def format_for_injection(self) -> Optional[str]:
        """Render the list for post-compression injection, or None if nothing active. Only
        pending/in_progress items are injected — finished ones make the model re-do work after
        compression. A parent is kept (with its real status marker) when any descendant is
        active so subtasks keep context."""
        if not self._items:
            return None
        children: Dict[str, List[Dict[str, str]]] = {}
        for item in self._items:
            if item.get("parent"):
                children.setdefault(item["parent"], []).append(item)

        def render(item: Dict[str, str], depth: int, out: List[str]) -> bool:
            kid_lines: List[str] = []
            has_active_kid = False
            for kid in children.get(item["id"], []):
                has_active_kid |= render(kid, depth + 1, kid_lines)
            keep = item["status"] in _ACTIVE_STATUSES or has_active_kid
            if keep:
                marker = _STATUS_MARKERS.get(item["status"], "[?]")
                out.append(f"{'  ' * depth}- {marker} {item['id']}. "
                           f"{item['content']} ({item['status']})")
                out.extend(kid_lines)
            return keep

        lines = [TODO_INJECTION_HEADER]
        for item in self._items:
            if not item.get("parent"):
                render(item, 0, lines)
        return "\n".join(lines) if len(lines) > 1 else None

    @staticmethod
    def _cap_content(content: str) -> str:
        """Truncate to MAX_TODO_CONTENT_CHARS keeping the head (the actionable part) + marker."""
        if len(content) > MAX_TODO_CONTENT_CHARS:
            return content[:MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _cap_field(value: str, limit: int) -> str:
        """Truncate an oversized optional field (notes/code) to `limit` chars."""
        if len(value) > limit:
            keep = limit - len(_TRUNCATION_MARKER)
            return value[:keep] + _TRUNCATION_MARKER
        return value

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        """Normalize one item to ``{id, content, status, parent?}`` plus optional
        ``notes``/``code`` (placeholders when missing)."""
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip().lower()
        result = {"id": item_id,
                  "content": TodoStore._cap_content(content) if content else "(no description)",
                  "status": status if status in VALID_STATUSES else "pending"}
        parent = str(item.get("parent") or "").strip()
        if parent and parent != item_id:
            result["parent"] = parent
        raw_notes = item.get("notes")
        if raw_notes is not None:
            notes = str(raw_notes).strip()
            if notes:
                result["notes"] = TodoStore._cap_field(notes, MAX_TODO_NOTES_CHARS)
        raw_code = item.get("code")
        if raw_code is not None:
            code = str(raw_code).strip()
            if code:
                result["code"] = TodoStore._cap_field(code, MAX_TODO_CODE_CHARS)
        return result

    @staticmethod
    def _sanitize_parents(items: List[Dict[str, str]]) -> None:
        """Drop dangling parent refs and break cycles in place (such items become roots)."""
        by_id = {item["id"]: item for item in items}
        for item in items:
            if item.get("parent") and item["parent"] not in by_id:
                item.pop("parent", None)
        for item in items:
            seen, node = {item["id"]}, item
            while node.get("parent"):
                if node["parent"] in seen:
                    item.pop("parent", None)
                    break
                seen.add(node["parent"])
                node = by_id[node["parent"]]

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):  # non-dicts get a synthetic key; _validate handles them
            key = str(item.get("id", "")).strip() if isinstance(item, dict) else f"__invalid_{i}"
            last_index[key or "?"] = i
        return [todos[i] for i in sorted(last_index.values())]

    @staticmethod
    def _detect_fuzzy_warnings(
        todos: List[Dict[str, Any]], old_items: List[Dict[str, str]]
    ) -> List[str]:
        """Non-blocking warnings for incoming content that near-matches existing.

        For each incoming item whose content differs from every existing
        item's content, the best existing-content match (rapidfuzz
        token_sort_ratio on lowercased strings, threshold 75) is reported —
        first hit per new item only.
        """
        if not old_items:
            return []
        old_contents = [item["content"] for item in old_items]
        old_content_set = set(old_contents)
        warnings: List[str] = []
        for t in todos:
            if not isinstance(t, dict):
                continue
            content = str(t.get("content", "")).strip()
            if not content or content in old_content_set:
                continue
            matches = rapidfuzz.process.extract(
                content,
                old_contents,
                scorer=rapidfuzz.fuzz.token_sort_ratio,
                limit=1,
                score_cutoff=75.0,
                processor=str.lower,
            )
            if matches:
                old = old_items[matches[0][2]]
                warnings.append(
                    f"'{content}' looks like existing '{old['content']}' "
                    f"(id {old['id']})"
                )
        return warnings

    @staticmethod
    def _detect_duplicate_contents(todos: List[Dict[str, Any]]) -> List[str]:
        """Warn when incoming items share content but carry different ids."""
        warnings: List[str] = []
        first_id_by_content: Dict[str, str] = {}
        warned: set = set()
        for t in todos:
            if not isinstance(t, dict):
                continue
            content = str(t.get("content", "")).strip()
            if not content:
                continue
            item_id = str(t.get("id", "")).strip() or "?"
            first_id = first_id_by_content.get(content)
            if first_id is None:
                first_id_by_content[content] = item_id
            elif first_id != item_id and content not in warned:
                warnings.append(
                    f"Duplicate content across ids {first_id} and {item_id}: "
                    f"'{content}'"
                )
                warned.add(content)
        return warnings

    def _verify_completed_transition(
        self, item: Dict[str, str], warnings: List[str]
    ) -> None:
        """Run an item's verification code when it is marked completed.

        On success the item stays completed (silently). On failure the item
        reverts to pending, the error is appended to notes (capped at 500
        chars) and a warning is recorded. Execution is wrapped so a
        verification bug can never break the write pipeline.
        """
        try:
            success, output = run_verification_code(item.get("code", ""))
        except Exception as exc:
            success, output = False, str(exc)
        if success:
            return
        item["status"] = "pending"
        note = "[verification failed] " + output[:500]
        existing_notes = item.get("notes", "")
        item["notes"] = existing_notes + "\n" + note if existing_notes else note
        warnings.append(
            f"Item '{item['id']}' marked completed failed verification and was "
            f"reverted to pending: {output[:200]}"
        )


    @staticmethod
    def _normalize_order(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Lift the in_progress step ahead of any earlier pending placeholder. Nested lists
        keep authored order — reordering would tear a subtask from its siblings."""
        statuses = [item["status"] for item in items]
        if any(item.get("parent") for item in items) or "in_progress" not in statuses:
            return items
        active_index = statuses.index("in_progress")
        if "pending" not in statuses[:active_index]:
            return items
        normalized = items.copy()
        normalized.insert(statuses.index("pending"), normalized.pop(active_index))
        return normalized


def _verification_argv(code: str) -> List[str]:
    """Resolve a non-shell code string to a subprocess argv.

    Existing .py files run under sys.executable; .sh under bash (fallback
    sh); .ps1 under powershell -File. Anything else is inline Python.

    Under the PyInstaller-frozen CN portable runtime (where sys.executable
    is the Hermes CLI binary, not a standalone python), the returned argv is
    only used for .sh/.ps1; python verification runs in-process via
    ``run_verification_code`` (see ``tools.runtime_compat``).
    """
    lowered = code.lower()
    if lowered.endswith(".py") and os.path.isfile(code):
        return [sys.executable, code]
    if lowered.endswith(".sh") and os.path.isfile(code):
        if shutil.which("bash"):
            return ["bash", code]
        if shutil.which("sh"):
            return ["sh", code]
        return ["bash", code]
    if lowered.endswith(".ps1") and os.path.isfile(code):
        return ["powershell", "-File", code]
    return [sys.executable, "-c", code]


def run_verification_code(code: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a todo item's verification code synchronously.

    Resolution mirrors the Kimi TodoList:
    - `!`-prefixed code runs as a shell command (shell=True is the portable
      path on Windows; the model already has a terminal tool, so this adds
      no privilege);
    - an existing .py / .sh / .ps1 file path runs under the matching
      interpreter;
    - anything else runs as inline Python under sys.executable.

    Returns (success, output) with stdout+stderr merged. Empty code is a
    no-op success. Never raises.
    """
    if not code:
        return True, ""
    stripped = str(code).strip()
    if not stripped:
        return True, ""
    try:
        if stripped.startswith("!"):
            command = stripped[1:].strip()
            if not command:
                return True, ""
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        else:
            lowered = stripped.lower()
            is_py_file = lowered.endswith(".py") and os.path.isfile(stripped)
            # PyInstaller-frozen runtime (CN portable desktop): sys.executable
            # is the Hermes CLI binary, not a standalone python.  Spawning it
            # with a .py path or ``-c`` would run `hermes <script>.py` and die
            # with argparse's "invalid choice" error — same class of bug as the
            # cron fix.  Run python verification in-process instead.
            from tools.runtime_compat import (
                is_frozen_runtime,
                run_python_script_in_process,
            )

            if is_frozen_runtime() and (
                is_py_file or _verification_argv(stripped)[0] == sys.executable
            ):
                if is_py_file:
                    exit_code, stdout, stderr = run_python_script_in_process(
                        stripped, timeout
                    )
                else:
                    # Inline python: write to a temp file then run in-process
                    # (runpy.run_path needs a real path, not ``-c`` source).
                    import tempfile

                    with tempfile.NamedTemporaryFile(
                        "w", suffix=".py", delete=False, encoding="utf-8"
                    ) as tf:
                        tf.write(stripped)
                        tmp_path = tf.name
                    try:
                        exit_code, stdout, stderr = run_python_script_in_process(
                            tmp_path, timeout
                        )
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                output = stdout or ""
                if stderr:
                    output = (output + "\n" + stderr) if output else stderr
                if exit_code == 0:
                    return True, output
                return False, f"Code failed (exit code {exit_code}):\n{output}"
            proc = subprocess.run(
                _verification_argv(stripped),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        try:
            exc.kill()
        except Exception:
            pass
        return False, f"Code execution timed out after {timeout}s."
    except FileNotFoundError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)

    output = proc.stdout or ""
    if proc.stderr:
        output = (output + "\n" + proc.stderr) if output else proc.stderr
    if proc.returncode == 0:
        return True, output
    return False, f"Code failed (exit code {proc.returncode}):\n{output}"


def todo_tool(todos: Optional[List[Dict[str, Any]]] = None, merge: bool = False,
              auto_fix: bool = True, store: Optional[TodoStore] = None) -> str:
    """Write ``todos`` (replace, or ``merge`` by id) or read when None -> list + summary JSON.

    Returns the full list, ``revision``, status ``summary`` (including the archived
    count), non-blocking ``warnings`` and the all-done reminder ``message`` (or null).
    """
    if store is None:
        return tool_error("TodoStore not initialized")
    if todos is None:
        items = store.read()
        warnings = store.pop_warnings()
    else:
        if isinstance(todos, str):  # LLMs sometimes send a JSON string instead of a list
            try:
                todos = orjson.loads(todos)
            except (orjson.JSONDecodeError, TypeError):
                return tool_error("todos must be a list of objects, got unparseable string")
        if not isinstance(todos, list):
            return tool_error(f"todos must be a list, got {type(todos).__name__}")
        items = store.write(todos, merge, auto_fix=auto_fix)
        conflict_error = store.pop_conflict_error()
        if conflict_error is not None:
            return tool_error(conflict_error)
        warnings = store.pop_warnings()
    summary = {"total": len(items)}
    for status in ("pending", "in_progress", "completed", "cancelled"):
        summary[status] = sum(1 for i in items if i["status"] == status)
    summary["archived"] = len(store._archived)
    # All-done reminder: non-empty active list with every item finished.
    message = (
        _ALL_DONE_REMINDER
        if items and all(item["status"] in {"completed", "cancelled"} for item in items)
        else None
    )
    revision = store.snapshot()["revision"]
    return orjson.dumps({"todos": items, "revision": revision, "summary": summary,
                         "warnings": warnings, "message": message}).decode('utf-8')


def check_todo_requirements() -> bool:
    """Todo tool has no external requirements -- always available."""
    return True


# Behavioral guidance is baked into the (static, cached) description; item shape and merge
# semantics live ONLY in the parameter schema.
TODO_SCHEMA = {
    "name": "todo_list",
    "description": (
        # See #95681.
        "Track a task list for multi-step work (3+ steps). Use for complex tasks "
        "with 3+ steps or when the user provides multiple tasks. "
        "For 'all N items' tasks, enumerate every instance as its own checklist "
        "item so none are silently dropped. "
        "Call with no parameters to read the current list.\n"
        "List order is priority. Only ONE item in_progress at a time. "
        "Break large phases into subtasks via parent. "
        "Mark an item completed only after the work is verified done, never "
        "based on intent. If something fails, cancel it and add a revised "
        "item. Always returns the full current list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string"
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"]
                        },
                        "parent": {
                            "type": "string",
                            "description": "Optional id of another item, making this a nested subtask. Omit for top-level."
                        },
                        "notes": {
                            "type": "string",
                            "description": (
                                "Optional notes/details; updated on merge "
                                "only when provided"
                            )
                        },
                        "code": {
                            "type": "string",
                            "description": (
                                "Optional verification: inline Python, .py path, "
                                "`!`-prefixed shell command, or .sh/.ps1 "
                                "path. Runs on completion; failure reverts "
                                "to pending"
                            )
                        }
                    },
                    "required": ["id", "content", "status"]
                }
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list with a fresh plan."
                ),
                "default": False
            },
            "auto_fix": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Auto-complete extra in_progress items "
                    "extras (true, default) or error (false)"
                )
            }
        },
        "required": []
    }
}


from tools.registry import registry, tool_error

registry.register(
    name="todo_list", toolset="todo", schema=TODO_SCHEMA, check_fn=check_todo_requirements,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False),
        auto_fix=args.get("auto_fix", True), store=kw.get("store")),
    emoji="📋")
