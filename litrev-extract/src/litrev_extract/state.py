"""State management for resumable pipeline execution.

Replaces the simple ``load_state`` / ``save_state`` functions from the
original project with a thread-safe ``StateManager`` class that supports:

- Multi-model partitioning — state is keyed by ``(model_alias, task_id)``
  so separate models can share one state file without collisions.
- Atomic file writes via a temporary-file-then-rename strategy to prevent
  corruption on crash or concurrent access.
- Periodic auto-flush: every ``flush_interval`` mutations the in-memory
  dict is written to disk so progress is not lost if the process dies.
- Lock-guarded access for safe use from multiple worker threads.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional


class StateManager:
    """Manages pipeline execution state for incremental resumability.

    The state is stored as a JSON file on disk with the following structure:

    .. code-block:: json

        {
          "<model_alias>": {
            "<task_id>": {
              "status": "success" | "failed" | "in_progress",
              "retries": 0,
              "timestamp": 1234567890.0,
              "output_path": "...",
              "error": "..."         // only present for failed tasks
            }
          }
        }

    All public methods are thread-safe (protected by ``self._lock``).

    Attributes:
        state_file: Absolute or relative path to the persistent JSON state file.
        flush_interval: Number of dirty mutations after which an automatic
            flush to disk is triggered.
        _state: In-memory dict mirroring the on-disk state.
        _dirty_count: Accumulated mutations since the last flush.
        _lock: A ``threading.Lock`` guarding ``_state`` and ``_dirty_count``.

    Usage:
        sm = StateManager("state.json", flush_interval=50)
        sm.mark_success("opus", "doc1/prompt_a", retries=2)
        if sm.is_completed("opus", "doc1/prompt_a"):
            ...
        sm.flush()
    """

    def __init__(self, state_file: str, flush_interval: int = 50) -> None:
        """Initialise the state manager and load any existing state from disk.

        Args:
            state_file: Path to the JSON state file. Will be created if it
                does not exist.
            flush_interval: Number of mutations (success/failure marks) after
                which the in-memory state is automatically flushed to disk.
                A lower value reduces potential data loss at the cost of more
                disk I/O.
        """
        self.state_file: str = state_file
        self.flush_interval: int = flush_interval
        self._state: Dict[str, Dict[str, Any]] = {}
        self._dirty_count: int = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """Load state from disk into memory.

        If the state file does not exist, an empty dict is used silently.
        If the file exists but is corrupt (invalid JSON, read error), the
        corrupt state is discarded and processing starts fresh. This errs on
        the side of allowing the pipeline to continue rather than crashing,
        at the cost of re-processing already-completed tasks.
        """
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable — back up the corrupted file and
                # start fresh so the pipeline can continue rather than crash.
                import shutil
                backup = self.state_file + ".corrupt." + str(int(time.time()))
                try:
                    shutil.copy2(self.state_file, backup)
                    print(f"[WARN] Corrupt state file backed up to {backup}")
                except OSError:
                    pass
                print(f"[WARN] State file '{self.state_file}' is corrupt. "
                      "Starting fresh. All previously completed tasks will be re-processed.")
                self._state = {}
        else:
            self._state = {}

    def flush(self) -> None:
        """Atomically write the in-memory state to disk.

        The write is performed by first writing to a ``.tmp`` file alongside
        the target path, then atomically renaming (``os.replace``). This
        prevents partial writes from corrupting the state file if the
        process is killed mid-write.

        Thread-safety note: callers should hold ``self._lock`` if they
        are also modifying ``self._state``, but ``flush()`` itself does
        *not* acquire the lock — it is intended to be called from within
        locked methods.
        """
        tmp_file = self.state_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, self.state_file)

    def is_completed(self, model_alias: str, task_id: str) -> bool:
        """Check if a task was already successfully completed in a prior run.

        Args:
            model_alias: The model alias to look up.
            task_id: The unique task identifier.

        Returns:
            ``True`` if the task exists and its status is ``"success"``.
        """
        with self._lock:
            model_state = self._state.get(model_alias, {})
            entry = model_state.get(task_id, {})
            return entry.get("status") == "success"

    def mark_success(
        self,
        model_alias: str,
        task_id: str,
        retries: int,
        metadata: Optional[dict] = None,
    ) -> None:
        """Record a successful task completion and auto-flush if threshold met.

        The task entry records the number of retries it took, the timestamp,
        and any additional metadata (e.g. output file path).

        Args:
            model_alias: The model alias under which to record.
            task_id: The unique task identifier.
            retries: The number of retry attempts before success (0 = first try).
            metadata: Optional extra fields to store in the task entry.
        """
        with self._lock:
            self._state.setdefault(model_alias, {})[task_id] = {
                "status": "success",
                "retries": retries,
                "timestamp": time.time(),
                **(metadata or {}),
            }
            self._dirty_count += 1
            if self._dirty_count >= self.flush_interval:
                self.flush()
                self._dirty_count = 0

    def mark_failed(
        self,
        model_alias: str,
        task_id: str,
        retries: int,
        error: Optional[str] = None,
    ) -> None:
        """Record a permanent (non-retriable) task failure.

        Args:
            model_alias: The model alias under which to record.
            task_id: The unique task identifier.
            retries: The number of retry attempts made before giving up.
            error: Optional error message (truncated to 500 characters).
        """
        with self._lock:
            self._state.setdefault(model_alias, {})[task_id] = {
                "status": "failed",
                "retries": retries,
                "timestamp": time.time(),
            }
            if error:
                self._state[model_alias][task_id]["error"] = error[:500]
            self._dirty_count += 1
            if self._dirty_count >= self.flush_interval:
                self.flush()
                self._dirty_count = 0

    def get_task_status(self, model_alias: str, task_id: str) -> Optional[str]:
        """Return the status of a specific task, or ``None`` if not present.

        Args:
            model_alias: The model alias to look up.
            task_id: The unique task identifier.

        Returns:
            ``"success"``, ``"failed"``, or ``None`` if the task has no entry.
        """
        with self._lock:
            model_state = self._state.get(model_alias, {})
            entry = model_state.get(task_id)
            return entry.get("status") if entry else None

    def get_failed_ids(self, model_alias: str) -> list:
        """Return all task IDs with status ``'failed'`` for a model.

        Args:
            model_alias: The model alias to inspect.

        Returns:
            A list of task ID strings whose status is ``'failed'``.
        """
        with self._lock:
            model_state = self._state.get(model_alias, {})
            return [
                tid for tid, entry in model_state.items()
                if entry.get("status") == "failed"
            ]

    def reset_failed(self, model_alias: str, task_ids: Optional[list[str]] = None) -> int:
        """Remove failed-task entries from state so they can be retried.

        When *task_ids* is ``None`` (default), ALL failed entries for the
        model are cleared.  When a specific list is given, only those IDs
        are removed.

        Args:
            model_alias: The model alias to clean.
            task_ids: Optional subset of task IDs to reset.  ``None`` means
                      reset all failed tasks.

        Returns:
            The number of entries removed from state.
        """
        with self._lock:
            model_state = self._state.get(model_alias, {})
            if task_ids is None:
                task_ids = [
                    tid for tid, entry in model_state.items()
                    if entry.get("status") == "failed"
                ]
            removed = 0
            for tid in task_ids:
                if tid in model_state and model_state[tid].get("status") == "failed":
                    del model_state[tid]
                    removed += 1
            if removed:
                self.flush()
                self._dirty_count = 0
            return removed

    def reset_model(self, model_alias: str) -> None:
        """Reset all state for a given model alias.

        Removes the alias's key from the state dict entirely and immediately
        flushes to disk. Useful for re-processing a model from scratch.

        Args:
            model_alias: The model alias to clear.
        """
        with self._lock:
            self._state.pop(model_alias, None)
            self.flush()
            self._dirty_count = 0

    def get_summary(self, model_alias: str) -> Dict[str, int]:
        """Get summary counts for a model's tasks by status.

        Args:
            model_alias: The model alias to summarise.

        Returns:
            A dict with keys ``"success"``, ``"failed"``, and
            ``"in_progress"`` mapped to their respective counts.
        """
        with self._lock:
            model_state = self._state.get(model_alias, {})
            summary: Dict[str, int] = {"success": 0, "failed": 0, "in_progress": 0}
            for entry in model_state.values():
                status = entry.get("status", "unknown")
                if status in summary:
                    summary[status] += 1
            return summary

    @property
    def all_model_aliases(self) -> list:
        """Return all model aliases present in the state.

        Returns:
            A list of alias strings (e.g. ``["opus", "sonnet"]``).
            Returns an empty list if no state has been recorded yet.
        """
        with self._lock:
            return list(self._state.keys())