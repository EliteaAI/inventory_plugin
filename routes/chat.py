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
from typing import Optional

from pylon.core.tools import log, web


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

        log.info(f"[chat_route] Received chat request: project={effective_project_id}, toolkit={effective_toolkit_id}")

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

        Request body: { "prompt": str, "filters": {...}, "history": [...] }

        Response: SSE stream of events in real-time.
        """
        import threading
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

        # Use a queue for real-time streaming
        event_queue = queue.Queue()

        def emit_fn(event_type: str, data: dict):
            """Callback to push events to queue immediately."""
            event_queue.put({
                "event": event_type,
                "data": data,
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
                )
                # Push final result
                event_queue.put({
                    "event": "chat_result",
                    "data": result,
                })
            except Exception as e:
                log.exception(f"[chat_stream_route] Error in chat thread: {e}")
                event_queue.put({
                    "event": "error",
                    "data": {"error": str(e)},
                })
            finally:
                # Signal end of stream
                event_queue.put(None)

        def generate():
            """Generator function for SSE streaming."""
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
                        yield f"data: {json.dumps({'status': 'complete'})}\n\n"
                        break

                    yield f"event: {event['event']}\n"
                    yield f"data: {json.dumps(event['data'])}\n\n"

                except queue.Empty:
                    # Timeout - send keep-alive
                    yield f": keep-alive\n\n"
                except Exception as e:
                    log.exception(f"[chat_stream_route] Error in generator: {e}")
                    yield f"event: error\n"
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
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
