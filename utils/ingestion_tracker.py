#!/usr/bin/python3
# coding=utf-8

"""
Ingestion Tracker

Manages parallel ingestion limits by tracking active ingestions in a JSON file.
Uses file-based locking for thread/process safety.
"""

import json
import os
import time
import threading
import fcntl
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

try:
    from pylon.core.tools import log
except ModuleNotFoundError:
    import logging

    log = logging.getLogger(__name__)


class IngestionSlotError(Exception):
    """Raised when no ingestion slots are available"""
    pass


class IngestionTracker:
    """
    Tracks active ingestions and enforces parallel limits.

    Stores state in a JSON file at {base_path}/ingestions.json:
    {
        "ingestions": {
            "in_progress": [
                {
                    "task_id": "xxx",
                    "project_id": 7,
                    "toolkit_id": 2,
                    "application_id": 5,
                    "started_at": "2025-12-22T10:30:00Z"
                },
                ...
            ]
        }
    }
    """

    STATE_FILE = "ingestions.json"

    def __init__(
        self,
        base_path: str = "/data/graphs",
        max_parallel: int = 2,
    ):
        self.base_path = Path(base_path)
        self.max_parallel = max_parallel
        self._lock = threading.RLock()

        # Ensure base path exists
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Initialize state file if needed
        self._init_state_file()

        log.info(f"IngestionTracker initialized: max_parallel={max_parallel}, path={self.state_file_path}")

    @property
    def state_file_path(self) -> Path:
        return self.base_path / self.STATE_FILE

    def _init_state_file(self):
        """Initialize state file if it doesn't exist"""
        if not self.state_file_path.exists():
            self._write_state({"ingestions": {"in_progress": []}})

    def _read_state(self) -> Dict[str, Any]:
        """Read current state from file with locking"""
        try:
            with open(self.state_file_path, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"ingestions": {"in_progress": []}}

    def _write_state(self, state: Dict[str, Any]):
        """Write state to file with locking"""
        with open(self.state_file_path, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _atomic_update(self, update_func) -> Any:
        """
        Perform atomic read-modify-write operation on state file.

        Args:
            update_func: Function that takes state dict and returns (new_state, result)

        Returns:
            Result from update_func
        """
        with self._lock:
            # Open file for read+write
            mode = 'r+' if self.state_file_path.exists() else 'w+'
            with open(self.state_file_path, mode) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Read current state
                    f.seek(0)
                    content = f.read()
                    if content:
                        state = json.loads(content)
                    else:
                        state = {"ingestions": {"in_progress": []}}

                    # Apply update
                    new_state, result = update_func(state)

                    # Write new state
                    f.seek(0)
                    f.truncate()
                    json.dump(new_state, f, indent=2)

                    return result
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def get_active_ingestions(self) -> List[Dict[str, Any]]:
        """Get list of currently active ingestions"""
        state = self._read_state()
        return state.get("ingestions", {}).get("in_progress", [])

    def get_available_slots(self) -> int:
        """Get number of available ingestion slots"""
        active = len(self.get_active_ingestions())
        return max(0, self.max_parallel - active)

    def can_start_ingestion(self) -> bool:
        """Check if a new ingestion can be started"""
        return self.get_available_slots() > 0

    def acquire_slot(
        self,
        task_id: str,
        project_id: int,
        toolkit_id: int,
        application_id: int,
        enforce_limit: bool = True,
    ) -> bool:
        """
        Attempt to acquire an ingestion slot.

        Args:
            task_id: Unique task identifier
            project_id: Project ID
            toolkit_id: Source toolkit ID
            application_id: Application/inventory toolkit ID
            enforce_limit: Whether to enforce the local max_parallel limit

        Returns:
            True if slot acquired, raises IngestionSlotError if no slots available
        """
        def do_acquire(state):
            in_progress = state.get("ingestions", {}).get("in_progress", [])

            # Check if we have capacity
            if enforce_limit and len(in_progress) >= self.max_parallel:
                # Return current state and False to indicate failure
                return state, False

            # Check if this task is already tracked (idempotency)
            for ing in in_progress:
                if ing.get("task_id") == task_id:
                    return state, True  # Already tracked

            # Add new ingestion
            in_progress.append({
                "task_id": task_id,
                "project_id": project_id,
                "toolkit_id": toolkit_id,
                "application_id": application_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            })

            state["ingestions"] = {"in_progress": in_progress}
            return state, True

        result = self._atomic_update(do_acquire)

        if not result:
            active = self.get_active_ingestions()
            raise IngestionSlotError(
                f"All ingestion workers are currently busy ({self.max_parallel} active). "
                f"Please wait 10-15 minutes and try again.\n\n"
                f"Currently running ingestions:\n" +
                "\n".join([
                    f"  - Project {ing['project_id']}, Toolkit {ing['toolkit_id']} "
                    f"(started {ing.get('started_at', 'unknown')})"
                    for ing in active
                ])
            )

        log.info(f"Acquired ingestion slot: task_id={task_id}, project={project_id}, toolkit={toolkit_id}")
        return True

    def release_slot(self, task_id: str) -> bool:
        """
        Release an ingestion slot.

        Args:
            task_id: Task identifier to release

        Returns:
            True if slot was released, False if task wasn't tracked
        """
        def do_release(state):
            in_progress = state.get("ingestions", {}).get("in_progress", [])

            # Find and remove the task
            original_count = len(in_progress)
            in_progress = [ing for ing in in_progress if ing.get("task_id") != task_id]

            state["ingestions"] = {"in_progress": in_progress}
            return state, len(in_progress) < original_count

        result = self._atomic_update(do_release)

        if result:
            log.info(f"Released ingestion slot: task_id={task_id}")
        else:
            log.warning(f"Attempted to release unknown ingestion slot: task_id={task_id}")

        return result

    def update_progress(
        self,
        task_id: str,
        progress_message: str,
        progress_phase: Optional[str] = None,
    ) -> bool:
        """Update progress metadata for an active ingestion."""
        def do_update(state):
            in_progress = state.get("ingestions", {}).get("in_progress", [])
            updated = False

            for ing in in_progress:
                if ing.get("task_id") == task_id:
                    ing["progress_message"] = progress_message
                    ing["last_updated"] = datetime.now(timezone.utc).isoformat()
                    if progress_phase:
                        ing["progress_phase"] = progress_phase
                    updated = True
                    break

            state["ingestions"] = {"in_progress": in_progress}
            return state, updated

        result = self._atomic_update(do_update)
        if not result:
            log.debug(f"Attempted to update unknown ingestion progress: task_id={task_id}")
        return result

    def cleanup_stale_ingestions(self, max_age_hours: int = 24) -> int:
        """
        Remove ingestions that have been running too long (likely crashed).

        Args:
            max_age_hours: Maximum age in hours before considering an ingestion stale

        Returns:
            Number of stale ingestions removed
        """
        max_age_seconds = max_age_hours * 3600

        def do_cleanup(state):
            in_progress = state.get("ingestions", {}).get("in_progress", [])
            now = datetime.now(timezone.utc)

            valid = []
            removed = 0

            for ing in in_progress:
                started_str = ing.get("started_at")
                if started_str:
                    try:
                        started = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
                        age_seconds = (now - started).total_seconds()
                        if age_seconds <= max_age_seconds:
                            valid.append(ing)
                        else:
                            removed += 1
                            log.warning(
                                f"Removing stale ingestion: task_id={ing.get('task_id')}, "
                                f"age={age_seconds/3600:.1f}h"
                            )
                    except Exception:
                        valid.append(ing)  # Keep if we can't parse
                else:
                    valid.append(ing)  # Keep if no timestamp

            state["ingestions"] = {"in_progress": valid}
            return state, removed

        return self._atomic_update(do_cleanup)

    def get_status(self) -> Dict[str, Any]:
        """Get current tracker status"""
        active = self.get_active_ingestions()
        return {
            "max_parallel": self.max_parallel,
            "active_count": len(active),
            "available_slots": self.get_available_slots(),
            "active_ingestions": active,
        }
