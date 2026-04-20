#!/usr/bin/python3
# coding=utf-8

"""Health Check Route"""

import datetime
import time

from pylon.core.tools import web


class Route:
    """Health check route"""

    @web.route("/health", methods=["GET"])
    def health_route(self):
        """Return plugin health status"""
        try:
            current_time = time.time()
            uptime = current_time - getattr(self, 'start_time', current_time)

            # Count active invocations
            active_invocations = 0
            with self.state_lock:
                for toolkit_state in self.invocation_state.values():
                    for tool_state in toolkit_state.values():
                        for inv_state in tool_state.values():
                            if inv_state.get("status") in ("pending", "running"):
                                active_invocations += 1

            # Count loaded graphs
            loaded_graphs = len(self.graph_instances)

            return {
                "status": "UP",
                "providerVersion": "1.0.0",
                "uptime": int(uptime),
                "timestamp": datetime.datetime.now(
                    datetime.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "plugin": "InventoryPlugin",
                "extra_info": {
                    "active_invocations": active_invocations,
                    "loaded_graphs": loaded_graphs,
                },
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "plugin": "inventory",
                "error": str(e)
            }, 500
