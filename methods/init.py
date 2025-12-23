#!/usr/bin/python3
# coding=utf-8

"""Initialization Methods"""

import time
import arbiter
import threading

from pylon.core.tools import log, web

from ..utils.cache_manager import GraphCacheManager
from ..utils.ingestion_tracker import IngestionTracker


class Method:
    """Initialization methods"""

    @web.init()
    def init_config(self):
        """Initialize plugin configuration"""
        log.info("Initializing Inventory Plugin configuration")

        # Store start time for health checks
        self.start_time = time.time()

        # Initialize state lock for thread-safe invocation tracking
        self.state_lock = threading.Lock()

        # Invocation state: toolkit -> tool -> invocation_id -> state
        self.invocation_state = {}

        # Current loaded graph state per toolkit instance
        self.graph_instances = {}

        # Initialize cache manager with config
        cache_config = self.descriptor.config.get("cache", {})
        self.cache_manager = GraphCacheManager(
            base_path=cache_config.get("base_path", "/data/graphs"),
            max_age_days=cache_config.get("max_age_days", 7),
            max_cache_size_gb=cache_config.get("max_cache_size_gb", 5.0),
            max_graphs=cache_config.get("max_graphs", 50),
            housekeeping_interval_seconds=cache_config.get("housekeeping_interval_seconds", 3600),
        )

        # Start cache housekeeping
        self.cache_manager.start_housekeeping()

        # Initialize ingestion tracker for parallel limit enforcement
        max_parallel = self.descriptor.config.get("max_parallel_ingestions", 2)
        self.ingestion_tracker = IngestionTracker(
            base_path=cache_config.get("base_path", "/data/graphs"),
            max_parallel=max_parallel,
        )

        # Cleanup any stale ingestions from previous runs
        self.ingestion_tracker.cleanup_stale_ingestions(max_age_hours=24)

        # Create event node for task management
        self.invocation_event_node = arbiter.make_event_node(
            config={
                "type": "MockEventNode",
            },
        )

        # Create task node for async invocations
        self.invocation_task_node = arbiter.TaskNode(
            self.invocation_event_node,
            pool="invocation",
            task_limit=None,
            ident_prefix="inventory_",
            multiprocessing_context="threading",
            task_retention_period=3600,
            housekeeping_interval=60,
            thread_scan_interval=0.1,
            start_max_wait=1,
            query_wait=1,
            watcher_max_wait=1,
            stop_node_task_wait=1,
            result_max_wait=1,
            result_transport="memory",
            start_attempts=1,
        )

        # Start task node
        self.invocation_task_node.start()
        self.invocation_task_node.subscribe_to_task_statuses(self.invocation_task_change)

        # Register task handlers
        self.invocation_task_node.register_task(
            self.perform_invoke_request, "perform_invoke_request",
        )

        log.info("Inventory Plugin configuration initialized")

    @web.deinit()
    def deinit_config(self):
        """Cleanup on shutdown"""
        log.info("Cleaning up Inventory Plugin")

        # Stop cache housekeeping
        if hasattr(self, 'cache_manager'):
            self.cache_manager.stop_housekeeping()

        # Unregister task handlers
        self.invocation_task_node.unregister_task(
            self.perform_invoke_request, "perform_invoke_request",
        )

        # Stop task node
        self.invocation_task_node.stop()

        log.info("Inventory Plugin cleanup complete")
