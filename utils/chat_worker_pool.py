#!/usr/bin/python3
# coding=utf-8

"""
Chat Worker Pool

Manages a pool of worker processes for executing inventory chat requests.
Each worker process has its own event loop, avoiding the "Cannot run the event loop
while another loop is running" error that occurs with threading.

Uses multiprocessing to run chat agents in separate processes where they can
safely create and manage their own event loops.
"""

import json
import os
import time
import threading
import fcntl
import multiprocessing
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timezone
from queue import Empty
import traceback

from pylon.core.tools import log


class ChatWorkerSlotError(Exception):
    """Raised when no chat worker slots are available"""
    pass


class ChatWorkerTracker:
    """
    Tracks active chat workers and enforces parallel limits.

    Stores state in a JSON file at {base_path}/chat_workers.json
    """

    STATE_FILE = "chat_workers.json"

    def __init__(
        self,
        base_path: str = "/data/graphs",
        max_workers: int = 10,
    ):
        self.base_path = Path(base_path)
        self.max_workers = max_workers
        self._lock = threading.RLock()

        # Ensure base path exists
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Initialize state file if needed
        self._init_state_file()

        log.info(f"ChatWorkerTracker initialized: max_workers={max_workers}, path={self.state_file_path}")

    @property
    def state_file_path(self) -> Path:
        return self.base_path / self.STATE_FILE

    def _init_state_file(self):
        """Initialize state file if it doesn't exist"""
        if not self.state_file_path.exists():
            self._write_state({"workers": {"active": []}})

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
            return {"workers": {"active": []}}

    def _write_state(self, state: Dict[str, Any]):
        """Write state to file with locking"""
        with open(self.state_file_path, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _atomic_update(self, update_func) -> Any:
        """Perform atomic read-modify-write operation on state file."""
        with self._lock:
            mode = 'r+' if self.state_file_path.exists() else 'w+'
            with open(self.state_file_path, mode) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    content = f.read()
                    if content:
                        state = json.loads(content)
                    else:
                        state = {"workers": {"active": []}}

                    new_state, result = update_func(state)

                    f.seek(0)
                    f.truncate()
                    json.dump(new_state, f, indent=2)

                    return result
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def get_active_workers(self) -> List[Dict[str, Any]]:
        """Get list of currently active workers"""
        state = self._read_state()
        return state.get("workers", {}).get("active", [])

    def get_available_slots(self) -> int:
        """Get number of available worker slots"""
        active = len(self.get_active_workers())
        return max(0, self.max_workers - active)

    def can_start_chat(self) -> bool:
        """Check if a new chat can be started"""
        return self.get_available_slots() > 0

    def acquire_slot(
        self,
        worker_id: str,
        project_id: int,
        toolkit_id: int,
        user_id: Optional[str] = None,
    ) -> bool:
        """
        Attempt to acquire a chat worker slot.

        Returns:
            True if slot acquired, raises ChatWorkerSlotError if no slots available
        """
        def do_acquire(state):
            active = state.get("workers", {}).get("active", [])

            if len(active) >= self.max_workers:
                return state, False

            # Check if this worker is already tracked
            for w in active:
                if w.get("worker_id") == worker_id:
                    return state, True

            # Add new worker
            active.append({
                "worker_id": worker_id,
                "project_id": project_id,
                "toolkit_id": toolkit_id,
                "user_id": user_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            })

            state["workers"] = {"active": active}
            return state, True

        result = self._atomic_update(do_acquire)

        if not result:
            active = self.get_active_workers()
            raise ChatWorkerSlotError(
                f"All chat workers are currently busy ({self.max_workers} active). "
                f"Please wait a moment and try again.\n\n"
                f"Active chat sessions: {len(active)}"
            )

        log.info(f"Acquired chat worker slot: worker_id={worker_id}")
        return True

    def release_slot(self, worker_id: str) -> bool:
        """Release a chat worker slot."""
        def do_release(state):
            active = state.get("workers", {}).get("active", [])
            original_count = len(active)
            active = [w for w in active if w.get("worker_id") != worker_id]
            state["workers"] = {"active": active}
            return state, len(active) < original_count

        result = self._atomic_update(do_release)

        if result:
            log.info(f"Released chat worker slot: worker_id={worker_id}")
        else:
            log.warning(f"Attempted to release unknown chat worker slot: worker_id={worker_id}")

        return result

    def cleanup_stale_workers(self, max_age_seconds: int = 1800) -> int:
        """
        Remove workers that have been running too long (30 min default).
        """
        def do_cleanup(state):
            active = state.get("workers", {}).get("active", [])
            now = datetime.now(timezone.utc)

            valid = []
            removed = 0

            for w in active:
                started_str = w.get("started_at")
                if started_str:
                    try:
                        started = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
                        age_seconds = (now - started).total_seconds()
                        if age_seconds <= max_age_seconds:
                            valid.append(w)
                        else:
                            removed += 1
                            log.warning(f"Removing stale chat worker: worker_id={w.get('worker_id')}")
                    except Exception:
                        valid.append(w)
                else:
                    valid.append(w)

            state["workers"] = {"active": valid}
            return state, removed

        return self._atomic_update(do_cleanup)

    def get_status(self) -> Dict[str, Any]:
        """Get current tracker status"""
        active = self.get_active_workers()
        return {
            "max_workers": self.max_workers,
            "active_count": len(active),
            "available_slots": self.get_available_slots(),
            "active_workers": active,
        }


def _chat_worker_process(
    request_queue: Queue,
    result_queue: Queue,
    event_queue: Queue,
    worker_id: str,
):
    """
    Worker process function that handles chat requests.

    Each worker process has its own event loop, avoiding conflicts with the main process.

    Args:
        request_queue: Queue to receive chat requests
        result_queue: Queue to send final results
        event_queue: Queue to send streaming events
        worker_id: Unique identifier for this worker
    """
    import sys

    # Add plugin directory to path for imports
    plugin_dir = Path(__file__).parent.parent
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))

    log.info(f"[ChatWorker:{worker_id}] Worker process started")

    while True:
        try:
            # Wait for request
            request = request_queue.get(timeout=60)  # 60s timeout

            if request is None:
                # Shutdown signal
                log.info(f"[ChatWorker:{worker_id}] Received shutdown signal")
                break

            chat_request_id = request.get("request_id")
            log.info(f"[ChatWorker:{worker_id}] Processing request {chat_request_id}")

            try:
                result = _execute_chat_in_worker(
                    request=request,
                    event_queue=event_queue,
                    worker_id=worker_id,
                )

                result_queue.put({
                    "request_id": chat_request_id,
                    "success": True,
                    "result": result,
                })

            except Exception as e:
                log.exception(f"[ChatWorker:{worker_id}] Error processing request: {e}")
                result_queue.put({
                    "request_id": chat_request_id,
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                })

        except Empty:
            # Timeout - continue waiting
            continue
        except Exception as e:
            log.exception(f"[ChatWorker:{worker_id}] Worker error: {e}")
            continue

    log.info(f"[ChatWorker:{worker_id}] Worker process exiting")


