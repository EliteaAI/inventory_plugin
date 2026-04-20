#!/usr/bin/python3
# coding=utf-8

"""Tool Invocation Route"""

import flask

from pylon.core.tools import log, web


class Route:
    """Invocation route"""

    # Primary route for direct access
    @web.route("/tools/<toolkit_name>/<tool_name>/invoke", methods=["POST"], endpoint="invoke_route")
    # Alternate route for ui_host proxy access (proxies to /ui/...)
    @web.route("/ui/tools/<toolkit_name>/<tool_name>/invoke", methods=["POST"], endpoint="invoke_route_ui")
    def invoke_route(self, toolkit_name, tool_name):
        """Handle tool invocation requests"""
        # Validate request
        try:
            request_data = flask.request.json
        except Exception:
            return {
                "errorCode": "400",
                "message": "Bad Request",
                "details": ["Invalid JSON payload"],
            }, 400

        # Start async task
        invocation_id = self.invocation_task_node.start_task(
            "perform_invoke_request",
            kwargs={
                "toolkit_name": toolkit_name,
                "tool_name": tool_name,
                "request_data": request_data,
            },
            pool="invocation",
            meta={
                "toolkit_name": toolkit_name,
                "tool_name": tool_name,
            },
        )

        if invocation_id is None:
            return {
                "errorCode": "500",
                "message": "Internal Server Error",
                "details": ["Failed to start invocation task"],
            }, 500

        # Return invocation ID immediately for async polling
        return {
            "invocation_id": invocation_id,
            "status": "Started",
        }
