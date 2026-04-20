#!/usr/bin/python3
# coding=utf-8

"""
Inventory Chat Route

HTTP endpoint for inventory chat.
For streaming responses, use the socket.io event instead.
"""

import flask
import json
import uuid
import threading
import time
from typing import Optional, Dict, Any
import requests as http_requests

from pylon.core.tools import log, web


# Maximum chat session duration in seconds (30 minutes)
MAX_CHAT_SESSION_DURATION = 30 * 60

# Cleanup interval in seconds (check every 2 minutes)
CLEANUP_INTERVAL = 2 * 60

# Module-level registry for HTTP streaming sessions (with timeout tracking)
_http_chat_sessions: Dict[str, Dict[str, Any]] = {}
_http_chat_sessions_lock = threading.Lock()
_cleanup_thread_started = False
_cleanup_stop_event: Optional[threading.Event] = None


def _start_cleanup_thread():
    """Start background thread for cleaning up stale HTTP sessions."""
    global _cleanup_thread_started, _cleanup_stop_event

    if _cleanup_thread_started:
        return

    _cleanup_thread_started = True
    _cleanup_stop_event = threading.Event()

    def cleanup_loop():
        log.info("[http_chat_cleanup] Cleanup thread started")
        while not _cleanup_stop_event.is_set():
            try:
                _cleanup_stale_sessions()
            except Exception as e:
                log.exception(f"[http_chat_cleanup] Error in cleanup: {e}")

            # Wait for next cleanup interval or stop event
            _cleanup_stop_event.wait(timeout=CLEANUP_INTERVAL)

        log.info("[http_chat_cleanup] Cleanup thread stopped")

    thread = threading.Thread(target=cleanup_loop, daemon=True, name="http_chat_session_cleanup")
    thread.start()


def _cleanup_stale_sessions():
    """Clean up sessions that have exceeded the maximum duration."""
    now = time.time()
    stale_sessions = []

    with _http_chat_sessions_lock:
        for session_id, session_data in list(_http_chat_sessions.items()):
            started_at = session_data.get("started_at")
            if started_at:
                duration = now - started_at
                if duration > MAX_CHAT_SESSION_DURATION:
                    stale_sessions.append(session_id)
                    # Mark as cancelled so the running thread will stop
                    session_data["cancelled"].set()
                    session_data["timed_out"] = True

    if stale_sessions:
        log.warning(f"[http_chat_cleanup] Marked {len(stale_sessions)} stale sessions for cleanup: {stale_sessions}")


def _register_http_session(session_id: str) -> Dict[str, Any]:
    """Register an HTTP chat session for cancellation and timeout tracking."""
    # Start cleanup thread if not already running
    _start_cleanup_thread()

    session_data = {
        "cancelled": threading.Event(),
        "started_at": time.time(),
        "timed_out": False,
    }
    with _http_chat_sessions_lock:
        _http_chat_sessions[session_id] = session_data
    return session_data


def _unregister_http_session(session_id: str):
    """Unregister an HTTP chat session."""
    with _http_chat_sessions_lock:
        _http_chat_sessions.pop(session_id, None)


def _cancel_http_session(session_id: str) -> bool:
    """Cancel an HTTP chat session."""
    with _http_chat_sessions_lock:
        session_data = _http_chat_sessions.get(session_id)
        if session_data:
            session_data["cancelled"].set()
            return True
    return False


def _is_session_timed_out(session_id: str) -> bool:
    """Check if a session has timed out."""
    with _http_chat_sessions_lock:
        session_data = _http_chat_sessions.get(session_id)
        if session_data:
            # Check explicit timeout flag
            if session_data.get("timed_out"):
                return True
            # Check duration
            started_at = session_data.get("started_at")
            if started_at and (time.time() - started_at) > MAX_CHAT_SESSION_DURATION:
                session_data["timed_out"] = True
                session_data["cancelled"].set()
                return True
    return False


