#!/usr/bin/env python3
"""
Todo Tool Module - Planning & Task Management

Provides an in-memory task list the agent uses to decompose complex tasks,
track progress, and maintain focus across long conversations. The state
lives on the AIAgent instance (one per session) and is re-injected into
the conversation after context compression events.

Design:
- Single `todo` tool: provide `todos` param to write, omit to read
- Every call returns the full current list
- No system prompt mutation, no tool response modification
- Behavioral guidance lives entirely in the tool schema description
"""

import os
import shutil
import subprocess
import sys

import orjson
import rapidfuzz
from typing import Any, Dict, List, Optional


# Valid status values for todo items
VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}

# Bounds on persisted todo state. The todo list is a planning aid the model
# re-reads after every context-compression event (see format_for_injection),
# so unbounded item content or count defeats the compression it rides through.
# These caps keep a single oversized item (whether authored by the model or
# replayed from caller-supplied history on the API server) from inflating the
# re-injection block. Generous relative to real plans — a todo item is a short
# task description, and active lists are a handful of items, not hundreds.
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
# Upper bound on a single todo tool-result payload accepted during history
# hydration. The gateway/API server replays caller-supplied conversation
# history to rebuild the store, so an oversized forged result is dropped
# before it is parsed and re-injected (see AIAgent._hydrate_todo_store).
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
# Persisted as ordinary message content. ContextCompressor uses this stable
# header to distinguish the synthetic post-compaction row from a real user.
TODO_INJECTION_HEADER = (
    "[Your active task list was preserved across context compression]"
)

# Reminder surfaced as the result "message" once the active list is non-empty
# and every item is finished — exact Kimi TodoList wording.
_ALL_DONE_REMINDER = (
    "All todos are done. "
    "Please review the requirements again to ensure nothing is left unfinished."
)


class TodoStore:
    """
    In-memory todo list. One instance per AIAgent (one per session).

    Items are ordered -- list position is priority. Each item has:
      - id: unique string identifier (agent-chosen)
      - content: task description
      - status: pending | in_progress | completed | cancelled
      - notes: optional free-form detail (kept across merges when omitted)
      - code: optional verification code run when the item is marked completed

    Finished items dropped by a replace-mode write move to _archived
    (bounded by MAX_ARCHIVED_TODOS). Warnings produced by the write pipeline
    accumulate on _warnings and are surfaced via pop_warnings().
    """

    def __init__(self):
        self._items: List[Dict[str, str]] = []
        self._archived: List[Dict[str, str]] = []
        self._warnings: List[str] = []
        self._conflict_error: Optional[str] = None

    def write(
        self,
        todos: List[Dict[str, Any]],
        merge: bool = False,
        auto_fix: bool = True,
    ) -> List[Dict[str, str]]:
        """
        Write todos. Returns the full current list after writing.

        Args:
            todos: list of {id, content, status} dicts
            merge: if False, replace the entire list. If True, update
                   existing items by id and append new ones.
            auto_fix: if True (default), when more than one item is
                      in_progress keep the first and mark the extras
                      completed. If False, the conflict is left in place
                      and recorded so todo_tool() can surface a hard error.

        Warnings accumulate on self._warnings (pop_warnings() retrieves them);
        the auto_fix=False conflict is retrievable via pop_conflict_error().
        """
        self._warnings = []
        self._conflict_error = None
        incoming = self._dedupe_by_id(todos)
        old_items = list(self._items)
        old_statuses = {item["id"]: item["status"] for item in old_items}

        # Non-blocking content guards (warnings only).
        self._warnings.extend(self._detect_fuzzy_warnings(incoming, old_items))
        self._warnings.extend(self._detect_duplicate_contents(incoming))

        if not merge:
            # Replace mode: new list entirely
            self._items = [self._validate(t) for t in incoming]
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
        else:
            # Merge mode: update existing items by id, append new ones
            existing = {item["id"]: item for item in self._items}
            for t in incoming:
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue  # Can't merge without an id

                if item_id in existing:
                    # Update only the fields the LLM actually provided
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = self._cap_content(str(t["content"]).strip())
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                    # notes/code update only when the incoming value is
                    # truthy; None or empty keeps the stored value.
                    if t.get("notes"):
                        existing[item_id]["notes"] = self._cap_field(
                            str(t["notes"]).strip(), MAX_TODO_NOTES_CHARS
                        )
                    if t.get("code"):
                        existing[item_id]["code"] = self._cap_field(
                            str(t["code"]).strip(), MAX_TODO_CODE_CHARS
                        )
                else:
                    # New item -- validate fully and append to end
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            # Rebuild _items preserving order for existing items
            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt

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

        # Bound total item count so a replayed/oversized list can't grow the
        # re-injection block without limit. Keep the highest-priority head
        # (list order is priority).
        if len(self._items) > MAX_TODO_ITEMS:
            self._items = self._items[:MAX_TODO_ITEMS]
        return [item.copy() for item in self._items]

    def read(self) -> List[Dict[str, str]]:
        """Return a copy of the current list."""
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
        """Check if there are any items in the list."""
        return bool(self._items)

    def format_for_injection(self) -> Optional[str]:
        """
        Render the todo list for post-compression injection.

        Returns a human-readable string to append to the compressed
        message history, or None if the list is empty.
        """
        if not self._items:
            return None

        # Status markers for compact display
        markers = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
            "cancelled": "[~]",
        }

        # Only inject pending/in_progress items — completed/cancelled ones
        # cause the model to re-do finished work after compression.
        active_items = [
            item for item in self._items
            if item["status"] in {"pending", "in_progress"}
        ]
        if not active_items:
            return None

        lines = [TODO_INJECTION_HEADER]
        for item in active_items:
            marker = markers.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}. {item['content']} ({item['status']})")

        return "\n".join(lines)

    @staticmethod
    def _cap_content(content: str) -> str:
        """Truncate oversized todo content to MAX_TODO_CONTENT_CHARS.

        A single huge item would otherwise inflate the post-compression
        re-injection block (format_for_injection) without bound. Keep the
        head — the actionable part of a task description — plus a marker.
        """
        if len(content) > MAX_TODO_CONTENT_CHARS:
            keep = MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)
            return content[:keep] + _TRUNCATION_MARKER
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
        """
        Validate and normalize a todo item.

        Ensures required fields exist and status is valid.
        Returns a clean dict with {id, content, status} and optionally
        notes/code when provided.
        """
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}

        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"

        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"
        else:
            content = TodoStore._cap_content(content)

        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"

        result: Dict[str, str] = {"id": item_id, "content": content, "status": status}
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
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                # Non-dict items get a synthetic key so _validate can handle them
                last_index[f"__invalid_{i}"] = i
                continue
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
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


