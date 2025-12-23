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
                invocation_state["stop_requested"] = True

                # Terminate any managed subprocesses
                if "processes" in invocation_state:
                    for proc in invocation_state["processes"]:
                        if proc.poll() is None:
                            proc.terminate()

            return flask.Response(status=204)

        return {
            "errorCode": "500",
            "message": "Internal Server Error",
            "details": [],
        }, 500
