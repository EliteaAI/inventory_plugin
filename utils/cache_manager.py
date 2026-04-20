#!/usr/bin/python3
# coding=utf-8

"""
Graph Cache Manager

Handles local caching of knowledge graphs with:
- Lazy loading from artifacts API
- Write-through to artifacts
- TTL-based cache invalidation
- Background housekeeping for stale graph cleanup
"""

import os
import json
import time
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from pylon.core.tools import log


class CacheMetadata:
    """Metadata for a cached graph"""

    def __init__(
        self,
        last_accessed: Optional[str] = None,
        last_synced: Optional[str] = None,
        remote_etag: Optional[str] = None,
        local_size_bytes: int = 0,
        source_project_id: Optional[int] = None,
        source_bucket: Optional[str] = None,
    ):
        self.last_accessed = last_accessed or datetime.now(timezone.utc).isoformat()
        self.last_synced = last_synced
        self.remote_etag = remote_etag
        self.local_size_bytes = local_size_bytes
        self.source_project_id = source_project_id
        self.source_bucket = source_bucket

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_accessed": self.last_accessed,
            "last_synced": self.last_synced,
            "remote_etag": self.remote_etag,
            "local_size_bytes": self.local_size_bytes,
            "source_project_id": self.source_project_id,
            "source_bucket": self.source_bucket,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheMetadata':
        return cls(
            last_accessed=data.get("last_accessed"),
            last_synced=data.get("last_synced"),
            remote_etag=data.get("remote_etag"),
            local_size_bytes=data.get("local_size_bytes", 0),
            source_project_id=data.get("source_project_id"),
            source_bucket=data.get("source_bucket"),
        )

    def touch(self):
        """Update last accessed time"""
        self.last_accessed = datetime.now(timezone.utc).isoformat()

    def get_age_seconds(self) -> float:
        """Get seconds since last access"""
        if not self.last_accessed:
            return float('inf')
        try:
            accessed = datetime.fromisoformat(self.last_accessed.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            return (now - accessed).total_seconds()
        except Exception:
            return float('inf')


class GraphCacheManager:
    """
    Manages local caching of knowledge graphs.

    Directory structure:
    /data/graphs/{bucket}/{graph_name}/
    ├── graph.json           # The actual graph data
    └── .cache_meta.json     # Cache metadata
    """

    CACHE_META_FILE = ".cache_meta.json"
    GRAPH_FILE = "graph.json"

    def __init__(
        self,
        base_path: str = "/data/graphs",
        max_age_days: int = 7,
        max_cache_size_gb: float = 5.0,
        max_graphs: int = 50,
        housekeeping_interval_seconds: int = 3600,
    ):
        self.base_path = Path(base_path)
        self.max_age_seconds = max_age_days * 24 * 3600
        self.max_cache_size_bytes = int(max_cache_size_gb * 1024 * 1024 * 1024)
        self.max_graphs = max_graphs
        self.housekeeping_interval = housekeeping_interval_seconds

        self._lock = threading.RLock()
        self._housekeeping_thread: Optional[threading.Thread] = None
        self._stop_housekeeping = threading.Event()

        # Ensure base path exists
        self.base_path.mkdir(parents=True, exist_ok=True)

        log.info(
            f"GraphCacheManager initialized: max_age={max_age_days}d, "
            f"max_size={max_cache_size_gb}GB, max_graphs={max_graphs}"
        )

    def start_housekeeping(self):
        """Start background housekeeping thread"""
        if self._housekeeping_thread is not None:
            return

        self._stop_housekeeping.clear()
        self._housekeeping_thread = threading.Thread(
            target=self._housekeeping_loop,
            daemon=True,
            name="GraphCacheHousekeeping"
        )
        self._housekeeping_thread.start()
        log.info(f"Cache housekeeping started (interval: {self.housekeeping_interval}s)")

    def stop_housekeeping(self):
        """Stop background housekeeping thread"""
        if self._housekeeping_thread is None:
            return

        self._stop_housekeeping.set()
        self._housekeeping_thread.join(timeout=5)
        self._housekeeping_thread = None
        log.info("Cache housekeeping stopped")

    def _housekeeping_loop(self):
        """Background loop for cache cleanup"""
        while not self._stop_housekeeping.is_set():
            try:
                self.cleanup_stale_graphs()
            except Exception as e:
                log.exception(f"Housekeeping error: {e}")

            # Wait for next interval or stop signal
            self._stop_housekeeping.wait(timeout=self.housekeeping_interval)

    def get_graph_path(self, bucket: str, graph_name: str) -> Path:
        """Get the directory path for a graph"""
        return self.base_path / bucket / graph_name

    def get_graph_file_path(self, bucket: str, graph_name: str) -> Path:
        """Get the graph.json file path"""
        return self.get_graph_path(bucket, graph_name) / self.GRAPH_FILE

    def get_meta_path(self, bucket: str, graph_name: str) -> Path:
        """Get the cache metadata file path"""
        return self.get_graph_path(bucket, graph_name) / self.CACHE_META_FILE

    def _load_metadata(self, bucket: str, graph_name: str) -> Optional[CacheMetadata]:
        """Load cache metadata for a graph"""
        meta_path = self.get_meta_path(bucket, graph_name)
        if not meta_path.exists():
            return None

        try:
            with open(meta_path, 'r') as f:
                data = json.load(f)
            return CacheMetadata.from_dict(data)
        except Exception as e:
            log.warning(f"Failed to load cache metadata for {bucket}/{graph_name}: {e}")
            return None

    def _save_metadata(self, bucket: str, graph_name: str, metadata: CacheMetadata):
        """Save cache metadata for a graph"""
        meta_path = self.get_meta_path(bucket, graph_name)
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(meta_path, 'w') as f:
                json.dump(metadata.to_dict(), f, indent=2)
        except Exception as e:
            log.warning(f"Failed to save cache metadata for {bucket}/{graph_name}: {e}")

    def is_cached(self, bucket: str, graph_name: str) -> bool:
        """Check if a graph is cached locally"""
        graph_path = self.get_graph_file_path(bucket, graph_name)
        return graph_path.exists()

    def is_stale(self, bucket: str, graph_name: str) -> bool:
        """Check if a cached graph is stale (exceeded max age)"""
        metadata = self._load_metadata(bucket, graph_name)
        if metadata is None:
            return True
        return metadata.get_age_seconds() > self.max_age_seconds

    def touch(self, bucket: str, graph_name: str):
        """Update last accessed time for a cached graph"""
        with self._lock:
            metadata = self._load_metadata(bucket, graph_name)
            if metadata:
                metadata.touch()
                self._save_metadata(bucket, graph_name, metadata)

    def get_or_load(
        self,
        bucket: str,
        graph_name: str,
        fetch_func: Optional[callable] = None,
        project_id: Optional[int] = None,
    ) -> Tuple[Optional[str], bool]:
        """
        Get graph from cache or load from remote.

        Args:
            bucket: The bucket name
            graph_name: The graph name
            fetch_func: Optional function to fetch from remote: fetch_func(bucket, graph_name) -> (data, etag)
            project_id: Project ID for tracking

        Returns:
            Tuple of (graph_file_path, was_fetched_from_remote)
        """
        with self._lock:
            graph_path = self.get_graph_file_path(bucket, graph_name)

            # Check if we have it cached
            if graph_path.exists():
                metadata = self._load_metadata(bucket, graph_name)
                if metadata:
                    metadata.touch()
                    self._save_metadata(bucket, graph_name, metadata)
                return str(graph_path), False

            # Need to fetch from remote
            if fetch_func is None:
                # No fetch function, check if file exists anyway (manual placement)
                if graph_path.exists():
                    return str(graph_path), False
                return None, False

            try:
                log.info(f"Fetching graph from remote: {bucket}/{graph_name}")
                data, etag = fetch_func(bucket, graph_name)

                if data is None:
                    log.warning(f"Remote returned no data for {bucket}/{graph_name}")
                    return None, False

                # Save to cache
                graph_path.parent.mkdir(parents=True, exist_ok=True)

                with open(graph_path, 'w') as f:
                    if isinstance(data, str):
                        f.write(data)
                    else:
                        json.dump(data, f)

                # Save metadata
                size = graph_path.stat().st_size
                metadata = CacheMetadata(
                    last_synced=datetime.now(timezone.utc).isoformat(),
                    remote_etag=etag,
                    local_size_bytes=size,
                    source_project_id=project_id,
                    source_bucket=bucket,
                )
                self._save_metadata(bucket, graph_name, metadata)

                log.info(f"Cached graph: {bucket}/{graph_name} ({size / 1024 / 1024:.1f} MB)")
                return str(graph_path), True

            except Exception as e:
                log.exception(f"Failed to fetch graph {bucket}/{graph_name}: {e}")
                return None, False

    def save_graph(
        self,
        bucket: str,
        graph_name: str,
        data: Any,
        push_func: Optional[callable] = None,
        project_id: Optional[int] = None,
    ) -> bool:
        """
        Save graph to local cache and optionally push to remote.

        Args:
            bucket: The bucket name
            graph_name: The graph name
            data: Graph data (dict or string)
            push_func: Optional function to push to remote: push_func(bucket, graph_name, data) -> etag
            project_id: Project ID for tracking

        Returns:
            True if saved successfully
        """
        with self._lock:
            try:
                graph_path = self.get_graph_file_path(bucket, graph_name)
                graph_path.parent.mkdir(parents=True, exist_ok=True)

                # Write to local
                with open(graph_path, 'w') as f:
                    if isinstance(data, str):
                        f.write(data)
                    else:
                        json.dump(data, f)

                size = graph_path.stat().st_size
                etag = None

                # Push to remote if function provided
                if push_func is not None:
                    try:
                        log.info(f"Pushing graph to remote: {bucket}/{graph_name}")
                        etag = push_func(bucket, graph_name, data)
                    except Exception as e:
                        log.warning(f"Failed to push graph to remote: {e}")

                # Update metadata
                metadata = CacheMetadata(
                    last_synced=datetime.now(timezone.utc).isoformat() if etag else None,
                    remote_etag=etag,
                    local_size_bytes=size,
                    source_project_id=project_id,
                    source_bucket=bucket,
                )
                self._save_metadata(bucket, graph_name, metadata)

                log.info(f"Saved graph: {bucket}/{graph_name} ({size / 1024 / 1024:.1f} MB)")
                return True

            except Exception as e:
                log.exception(f"Failed to save graph {bucket}/{graph_name}: {e}")
                return False

    def delete_graph(self, bucket: str, graph_name: str) -> bool:
        """Delete a cached graph"""
        with self._lock:
            graph_dir = self.get_graph_path(bucket, graph_name)
            if not graph_dir.exists():
                return True

            try:
                shutil.rmtree(graph_dir)
                log.info(f"Deleted cached graph: {bucket}/{graph_name}")
                return True
            except Exception as e:
                log.warning(f"Failed to delete cached graph {bucket}/{graph_name}: {e}")
                return False

    def list_cached_graphs(self) -> list:
        """List all cached graphs with their metadata"""
        graphs = []

        if not self.base_path.exists():
            return graphs

        for bucket_dir in self.base_path.iterdir():
            if not bucket_dir.is_dir():
                continue

            bucket = bucket_dir.name

            for graph_dir in bucket_dir.iterdir():
                if not graph_dir.is_dir():
                    continue

                graph_name = graph_dir.name
                graph_file = graph_dir / self.GRAPH_FILE

                if not graph_file.exists():
                    continue

                metadata = self._load_metadata(bucket, graph_name)

                graphs.append({
                    "bucket": bucket,
                    "graph_name": graph_name,
                    "path": str(graph_file),
                    "size_bytes": graph_file.stat().st_size if graph_file.exists() else 0,
                    "metadata": metadata.to_dict() if metadata else None,
                })

        return graphs

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        graphs = self.list_cached_graphs()
        total_size = sum(g["size_bytes"] for g in graphs)

        return {
            "total_graphs": len(graphs),
            "total_size_bytes": total_size,
            "total_size_mb": total_size / 1024 / 1024,
            "max_size_bytes": self.max_cache_size_bytes,
            "max_size_mb": self.max_cache_size_bytes / 1024 / 1024,
            "max_graphs": self.max_graphs,
            "max_age_days": self.max_age_seconds / 86400,
            "usage_percent": (total_size / self.max_cache_size_bytes * 100) if self.max_cache_size_bytes > 0 else 0,
        }

    def cleanup_stale_graphs(self) -> Dict[str, Any]:
        """
        Clean up stale graphs based on:
        1. Age (not accessed in max_age_days)
        2. Total size limit
        3. Max number of graphs

        Returns cleanup statistics.
        """
        with self._lock:
            graphs = self.list_cached_graphs()

            if not graphs:
                return {"removed": 0, "freed_bytes": 0}

            # Sort by last accessed (oldest first)
            def get_age(g):
                if g["metadata"] and g["metadata"].get("last_accessed"):
                    return g["metadata"]["last_accessed"]
                return "1970-01-01T00:00:00Z"  # Very old

            graphs.sort(key=get_age)

            removed = []
            freed_bytes = 0

            # Remove graphs older than max age
            for graph in graphs:
                metadata = graph.get("metadata")
                if metadata:
                    meta = CacheMetadata.from_dict(metadata)
                    if meta.get_age_seconds() > self.max_age_seconds:
                        if self.delete_graph(graph["bucket"], graph["graph_name"]):
                            removed.append(f"{graph['bucket']}/{graph['graph_name']}")
                            freed_bytes += graph["size_bytes"]

            # Recalculate after age-based cleanup
            remaining_graphs = [g for g in graphs if f"{g['bucket']}/{g['graph_name']}" not in removed]

            # Remove oldest graphs if over count limit
            while len(remaining_graphs) > self.max_graphs:
                oldest = remaining_graphs[0]
                if self.delete_graph(oldest["bucket"], oldest["graph_name"]):
                    removed.append(f"{oldest['bucket']}/{oldest['graph_name']}")
                    freed_bytes += oldest["size_bytes"]
                remaining_graphs = remaining_graphs[1:]

            # Remove oldest graphs if over size limit
            total_size = sum(g["size_bytes"] for g in remaining_graphs)
            while total_size > self.max_cache_size_bytes and remaining_graphs:
                oldest = remaining_graphs[0]
                if self.delete_graph(oldest["bucket"], oldest["graph_name"]):
                    removed.append(f"{oldest['bucket']}/{oldest['graph_name']}")
                    freed_bytes += oldest["size_bytes"]
                    total_size -= oldest["size_bytes"]
                remaining_graphs = remaining_graphs[1:]

            if removed:
                log.info(
                    f"Cache cleanup: removed {len(removed)} graphs, "
                    f"freed {freed_bytes / 1024 / 1024:.1f} MB"
                )

            return {
                "removed": len(removed),
                "removed_graphs": removed,
                "freed_bytes": freed_bytes,
                "freed_mb": freed_bytes / 1024 / 1024,
            }