def _execute_chat_in_worker(
    request: Dict[str, Any],
    event_queue: Queue,
    worker_id: str,
) -> Dict[str, Any]:
    """
    Execute a chat request in the worker process.

    This function runs in a separate process with its own event loop,
    so async operations can work without conflicts.
    """
    from alita_sdk.runtime.clients.client import AlitaClient

    # Extract request parameters
    project_id = request["project_id"]
    toolkit_id = request["toolkit_id"]
    prompt = request["prompt"]
    filters = request.get("filters", {})
    conversation_id = request.get("conversation_id")
    history = request.get("history", [])
    model = request.get("model")
    user_id = request.get("user_id")

    # Platform config passed from main process
    platform_api_url = request["platform_api_url"]
    platform_token = request["platform_token"]

    # Create AlitaClient in this process
    alita_client = AlitaClient(
        base_url=platform_api_url.rstrip("/"),
        project_id=int(project_id),
        auth_token=platform_token,
    )

    # Create emit function that sends to event queue
    def emit_fn(event_type: str, data: dict):
        """Send events to main process via queue."""
        try:
            event_queue.put_nowait({
                "request_id": request["request_id"],
                "event": event_type,
                "data": data,
            })
        except Exception as e:
            log.warning(f"[ChatWorker:{worker_id}] Failed to emit event: {e}")

    # Import and execute chat logic
    # We need to run the actual chat here - import the inventory_chat logic
    from inventory_chat_worker_impl import execute_inventory_chat

    result = execute_inventory_chat(
        alita_client=alita_client,
        project_id=project_id,
        toolkit_id=toolkit_id,
        prompt=prompt,
        filters=filters,
        conversation_id=conversation_id,
        history=history,
        model=model,
        user_id=user_id,
        emit_fn=emit_fn,
    )

    return result


