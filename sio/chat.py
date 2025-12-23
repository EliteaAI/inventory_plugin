#!/usr/bin/python3
# coding=utf-8

"""
Inventory Chat Socket.IO Event Handler

Handles real-time streaming chat via socket.io.
"""

import json
import threading
from typing import Dict, Any

from pylon.core.tools import log, web


class SIO:
    """Socket.IO event handlers for inventory chat."""

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
        }

        Emits events:
        - inventory_chat_start: Chat started
        - inventory_chat_tool_start: Tool execution started
        - inventory_chat_tool_end: Tool execution completed
        - inventory_chat_llm_start: LLM call started
        - inventory_chat_llm_token: LLM token received (streaming)
        - inventory_chat_llm_end: LLM call completed
        - inventory_chat_complete: Final result
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
                },
                room=sid,
            )

        # Emit start event
        emit_fn("start", {"message": "Starting chat..."})

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
                )

                # Emit final result
                self.context.sio.emit(
                    "inventory_chat_complete",
                    {
                        **result,
                        "project_id": project_id,
                        "toolkit_id": toolkit_id,
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
                    },
                    room=sid,
                )

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
        }
        """
        log.info(f"[inventory_chat_cancel sio] Received cancel request from {sid}")

        # For now, we just acknowledge the cancel request
        # In a more sophisticated implementation, we'd track active sessions
        # and actually cancel them
        self.context.sio.emit(
            "inventory_chat_cancelled",
            {"message": "Cancel request received"},
            room=sid,
        )
