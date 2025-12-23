#!/usr/bin/python3
# coding=utf-8

"""Invocation State Management Methods"""

import time

from pylon.core.tools import log
from pylon.core.tools import web

from arbiter.tasknode.tools import InterruptTaskThread


class Method:
    """
    Method Resource for invocation state management

    self is pointing to current Module instance

    web.method decorator takes zero or one argument: method name
    Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def invocation_task_change(self, event, data):
        """Handle task state changes"""
        _ = event

        task_id = data.get("task_id", None)
        status = data.get("status", "unknown")  # pending, running, stopped, pruned

        if task_id is None or status == "unknown":
            return

        if status == "pruned":
            # Cleanup pruned tasks from all toolkits
            with self.state_lock:
                for _toolkit_name, toolkit_state in self.invocation_state.items():
                    for _tool_name, tool_state in toolkit_state.items():
                        tool_state.pop(task_id, None)
            return

        # Get task metadata
        task_meta = self.invocation_task_node.get_task_meta(task_id)

        toolkit_name = task_meta.get("toolkit_name", "Toolkit")
        tool_name = task_meta.get("tool_name", "tool")

        with self.state_lock:
            # Ensure state structure exists
            if toolkit_name not in self.invocation_state:
                self.invocation_state[toolkit_name] = {}

            if tool_name not in self.invocation_state[toolkit_name]:
                self.invocation_state[toolkit_name][tool_name] = {}

            if task_id not in self.invocation_state[toolkit_name][tool_name]:
                self.invocation_state[toolkit_name][tool_name][task_id] = {
                    "task_id": task_id,
                    "added_ts": time.time(),
                }

            # Update status
            self.invocation_state[toolkit_name][tool_name][task_id]["status"] = status

            # If task is stopped, get result
            if status == "stopped":
                try:
                    result = self.invocation_task_node.get_task_result(task_id)
                except BaseException as exception:
                    log.exception("Failed to invoke %s:%s", toolkit_name, tool_name)
                    exception_info = str(exception)
                    result = {
                        "errorCode": "500",
                        "message": "Internal Server Error",
                        "details": [exception_info],
                    }, 500

                self.invocation_state[toolkit_name][tool_name][task_id]["result"] = result

    @web.method()
    def invocation_thinking(self, message):
        """Store custom event/progress message for current task"""
        try:
            import tasknode_task

            task_id = tasknode_task.id
            task_meta = tasknode_task.meta
        except:
            return

        toolkit_name = task_meta.get("toolkit_name", "Toolkit")
        tool_name = task_meta.get("tool_name", "tool")

        with self.state_lock:
            if toolkit_name not in self.invocation_state:
                return

            if tool_name not in self.invocation_state[toolkit_name]:
                return

            if task_id not in self.invocation_state[toolkit_name][tool_name]:
                return

            if "custom_events" not in self.invocation_state[toolkit_name][tool_name][task_id]:
                self.invocation_state[toolkit_name][tool_name][task_id]["custom_events"] = []

            self.invocation_state[toolkit_name][tool_name][task_id]["custom_events"].append({
                "data": {
                    "message": message,
                },
            })

    @web.method()
    def invocation_stop_checkpoint(self):
        """Check if stop has been requested for current task"""
        try:
            import tasknode_task

            task_id = tasknode_task.id
            task_meta = tasknode_task.meta
        except:
            return

        toolkit_name = task_meta.get("toolkit_name", "Toolkit")
        tool_name = task_meta.get("tool_name", "tool")

        with self.state_lock:
            if toolkit_name not in self.invocation_state:
                return

            if tool_name not in self.invocation_state[toolkit_name]:
                return

            if task_id not in self.invocation_state[toolkit_name][tool_name]:
                return

            invocation_state = self.invocation_state[toolkit_name][tool_name][task_id]

            if "stop_requested" not in invocation_state:
                return

            if invocation_state["stop_requested"]:
                # Terminate any managed processes
                if "processes" in invocation_state:
                    for proc in invocation_state["processes"]:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=3)
                            except:
                                proc.kill()
                                try:
                                    proc.wait(timeout=1)
                                except:
                                    pass

                raise InterruptTaskThread()

    @web.method()
    def invocation_process_add(self, proc):
        """Add a subprocess to be managed by the current invocation"""
        try:
            import tasknode_task

            task_id = tasknode_task.id
            task_meta = tasknode_task.meta
        except:
            return

        toolkit_name = task_meta.get("toolkit_name", "Toolkit")
        tool_name = task_meta.get("tool_name", "tool")

        with self.state_lock:
            if toolkit_name not in self.invocation_state:
                return

            if tool_name not in self.invocation_state[toolkit_name]:
                return

            if task_id not in self.invocation_state[toolkit_name][tool_name]:
                return

            invocation_state = self.invocation_state[toolkit_name][tool_name][task_id]

            if "processes" not in invocation_state:
                invocation_state["processes"] = []

            invocation_state["processes"].append(proc)

    @web.method()
    def invocation_process_remove(self, proc):
        """Remove a subprocess from management"""
        try:
            import tasknode_task

            task_id = tasknode_task.id
            task_meta = tasknode_task.meta
        except:
            return

        toolkit_name = task_meta.get("toolkit_name", "Toolkit")
        tool_name = task_meta.get("tool_name", "tool")

        with self.state_lock:
            if toolkit_name not in self.invocation_state:
                return

            if tool_name not in self.invocation_state[toolkit_name]:
                return

            if task_id not in self.invocation_state[toolkit_name][tool_name]:
                return

            invocation_state = self.invocation_state[toolkit_name][tool_name][task_id]

            if "processes" not in invocation_state:
                return

            if proc in invocation_state["processes"]:
                invocation_state["processes"].remove(proc)