class ChatWorkerPool:
    """
    Manages a pool of worker processes for handling chat requests.

    Each worker runs in a separate process with its own Python interpreter
    and event loop, allowing multiple concurrent chat sessions without
    event loop conflicts.
    """

    def __init__(
        self,
        num_workers: int = 10,
        base_path: str = "/data/graphs",
    ):
        self.num_workers = num_workers
        self.base_path = base_path
        self.tracker = ChatWorkerTracker(base_path=base_path, max_workers=num_workers)

        self._workers: List[Process] = []
        self._request_queues: List[Queue] = []
        self._result_queue: Queue = None
        self._event_queue: Queue = None
        self._running = False
        self._lock = threading.Lock()
        self._pending_requests: Dict[str, Dict] = {}
        self._result_thread: Optional[threading.Thread] = None
        self._event_thread: Optional[threading.Thread] = None
        self._next_worker = 0  # Round-robin counter

        log.info(f"ChatWorkerPool initialized: num_workers={num_workers}")

    def start(self):
        """Start the worker pool."""
        with self._lock:
            if self._running:
                return

            log.info(f"Starting ChatWorkerPool with {self.num_workers} workers")

            # Use 'spawn' context to create fresh processes with no inherited state
            ctx = multiprocessing.get_context('spawn')

            self._result_queue = ctx.Queue()
            self._event_queue = ctx.Queue()

            # Start worker processes
            for i in range(self.num_workers):
                request_queue = ctx.Queue()
                worker_id = f"chat_worker_{i}"

                process = ctx.Process(
                    target=_chat_worker_process,
                    args=(request_queue, self._result_queue, self._event_queue, worker_id),
                    daemon=True,
                )
                process.start()

                self._workers.append(process)
                self._request_queues.append(request_queue)

                log.info(f"Started worker process {worker_id} (pid={process.pid})")

            # Start result collector thread
            self._result_thread = threading.Thread(
                target=self._result_collector,
                daemon=True,
            )
            self._result_thread.start()

            # Start event forwarder thread
            self._event_thread = threading.Thread(
                target=self._event_forwarder,
                daemon=True,
            )
            self._event_thread.start()

            self._running = True
            log.info("ChatWorkerPool started")

    def stop(self):
        """Stop the worker pool."""
        with self._lock:
            if not self._running:
                return

            log.info("Stopping ChatWorkerPool")

            # Send shutdown signal to all workers
            for queue in self._request_queues:
                try:
                    queue.put(None)
                except Exception:
                    pass

            # Wait for workers to finish
            for worker in self._workers:
                try:
                    worker.join(timeout=5)
                    if worker.is_alive():
                        worker.terminate()
                except Exception:
                    pass

            self._workers.clear()
            self._request_queues.clear()
            self._running = False

            log.info("ChatWorkerPool stopped")

    def _result_collector(self):
        """Thread that collects results from worker processes."""
        while self._running:
            try:
                result = self._result_queue.get(timeout=1)
                request_id = result.get("request_id")

                if request_id in self._pending_requests:
                    pending = self._pending_requests[request_id]
                    pending["result"] = result
                    pending["event"].set()  # Signal completion

            except Empty:
                continue
            except Exception as e:
                log.exception(f"Error in result collector: {e}")

    def _event_forwarder(self):
        """Thread that forwards streaming events from workers."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=1)
                request_id = event.get("request_id")

                if request_id in self._pending_requests:
                    pending = self._pending_requests[request_id]
                    emit_fn = pending.get("emit_fn")
                    if emit_fn:
                        try:
                            emit_fn(event["event"], event["data"])
                        except Exception as e:
                            log.warning(f"Error forwarding event: {e}")

            except Empty:
                continue
            except Exception as e:
                log.exception(f"Error in event forwarder: {e}")

    def submit_chat(
        self,
        project_id: int,
        toolkit_id: int,
        prompt: str,
        filters: Dict[str, Any] = None,
        conversation_id: str = None,
        history: List[Dict] = None,
        model: str = None,
        user_id: str = None,
        emit_fn: Callable = None,
        platform_api_url: str = "",
        platform_token: str = "",
        timeout: float = 300,  # 5 minute default timeout
    ) -> Dict[str, Any]:
        """
        Submit a chat request to the worker pool and wait for result.

        Args:
            project_id: Project ID
            toolkit_id: Toolkit ID
            prompt: User prompt
            filters: Optional filters
            conversation_id: Optional conversation ID
            history: Optional chat history
            model: Optional model override
            user_id: Optional user ID
            emit_fn: Optional streaming callback
            platform_api_url: Platform API URL
            platform_token: Platform auth token
            timeout: Maximum wait time in seconds

        Returns:
            Chat result dict with answer, citations, tool_calls, etc.
        """
        import uuid

        if not self._running:
            raise RuntimeError("ChatWorkerPool is not running")

        request_id = str(uuid.uuid4())

        # Try to acquire a slot
        try:
            self.tracker.acquire_slot(
                worker_id=request_id,
                project_id=project_id,
                toolkit_id=toolkit_id,
                user_id=user_id,
            )
        except ChatWorkerSlotError as e:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "touched_entities": [],
                "error": str(e),
            }

        try:
            # Build request
            request = {
                "request_id": request_id,
                "project_id": project_id,
                "toolkit_id": toolkit_id,
                "prompt": prompt,
                "filters": filters or {},
                "conversation_id": conversation_id,
                "history": history or [],
                "model": model,
                "user_id": user_id,
                "platform_api_url": platform_api_url,
                "platform_token": platform_token,
            }

            # Create completion event
            completion_event = threading.Event()

            # Register pending request
            self._pending_requests[request_id] = {
                "request": request,
                "emit_fn": emit_fn,
                "event": completion_event,
                "result": None,
            }

            # Select worker (round-robin)
            with self._lock:
                worker_idx = self._next_worker % len(self._request_queues)
                self._next_worker += 1

            # Submit to worker
            self._request_queues[worker_idx].put(request)

            # Wait for result
            if completion_event.wait(timeout=timeout):
                pending = self._pending_requests.pop(request_id, {})
                result = pending.get("result", {})

                if result.get("success"):
                    return result.get("result", {
                        "answer": "",
                        "citations": [],
                        "tool_calls": [],
                        "touched_entities": [],
                        "error": None,
                    })
                else:
                    return {
                        "answer": "",
                        "citations": [],
                        "tool_calls": [],
                        "touched_entities": [],
                        "error": result.get("error", "Unknown worker error"),
                    }
            else:
                # Timeout
                self._pending_requests.pop(request_id, None)
                return {
                    "answer": "",
                    "citations": [],
                    "tool_calls": [],
                    "touched_entities": [],
                    "error": f"Chat request timed out after {timeout} seconds",
                }

        finally:
            # Always release the slot
            self.tracker.release_slot(request_id)

    def get_status(self) -> Dict[str, Any]:
        """Get pool status."""
        alive_workers = sum(1 for w in self._workers if w.is_alive())
        return {
            "running": self._running,
            "num_workers": self.num_workers,
            "alive_workers": alive_workers,
            "pending_requests": len(self._pending_requests),
            "tracker_status": self.tracker.get_status(),
        }