def _check_session_valid(session_id: str) -> str:
    """
    Check if session is still valid.

    Returns:
        "ok" if valid
        "cancelled" if cancelled by user
        "timeout" if timed out
    """
    if _is_session_timed_out(session_id):
        return "timeout"
    with _http_chat_sessions_lock:
        session_data = _http_chat_sessions.get(session_id)
        if session_data and session_data["cancelled"].is_set():
            return "cancelled"
    return "ok"


class Route:
    """Chat route"""

    # Route for ui_host proxy: /ui/{toolkit_id}/chat
    @web.route("/ui/<int:toolkit_id>/chat", methods=["POST"], endpoint="chat_route_proxy")
    # Route for direct access: /ui/{project_id}/{toolkit_id}/chat
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat", methods=["POST"], endpoint="chat_route_direct")
    def chat_route(self, project_id=None, toolkit_id=None):
        """
        Handle chat requests (synchronous).

        Request body:
        {
            "prompt": str,
            "filters": {...},  # optional
            "history": [{"role": "user|assistant", "content": "..."}]  # optional
        }

        Response:
        {
            "answer": str,
            "citations": [...],
            "tool_calls": [...],
            "error": str or null
        }
        """
        try:
            request_data = flask.request.json or {}
        except Exception:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": "Invalid JSON payload",
            }, 400

        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id or request_data.get("project_id")
        effective_toolkit_id = toolkit_id or request_data.get("toolkit_id")

        prompt = request_data.get("prompt", "").strip()

        if not effective_project_id:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": "project_id is required",
            }, 400

        if not effective_toolkit_id:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": "toolkit_id is required",
            }, 400

        if not prompt:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": "prompt is required",
            }, 400

        # Optional fields
        filters = request_data.get("filters", {})
        conversation_id = request_data.get("conversation_id")
        history = request_data.get("history", [])
        model = request_data.get("model")  # Optional model override

        log.info(f"[chat_route] Received chat request: project={effective_project_id}, toolkit={effective_toolkit_id}, model={model}")

        # Call the inventory_chat method
        try:
            result = self.inventory_chat(
                project_id=int(effective_project_id),
                toolkit_id=int(effective_toolkit_id),
                prompt=prompt,
                filters=filters,
                conversation_id=conversation_id,
                history=history,
                emit_fn=None,  # No streaming for HTTP route
                model=model,
            )

            return result, 200

        except Exception as e:
            log.exception(f"[chat_route] Error: {e}")
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": str(e),
            }, 500

    # Route for ui_host proxy: /ui/{toolkit_id}/chat/stream
    @web.route("/ui/<int:toolkit_id>/chat/stream", methods=["POST"], endpoint="chat_stream_route_proxy")
    # Route for direct access: /ui/{project_id}/{toolkit_id}/chat/stream
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/stream", methods=["POST"], endpoint="chat_stream_route_direct")
    def chat_stream_route(self, project_id=None, toolkit_id=None):
        """
        Handle streaming chat requests using Server-Sent Events (SSE).

        Request body: { "prompt": str, "filters": {...}, "history": [...], "session_id": str }

        Response: SSE stream of events in real-time.
        First event includes session_id for cancellation.
        """
        import queue

        try:
            request_data = flask.request.json or {}
        except Exception:
            return flask.Response(
                f"data: {json.dumps({'error': 'Invalid JSON payload'})}\n\n",
                mimetype='text/event-stream',
                status=400,
            )

        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id or request_data.get("project_id")
        effective_toolkit_id = toolkit_id or request_data.get("toolkit_id")
        prompt = request_data.get("prompt", "").strip()

        if not all([effective_project_id, effective_toolkit_id, prompt]):
            return flask.Response(
                f"data: {json.dumps({'error': 'project_id, toolkit_id, and prompt are required'})}\n\n",
                mimetype='text/event-stream',
                status=400,
            )

        filters = request_data.get("filters", {})
        conversation_id = request_data.get("conversation_id")
        history = request_data.get("history", [])
        model = request_data.get("model")  # Optional model override

        # Create or use provided session_id for cancellation tracking
        session_id = request_data.get("session_id") or str(uuid.uuid4())

        # Register session for cancellation and timeout tracking
        session_data = _register_http_session(session_id)

        # Create cancellation/timeout check function
        def is_cancelled() -> bool:
            # Check both cancellation and timeout
            status = _check_session_valid(session_id)
            return status != "ok"

        # Use a queue for real-time streaming
        event_queue = queue.Queue()

        def emit_fn(event_type: str, data: dict):
            """Callback to push events to queue immediately."""
            event_queue.put({
                "event": event_type,
                "data": {**data, "session_id": session_id},
            })

        def run_chat():
            """Run chat in background thread."""
            try:
                result = self.inventory_chat(
                    project_id=int(effective_project_id),
                    toolkit_id=int(effective_toolkit_id),
                    prompt=prompt,
                    filters=filters,
                    conversation_id=conversation_id,
                    history=history,
                    emit_fn=emit_fn,
                    model=model,
                    is_cancelled=is_cancelled,
                )

                # Check session status before emitting result
                session_status = _check_session_valid(session_id)

                if session_status == "timeout":
                    log.warning(f"[chat_stream_route] Chat timed out: session_id={session_id}")
                    event_queue.put({
                        "event": "chat_timeout",
                        "data": {
                            "message": "Chat session timed out after 30 minutes",
                            "session_id": session_id,
                        },
                    })
                elif session_status == "cancelled" or result.get("cancelled"):
                    log.info(f"[chat_stream_route] Chat cancelled: session_id={session_id}")
                    event_queue.put({
                        "event": "chat_cancelled",
                        "data": {"message": "Chat cancelled by user", "session_id": session_id},
                    })
                else:
                    # Push final result
                    event_queue.put({
                        "event": "chat_result",
                        "data": {**result, "session_id": session_id},
                    })
            except Exception as e:
                # Check if it was actually a timeout
                if _is_session_timed_out(session_id):
                    log.warning(f"[chat_stream_route] Chat timed out: session_id={session_id}")
                    event_queue.put({
                        "event": "chat_timeout",
                        "data": {
                            "message": "Chat session timed out after 30 minutes",
                            "session_id": session_id,
                        },
                    })
                else:
                    log.exception(f"[chat_stream_route] Error in chat thread: {e}")
                    event_queue.put({
                        "event": "error",
                        "data": {"error": str(e), "session_id": session_id},
                    })
            finally:
                # Unregister session and signal end of stream
                _unregister_http_session(session_id)
                event_queue.put(None)

        def generate():
            """Generator function for SSE streaming."""
            # Emit session start with session_id for client to use for cancellation
            yield f"event: chat_start\n"
            yield f"data: {json.dumps({'session_id': session_id, 'message': 'Starting chat...', 'max_duration_seconds': MAX_CHAT_SESSION_DURATION})}\n\n"

            # Start chat in background thread
            chat_thread = threading.Thread(target=run_chat, daemon=True)
            chat_thread.start()

            # Yield events as they arrive
            while True:
                try:
                    # Wait for event with timeout to prevent hanging
                    event = event_queue.get(timeout=120)  # 2 minute timeout

                    if event is None:
                        # End of stream
                        yield f"event: done\n"
                        yield f"data: {json.dumps({'status': 'complete', 'session_id': session_id})}\n\n"
                        break

                    yield f"event: {event['event']}\n"
                    yield f"data: {json.dumps(event['data'])}\n\n"

                except queue.Empty:
                    # Timeout - send keep-alive
                    yield f": keep-alive\n\n"
                except Exception as e:
                    log.exception(f"[chat_stream_route] Error in generator: {e}")
                    yield f"event: error\n"
                    yield f"data: {json.dumps({'error': str(e), 'session_id': session_id})}\n\n"
                    break

        return flask.Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )

    # Route for cancelling HTTP streaming chat
    @web.route("/ui/<int:toolkit_id>/chat/cancel", methods=["POST"], endpoint="chat_cancel_route_proxy")
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/cancel", methods=["POST"], endpoint="chat_cancel_route_direct")
    def chat_cancel_route(self, project_id=None, toolkit_id=None):
        """
        Cancel an active HTTP streaming chat session.

        Request body: { "session_id": str }
        """
        try:
            request_data = flask.request.json or {}
        except Exception:
            return {"error": "Invalid JSON payload"}, 400

        session_id = request_data.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}, 400

        if _cancel_http_session(session_id):
            log.info(f"[chat_cancel_route] Cancelled session: {session_id}")
            return {"status": "cancelled", "session_id": session_id}, 200
        else:
            return {"status": "not_found", "session_id": session_id, "message": "Session not found or already completed"}, 404

    # Route for ui_host proxy: /ui/{toolkit_id}/chat/history
    @web.route("/ui/<int:toolkit_id>/chat/history", methods=["GET"], endpoint="chat_history_route_proxy")
    # Route for direct access
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/history", methods=["GET"], endpoint="chat_history_route_direct")
    def chat_history_route(self, project_id=None, toolkit_id=None):
        """
        Get chat history for a toolkit.

        Query params:
        - limit: int (default 50)
        - offset: int (default 0)
        """
        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id

        limit = flask.request.args.get("limit", 50, type=int)
        offset = flask.request.args.get("offset", 0, type=int)

        log.info(f"[chat_history_route] Getting history for project={effective_project_id}, toolkit={toolkit_id}")

        # Load history from toolkit's data directory
        try:
            history_path = f"/data/graphs/{effective_project_id}/{toolkit_id}/chat_history.json"
            import os
            if os.path.exists(history_path):
                with open(history_path, "r") as f:
                    all_history = json.load(f)

                # Apply pagination
                total = len(all_history)
                paginated = all_history[offset:offset + limit]

                return {
                    "history": paginated,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
            else:
                return {
                    "history": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                }

        except Exception as e:
            log.exception(f"[chat_history_route] Error: {e}")
            return {"error": str(e)}, 500

    # Route for ui_host proxy: /ui/{toolkit_id}/chat/history
    @web.route("/ui/<int:toolkit_id>/chat/history", methods=["POST"], endpoint="chat_history_save_route_proxy")
    # Route for direct access
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/history", methods=["POST"], endpoint="chat_history_save_route_direct")
    def chat_history_save_route(self, project_id=None, toolkit_id=None):
        """
        Save a message to chat history.

        Request body:
        {
            "role": "user" | "assistant",
            "content": str,
            "citations": [...],  # optional, for assistant messages
            "tool_calls": [...],  # optional, for assistant messages
        }
        """
        try:
            request_data = flask.request.json or {}
        except Exception:
            return {"error": "Invalid JSON payload"}, 400

        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id or request_data.get("project_id")
        effective_toolkit_id = toolkit_id or request_data.get("toolkit_id")

        if not effective_project_id or not effective_toolkit_id:
            return {"error": "project_id and toolkit_id are required"}, 400

        role = request_data.get("role")
        content = request_data.get("content", "")

        if role not in ["user", "assistant"]:
            return {"error": "role must be 'user' or 'assistant'"}, 400

        log.info(f"[chat_history_save_route] Saving message for project={effective_project_id}, toolkit={effective_toolkit_id}")

        try:
            import os
            from datetime import datetime, timezone

            history_path = f"/data/graphs/{effective_project_id}/{effective_toolkit_id}/chat_history.json"

            # Ensure directory exists
            os.makedirs(os.path.dirname(history_path), exist_ok=True)

            # Load existing history
            if os.path.exists(history_path):
                with open(history_path, "r") as f:
                    history = json.load(f)
            else:
                history = []

            # Add new message
            message = {
                "id": str(uuid.uuid4()),
                "role": role,
                "content": content,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            if role == "assistant":
                message["citations"] = request_data.get("citations", [])
                message["tool_calls"] = request_data.get("tool_calls", [])

            history.append(message)

            # Save history (keep last 1000 messages)
            if len(history) > 1000:
                history = history[-1000:]

            with open(history_path, "w") as f:
                json.dump(history, f, indent=2)

            return {"message": message}, 200

        except Exception as e:
            log.exception(f"[chat_history_save_route] Error: {e}")
            return {"error": str(e)}, 500

    # Route for ui_host proxy: /ui/{toolkit_id}/chat/history
    @web.route("/ui/<int:toolkit_id>/chat/history", methods=["DELETE"], endpoint="chat_history_clear_route_proxy")
    # Route for direct access
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/history", methods=["DELETE"], endpoint="chat_history_clear_route_direct")
    def chat_history_clear_route(self, project_id=None, toolkit_id=None):
        """Clear chat history for a toolkit."""
        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id
        effective_toolkit_id = toolkit_id

        if not effective_project_id or not effective_toolkit_id:
            return {"error": "project_id and toolkit_id are required"}, 400

        log.info(f"[chat_history_clear_route] Clearing history for project={effective_project_id}, toolkit={effective_toolkit_id}")

        try:
            import os

            history_path = f"/data/graphs/{effective_project_id}/{effective_toolkit_id}/chat_history.json"

            if os.path.exists(history_path):
                os.remove(history_path)

            return {"status": "cleared"}, 200

        except Exception as e:
            log.exception(f"[chat_history_clear_route] Error: {e}")
            return {"error": str(e)}, 500

    # Route for ui_host proxy: /ui/{toolkit_id}/chat/models
    @web.route("/ui/<int:toolkit_id>/chat/models", methods=["GET"], endpoint="chat_models_route_proxy")
    # Route for direct access
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/chat/models", methods=["GET"], endpoint="chat_models_route_direct")
    def chat_models_route(self, project_id=None, toolkit_id=None):
        """
        Get available LLM models for chat.

        Returns list of models from platform configurations.
        """
        # Get project_id from header (ui_host proxy) or path param
        header_project_id = flask.request.headers.get('X-Project-Id')
        effective_project_id = header_project_id or project_id

        if not effective_project_id:
            return {"error": "project_id is required", "models": []}, 400

        log.info(f"[chat_models_route] Getting models for project={effective_project_id}")

        try:
            # Get EliteAClient to fetch models from platform
            elitea_client = self._get_elitea_client(int(effective_project_id))
            if not elitea_client:
                return {"error": "Platform API not configured", "models": []}, 500

            # Fetch LLM models from platform (includes shared models)
            models_url = f"{elitea_client.base_url}/api/v2/configurations/models/{effective_project_id}?include_shared=true"
            resp = http_requests.get(models_url, headers=elitea_client.headers, verify=False)

            if not resp.ok:
                log.warning(f"[chat_models_route] Failed to fetch models: {resp.status_code}")
                return {"error": f"Failed to fetch models: {resp.status_code}", "models": []}, 500

            data = resp.json()

            # Extract model names from items array
            models = []
            default_model = data.get("default_model_name")

            for item in data.get("items", []):
                model_name = item.get("name", "")
                display_name = item.get("display_name", model_name)
                if model_name:
                    models.append({
                        "name": model_name,
                        "display_name": display_name,
                        "is_default": model_name == default_model,
                        "context_window": item.get("context_window"),
                        "supports_reasoning": item.get("supports_reasoning", False),
                    })

            # Sort: default first, then by display name
            models.sort(key=lambda x: (not x.get("is_default", False), x["display_name"].lower()))

            log.info(f"[chat_models_route] Found {len(models)} models")

            return {"models": models}, 200

        except Exception as e:
            log.exception(f"[chat_models_route] Error: {e}")
            return {"error": str(e), "models": []}, 500
