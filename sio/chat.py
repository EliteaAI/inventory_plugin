#!/usr/bin/python3
# coding=utf-8

"""
Inventory Chat Socket.IO Event Handler

Handles real-time streaming chat via socket.io.
"""

import json
import threading
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any

from pylon.core.tools import log, web


# Maximum chat session duration in seconds (30 minutes)
MAX_CHAT_SESSION_DURATION = 30 * 60

# Cleanup interval in seconds (check every 2 minutes)
CLEANUP_INTERVAL = 2 * 60


class ChatCancelledException(Exception):
    """Raised when a chat session is cancelled by the user."""
    pass


class ChatTimeoutException(Exception):
    """Raised when a chat session exceeds the maximum duration."""
    pass


class SIO:
    """Socket.IO event handlers for inventory chat."""

    def _get_chat_sessions(self):
        """Get or create the chat sessions registry."""
        if not hasattr(self, '_chat_sessions'):
            self._chat_sessions = {}
            self._chat_sessions_lock = threading.Lock()
            # Start cleanup thread
            self._start_cleanup_thread()
        return self._chat_sessions

    def _start_cleanup_thread(self):
        """Start background thread for cleaning up stale sessions."""
        if hasattr(self, '_cleanup_thread_started') and self._cleanup_thread_started:
            return

        self._cleanup_thread_started = True
        self._cleanup_stop_event = threading.Event()

        def cleanup_loop():
            log.info("[chat_cleanup] Cleanup thread started")
            while not self._cleanup_stop_event.is_set():
                try:
                    self._cleanup_stale_sessions()
                except Exception as e:
                    log.exception(f"[chat_cleanup] Error in cleanup: {e}")

                # Wait for next cleanup interval or stop event
                self._cleanup_stop_event.wait(timeout=CLEANUP_INTERVAL)

            log.info("[chat_cleanup] Cleanup thread stopped")

        thread = threading.Thread(target=cleanup_loop, daemon=True, name="chat_session_cleanup")
        thread.start()

    def _cleanup_stale_sessions(self):
        """Clean up sessions that have exceeded the maximum duration."""
        sessions = self._get_chat_sessions()
        now = time.time()
        stale_sessions = []

        with self._chat_sessions_lock:
            for session_id, session_data in list(sessions.items()):
                started_at = session_data.get("started_at")
                if started_at:
                    duration = now - started_at
                    if duration > MAX_CHAT_SESSION_DURATION:
                        stale_sessions.append(session_id)
                        # Mark as cancelled so the running thread will stop
                        session_data["cancelled"].set()
                        session_data["timed_out"] = True

        if stale_sessions:
            log.warning(f"[chat_cleanup] Marking {len(stale_sessions)} stale sessions for cleanup: {stale_sessions}")

            # Emit timeout events and remove from registry
            for session_id in stale_sessions:
                with self._chat_sessions_lock:
                    session_data = sessions.get(session_id)
                    if session_data:
                        sid = session_data.get("sid")
                        if sid:
                            try:
                                self.context.sio.emit(
                                    "inventory_chat_timeout",
                                    {
                                        "message": "Chat session timed out after 30 minutes",
                                        "session_id": session_id,
                                    },
                                    room=sid,
                                )
                            except Exception as e:
                                log.warning(f"[chat_cleanup] Failed to emit timeout for {session_id}: {e}")
                        # Remove from registry
                        sessions.pop(session_id, None)

    def _register_chat_session(self, session_id: str, sid: str):
        """Register a new chat session."""
        sessions = self._get_chat_sessions()
        with self._chat_sessions_lock:
            sessions[session_id] = {
                "sid": sid,
                "cancelled": threading.Event(),
                "started_at": time.time(),
                "timed_out": False,
            }
        return sessions[session_id]

    def _unregister_chat_session(self, session_id: str):
        """Unregister a chat session."""
        sessions = self._get_chat_sessions()
        with self._chat_sessions_lock:
            sessions.pop(session_id, None)

    def _cancel_chat_session(self, session_id: str = None, sid: str = None):
        """
        Cancel a chat session by session_id or all sessions for a socket sid.

        Returns list of cancelled session IDs.
        """
        sessions = self._get_chat_sessions()
        cancelled = []

        with self._chat_sessions_lock:
            if session_id and session_id in sessions:
                sessions[session_id]["cancelled"].set()
                cancelled.append(session_id)
            elif sid:
                # Cancel all sessions for this socket
                for sess_id, sess_data in sessions.items():
                    if sess_data.get("sid") == sid:
                        sess_data["cancelled"].set()
                        cancelled.append(sess_id)

        return cancelled

    def _is_session_cancelled(self, session_id: str) -> bool:
        """Check if a session has been cancelled."""
        sessions = self._get_chat_sessions()
        with self._chat_sessions_lock:
            session = sessions.get(session_id)
            if session:
                return session["cancelled"].is_set()
        return False

    def _is_session_timed_out(self, session_id: str) -> bool:
        """Check if a session has timed out."""
        sessions = self._get_chat_sessions()
        with self._chat_sessions_lock:
            session = sessions.get(session_id)
            if session:
                # Check explicit timeout flag
                if session.get("timed_out"):
                    return True
                # Check duration
                started_at = session.get("started_at")
                if started_at and (time.time() - started_at) > MAX_CHAT_SESSION_DURATION:
                    session["timed_out"] = True
                    session["cancelled"].set()
                    return True
        return False

    def _check_session_valid(self, session_id: str) -> str:
        """
        Check if session is still valid.

        Returns:
            "ok" if valid
            "cancelled" if cancelled by user
            "timeout" if timed out
        """
        if self._is_session_timed_out(session_id):
            return "timeout"
        if self._is_session_cancelled(session_id):
            return "cancelled"
        return "ok"

    @web.sio("inventory_chat")
    def handle_inventory_chat(self, sid: str, data: Dict[str, Any]):
        """
        Handle inventory chat requests via socket.io.

        This enables real-time streaming of intermediate steps to the client.

        Event data:
        {
            "project_id": int,
            "toolkit_id": int,
            "prompt": str,
            "filters": {...},  # optional
            "conversation_id": str,  # optional
            "history": [{"role": "user|assistant", "content": "..."}]  # optional
            "session_id": str  # optional - for tracking/cancellation
        }

        Emits events:
        - inventory_chat_start: Chat started (includes session_id for cancellation)
        - inventory_chat_tool_start: Tool execution started
        - inventory_chat_tool_end: Tool execution completed
        - inventory_chat_llm_start: LLM call started
        - inventory_chat_llm_token: LLM token received (streaming)
        - inventory_chat_llm_end: LLM call completed
        - inventory_chat_complete: Final result
        - inventory_chat_cancelled: Chat was cancelled
        - inventory_chat_error: Error occurred
        """
        log.info(f"[inventory_chat sio] Received chat request from {sid}")

        # Validate required fields
        project_id = data.get("project_id")
        toolkit_id = data.get("toolkit_id")
        prompt = data.get("prompt", "").strip()

        if not all([project_id, toolkit_id, prompt]):
            self.context.sio.emit(
                "inventory_chat_error",
                {
                    "error": "project_id, toolkit_id, and prompt are required",
                    "project_id": project_id,
                    "toolkit_id": toolkit_id,
                },
                room=sid,
            )
            return

        filters = data.get("filters", {})
        conversation_id = data.get("conversation_id")
        history = data.get("history", [])
        model = data.get("model")

        # Create or use provided session_id for tracking
        session_id = data.get("session_id") or str(uuid.uuid4())

        # Register this chat session for cancellation tracking
        session = self._register_chat_session(session_id, sid)

        # Create emit function that sends events to this client
        def emit_fn(event_type: str, event_data: Dict[str, Any]):
            """Emit events to the client via socket.io."""
            full_event = f"inventory_chat_{event_type}"
            self.context.sio.emit(
                full_event,
                {
                    **event_data,
                    "project_id": project_id,
                    "toolkit_id": toolkit_id,
                    "session_id": session_id,
                },
                room=sid,
            )

        # Create cancellation/timeout check callback
        def is_cancelled() -> bool:
            # Check both cancellation and timeout
            status = self._check_session_valid(session_id)
            return status != "ok"

        # Emit start event with session_id so client can cancel
        emit_fn("start", {"message": "Starting chat...", "session_id": session_id, "max_duration_seconds": MAX_CHAT_SESSION_DURATION})

        # Run chat in background thread to not block socket.io
        def run_chat():
            try:
                result = self.inventory_chat(
                    project_id=int(project_id),
                    toolkit_id=int(toolkit_id),
                    prompt=prompt,
                    filters=filters,
                    conversation_id=conversation_id,
                    history=history,
                    emit_fn=emit_fn,
                    model=model,
                    is_cancelled=is_cancelled,  # Pass cancellation/timeout check
                )

                # Check session status before emitting result
                session_status = self._check_session_valid(session_id)

                if session_status == "timeout":
                    log.warning(f"[inventory_chat sio] Chat timed out: session_id={session_id}")
                    self.context.sio.emit(
                        "inventory_chat_timeout",
                        {
                            "message": "Chat session timed out after 30 minutes",
                            "project_id": project_id,
                            "toolkit_id": toolkit_id,
                            "session_id": session_id,
                        },
                        room=sid,
                    )
                elif session_status == "cancelled" or result.get("cancelled"):
                    log.info(f"[inventory_chat sio] Chat cancelled: session_id={session_id}")
                    self.context.sio.emit(
                        "inventory_chat_cancelled",
                        {
                            "message": "Chat cancelled by user",
                            "project_id": project_id,
                            "toolkit_id": toolkit_id,
                            "session_id": session_id,
                        },
                        room=sid,
                    )
                else:
                    # Emit final result
                    self.context.sio.emit(
                        "inventory_chat_complete",
                        {
                            **result,
                            "project_id": project_id,
                            "toolkit_id": toolkit_id,
                            "session_id": session_id,
                        },
                        room=sid,
                    )

            except ChatCancelledException:
                # Check if it was actually a timeout
                if self._is_session_timed_out(session_id):
                    log.warning(f"[inventory_chat sio] Chat timed out: session_id={session_id}")
                    self.context.sio.emit(
                        "inventory_chat_timeout",
                        {
                            "message": "Chat session timed out after 30 minutes",
                            "project_id": project_id,
                            "toolkit_id": toolkit_id,
                            "session_id": session_id,
                        },
                        room=sid,
                    )
                else:
                    log.info(f"[inventory_chat sio] Chat cancelled: session_id={session_id}")
                    self.context.sio.emit(
                        "inventory_chat_cancelled",
                        {
                            "message": "Chat cancelled by user",
                            "project_id": project_id,
                            "toolkit_id": toolkit_id,
                            "session_id": session_id,
                        },
                        room=sid,
                    )

            except Exception as e:
                log.exception(f"[inventory_chat sio] Error: {e}")
                self.context.sio.emit(
                    "inventory_chat_error",
                    {
                        "error": str(e),
                        "project_id": project_id,
                        "toolkit_id": toolkit_id,
                        "session_id": session_id,
                    },
                    room=sid,
                )

            finally:
                # Unregister session when done
                self._unregister_chat_session(session_id)

        # Start background thread
        thread = threading.Thread(target=run_chat, daemon=True)
        thread.start()

    @web.sio("inventory_chat_cancel")
    def handle_inventory_chat_cancel(self, sid: str, data: Dict[str, Any]):
        """
        Handle chat cancellation request.

        Event data:
        {
            "session_id": str  # optional - specific session to cancel
                               # if not provided, cancels all sessions for this socket
        }
        """
        session_id = data.get("session_id")
        log.info(f"[inventory_chat_cancel sio] Cancel request from {sid}, session_id={session_id}")

        # Cancel the session(s)
        cancelled = self._cancel_chat_session(session_id=session_id, sid=sid)

        if cancelled:
            log.info(f"[inventory_chat_cancel sio] Cancelled sessions: {cancelled}")
            self.context.sio.emit(
                "inventory_chat_cancelled",
                {
                    "message": "Cancel request processed",
                    "cancelled_sessions": cancelled,
                },
                room=sid,
            )
        else:
            self.context.sio.emit(
                "inventory_chat_cancelled",
                {
                    "message": "No active sessions to cancel",
                    "cancelled_sessions": [],
                },
                room=sid,
            )

    @web.sio("disconnect")
    def handle_disconnect(self, sid: str):
        """Clean up any active chat sessions when client disconnects."""
        cancelled = self._cancel_chat_session(sid=sid)
        if cancelled:
            log.info(f"[inventory_chat sio] Client {sid} disconnected, cancelled sessions: {cancelled}")