def todo_tool(
    todos: Optional[List[Dict[str, Any]]] = None,
    merge: bool = False,
    auto_fix: bool = True,
    store: Optional[TodoStore] = None,
) -> str:
    """
    Single entry point for the todo tool. Reads or writes depending on params.

    Args:
        todos: if provided, write these items. If None, read current list.
        merge: if True, update by id. If False (default), replace entire list.
        auto_fix: if True (default), auto-complete extra in_progress items;
                  if False, a multi-in_progress write returns a hard error.
        store: the TodoStore instance from the AIAgent.

    Returns:
        JSON string with the full current list, summary metadata (including
        the archived count), non-blocking warnings, and the all-done
        reminder message (or null).
    """
    if store is None:
        return tool_error("TodoStore not initialized")

    if todos is not None:
        # Guard: LLM sometimes sends todos as a JSON string instead of a list
        if isinstance(todos, str):
            try:
                todos = orjson.loads(todos)
            except (orjson.JSONDecodeError, TypeError):
                return tool_error("todos must be a list of objects, got unparseable string")
        if not isinstance(todos, list):
            return tool_error(
                f"todos must be a list, got {type(todos).__name__}"
            )
        items = store.write(todos, merge, auto_fix=auto_fix)
        conflict_error = store.pop_conflict_error()
        if conflict_error is not None:
            return tool_error(conflict_error)
        warnings = store.pop_warnings()
    else:
        items = store.read()
        warnings = store.pop_warnings()

    # Build summary counts
    pending = sum(1 for i in items if i["status"] == "pending")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    completed = sum(1 for i in items if i["status"] == "completed")
    cancelled = sum(1 for i in items if i["status"] == "cancelled")

    # All-done reminder: non-empty active list with every item finished.
    message = (
        _ALL_DONE_REMINDER
        if items and all(
            item["status"] in {"completed", "cancelled"} for item in items
        )
        else None
    )

    return orjson.dumps({
        "todos": items,
        "summary": {
            "total": len(items),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "cancelled": cancelled,
            "archived": len(store._archived),
        },
        "warnings": warnings,
        "message": message,
    }).decode('utf-8')


def check_todo_requirements() -> bool:
    """Todo tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================
# Behavioral guidance is baked into the description so it's part of the
# static tool schema (cached, never changes mid-conversation).

TODO_SCHEMA = {
    "name": "todo",
    "description": (
        "Manage the session's task list. Use for complex tasks (3+ steps) or "
        "multiple user tasks; no args = read.\n\n"
        "Writing: 'todos' items ({id, content, status: "
        "pending|in_progress|completed|cancelled, notes?, code?}). "
        "merge=false (default) replaces the list; merge=true updates by id, adds "
        "new. Order = priority; ONE in_progress at a time; complete items when "
        "done; cancel failed ones and add revised.\n\n"
        "Behavior:\n"
        "- auto_fix=true (default) auto-completes extra in_progress; false "
        "rejects the write\n"
        "- Done items cannot be re-opened (clamped)\n"
        "- Replace-dropped done items are archived\n"
        "- `code` items verified on completion; failure reverts to pending "
        "with error in notes\n"
        "- All-done returns a reminder in 'message'\n\n"
        "Always returns the full list + 'warnings' and 'message'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write. Omit to read current list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique item identifier"
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                            "description": "Current status"
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
                    "true: update items by id, add new; "
                    "false (default): replace the entire list."
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


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="todo",
    toolset="todo",
    schema=TODO_SCHEMA,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False),
        auto_fix=args.get("auto_fix", True), store=kw.get("store")),
    check_fn=check_todo_requirements,
    emoji="📋",
)
