#!/usr/bin/python3
# coding=utf-8

"""Invocation Status Route"""

import flask

from pylon.core.tools import log, web


class Route:
    """Invocation status route"""

    # Primary route for direct access
    @web.route(
        "/tools/<toolkit_name>/<tool_name>/invocations/<invocation_id>",
        methods=["GET", "DELETE"],
        endpoint="invocations_route"
    )
    # Alternate route for ui_host proxy access (proxies to /ui/...)
    @web.route(
        "/ui/tools/<toolkit_name>/<tool_name>/invocations/<invocation_id>",
        methods=["GET", "DELETE"],
        endpoint="invocations_route_ui"
    )
    def invocations_route(self, toolkit_name, tool_name, invocation_id):
        """Handle invocation status queries and cancellation"""
        if flask.request.method == "GET":
            # Handle GET - get invocation status
            with self.state_lock:
                # Check toolkit exists
                if toolkit_name not in self.invocation_state:
                    return {
                        "errorCode": "404",
                        "message": "Resource Not Found",
                        "details": [f"Toolkit '{toolkit_name}' not found"],
                    }, 404

                # Check tool exists
                if tool_name not in self.invocation_state[toolkit_name]:
                    return {
                        "errorCode": "404",
                        "message": "Resource Not Found",
                        "details": [f"Tool '{tool_name}' not found"],
                    }, 404

                # Check invocation exists
                if invocation_id not in self.invocation_state[toolkit_name][tool_name]:
                    return {
                        "errorCode": "404",
                        "message": "Resource Not Found",
                        "details": [f"Invocation '{invocation_id}' not found"],
                    }, 404

                invocation_state = self.invocation_state[toolkit_name][tool_name][invocation_id]
                invocation_status = invocation_state["status"]

                # Extract and clear custom events (streaming progress)
                custom_events = {}
                if "custom_events" in invocation_state and invocation_state["custom_events"]:
                    custom_events["custom_events"] = invocation_state["custom_events"].copy()
                    invocation_state["custom_events"].clear()

                # Return based on status
                if invocation_status == "pending":
                    return {
                        "invocation_id": invocation_id,
                        "status": "Started",
                        **custom_events,
                    }

                if invocation_status == "running":
                    return {
                        "invocation_id": invocation_id,
                        "status": "InProgress",
                        **custom_events,
                    }

                # Task completed - return result
                if "result" in invocation_state:
                    return invocation_state["result"]

            return {
                "invocation_id": invocation_id,
                "status": "Unknown",
            }

        elif flask.request.method == "DELETE":
            # Handle DELETE - cancel/stop an invocation
            log.info(f"[STOP_DELETE] Received stop request for {toolkit_name}/{tool_name}/{invocation_id}")
            log.info(f"[STOP_DELETE] Current invocation_state keys: {list(self.invocation_state.keys())}")

            # First, try to find the invocation in the in-memory state
            found_in_state = False
            with self.state_lock:
                if (toolkit_name in self.invocation_state and
                    tool_name in self.invocation_state[toolkit_name] and
                    invocation_id in self.invocation_state[toolkit_name][tool_name]):

                    found_in_state = True
                    invocation_state = self.invocation_state[toolkit_name][tool_name][invocation_id]
                    invocation_state["stop_requested"] = True
                    log.info(f"[STOP_DELETE] Successfully set stop_requested=True for {invocation_id}")

                    # Terminate any managed subprocesses
                    if "processes" in invocation_state:
                        for proc in invocation_state["processes"]:
                            if proc.poll() is None:
                                proc.terminate()

            if found_in_state:
                return flask.Response(status=204)

            # If not found in in-memory state, check if it's a stale entry in IngestionTracker
            # This happens when container restarts during an ingestion - the tracker persists
            # to disk but invocation_state is lost
            log.info(f"[STOP_DELETE] Task {invocation_id} not in invocation_state, checking IngestionTracker")

            try:
                # Check if this task is in the tracker
                active_ingestions = self.ingestion_tracker.get_active_ingestions()
                stale_ingestion = None
                for ing in active_ingestions:
                    if ing.get("task_id") == invocation_id:
                        stale_ingestion = ing
                        break

                if stale_ingestion:
                    log.info(f"[STOP_DELETE] Found stale task {invocation_id} in IngestionTracker, releasing slot")

                    # Update source status to mark as stopped for the recovered stale entry.
                    try:
                        from ..utils.source_status import SourceStatusManager
                        project_id = stale_ingestion.get("project_id")
                        application_id = stale_ingestion.get("application_id")
                        toolkit_id = stale_ingestion.get("toolkit_id")

                        if project_id and application_id:
                            graph_dir = f"/data/graphs/{project_id}/{application_id}"
                            status_manager = SourceStatusManager(graph_dir)
                            status_manager.stop_ingestion(
                                toolkit_id=str(toolkit_id),
                                message="Ingestion stopped by user",
                                documents_processed=0,
                            )
                            log.info(f"[STOP_DELETE] Updated source status for toolkit {toolkit_id}")
                    except Exception as status_error:
                        log.warning(f"[STOP_DELETE] Failed to update source status: {status_error}")

                    # Release the slot in the tracker (cleans up the stale entry)
                    self.ingestion_tracker.release_slot(invocation_id)
                    log.info(f"[STOP_DELETE] Successfully released stale slot for {invocation_id}")
                    return flask.Response(status=204)
                else:
                    log.warning(f"[STOP_DELETE] Task {invocation_id} not found in invocation_state or IngestionTracker")
            except Exception as e:
                log.warning(f"[STOP_DELETE] Error checking IngestionTracker: {e}")

            # Not found anywhere - return 404
            return {
                "errorCode": "404",
                "message": "Resource Not Found",
                "details": [f"Invocation '{invocation_id}' not found"],
            }, 404

        return {
            "errorCode": "500",
            "message": "Internal Server Error",
            "details": [],
        }, 500
