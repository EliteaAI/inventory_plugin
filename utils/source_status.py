#!/usr/bin/python3
# coding=utf-8

"""
Source Status Manager

Tracks the status and last update time for each source toolkit added to a knowledge graph.
Status is stored in sources_status.json alongside the graph.json file.

This enables:
- UI to show status badges on source cards (pending, in_progress, completed, error)
- Tracking when each source was last updated
- Showing entity/relation counts per source
"""

import json
import os
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

try:
    from pylon.core.tools import log
except ModuleNotFoundError:
    import logging

    log = logging.getLogger(__name__)


class SourceStatus:
    """Status constants for sources"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"


class SourceStatusManager:
    """
    Manages source status tracking for a knowledge graph.

    Stores status in sources_status.json in the graph directory:
    {
        "sources": {
            "<toolkit_id_or_name>": {
                "toolkit_id": "123",
                "toolkit_name": "github_repo",
                "toolkit_type": "github",
                "status": "completed",
                "last_updated": "2025-12-22T10:30:00Z",
                "started_at": "2025-12-22T10:25:00Z",
                "entities_count": 150,
                "relations_count": 200,
                "documents_processed": 45,
                "error_message": null,
                "branch": "main"
            }
        },
        "last_modified": "2025-12-22T10:30:00Z"
    }
    """

    STATUS_FILE = "sources_status.json"

    def __init__(self, graph_dir: str):
        """
        Initialize status manager for a graph directory.

        Args:
            graph_dir: Directory containing the graph.json file
        """
        self.graph_dir = Path(graph_dir)
        self._lock = threading.RLock()

        # Ensure directory exists
        self.graph_dir.mkdir(parents=True, exist_ok=True)

    @property
    def status_file_path(self) -> Path:
        return self.graph_dir / self.STATUS_FILE

    def _read_status(self) -> Dict[str, Any]:
        """Read current status from file"""
        try:
            if self.status_file_path.exists():
                with open(self.status_file_path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Failed to read status file: {e}")
        return {"sources": {}, "last_modified": None}

    def _write_status(self, status: Dict[str, Any]) -> None:
        """Write status to file atomically"""
        status["last_modified"] = datetime.now(timezone.utc).isoformat()

        # Write to temp file first, then rename for atomicity
        temp_path = self.status_file_path.with_suffix('.tmp')
        try:
            with open(temp_path, 'w') as f:
                json.dump(status, f, indent=2, default=str)
            temp_path.rename(self.status_file_path)
        except IOError as e:
            log.error(f"Failed to write status file: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def get_all_sources_status(self) -> Dict[str, Any]:
        """Get status for all sources"""
        with self._lock:
            return self._read_status()

    def get_sources(self) -> Dict[str, Dict[str, Any]]:
        """
        Get sources dict keyed by toolkit_id.

        Returns:
            Dict mapping toolkit_id -> source status dict
        """
        with self._lock:
            status = self._read_status()
            return status.get("sources", {})

    def get_source_status(self, source_key: str) -> Optional[Dict[str, Any]]:
        """
        Get status for a specific source.

        Args:
            source_key: Toolkit ID or name used as key

        Returns:
            Source status dict or None if not found
        """
        with self._lock:
            status = self._read_status()
            return status.get("sources", {}).get(str(source_key))

    def start_ingestion(
        self,
        toolkit_id: str,
        toolkit_name: str,
        toolkit_type: str = "",
        branch: Optional[str] = None,
    ) -> None:
        """
        Mark a source as starting ingestion.

        Args:
            toolkit_id: Unique toolkit ID
            toolkit_name: Human-readable toolkit name
            toolkit_type: Type of toolkit (github, ado, gitlab, etc.)
            branch: Git branch being ingested
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})

            # Use toolkit_id as key for consistent lookups
            source_key = str(toolkit_id)

            # Preserve previous counts if re-running
            previous = sources.get(source_key, {})

            sources[source_key] = {
                "toolkit_id": str(toolkit_id),
                "toolkit_name": toolkit_name,
                "toolkit_type": toolkit_type,
                "status": SourceStatus.IN_PROGRESS,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "entities_count": previous.get("entities_count", 0),
                "relations_count": previous.get("relations_count", 0),
                "documents_processed": 0,
                "error_message": None,
                "progress_message": "Starting ingestion...",
                "branch": branch,
            }

            status["sources"] = sources
            self._write_status(status)
            log.info(f"Source ingestion started: {toolkit_name} (ID: {toolkit_id})")

    def update_progress(
        self,
        toolkit_id: str,
        progress_message: str,
        documents_processed: Optional[int] = None,
        entities_count: Optional[int] = None,
    ) -> None:
        """
        Update progress message for an in-progress ingestion.

        This is called frequently during ingestion to show real-time progress
        in the UI. Only updates if source status is IN_PROGRESS.

        Args:
            toolkit_id: Toolkit ID
            progress_message: Human-readable progress message (e.g., "📄 Processed 10 files | 📊 370 entities")
            documents_processed: Optional update to documents processed count
            entities_count: Optional update to entities count
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})
            source_key = str(toolkit_id)

            if source_key not in sources:
                return  # Source not found

            # Only update if status is IN_PROGRESS
            if sources[source_key].get("status") != SourceStatus.IN_PROGRESS:
                return

            sources[source_key]["progress_message"] = progress_message
            sources[source_key]["last_updated"] = datetime.now(timezone.utc).isoformat()

            if documents_processed is not None:
                sources[source_key]["documents_processed"] = documents_processed
            if entities_count is not None:
                sources[source_key]["entities_count"] = entities_count

            status["sources"] = sources
            self._write_status(status)

    def complete_ingestion(
        self,
        toolkit_id: str,
        entities_count: int = 0,
        relations_count: int = 0,
        documents_processed: int = 0,
    ) -> None:
        """
        Mark a source ingestion as completed.

        Args:
            toolkit_id: Toolkit ID
            entities_count: Number of entities from this source
            relations_count: Number of relations from this source
            documents_processed: Number of documents processed
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})
            source_key = str(toolkit_id)

            if source_key not in sources:
                sources[source_key] = {
                    "toolkit_id": str(toolkit_id),
                    "toolkit_name": f"toolkit_{toolkit_id}",
                    "toolkit_type": "",
                }

            sources[source_key].update({
                "status": SourceStatus.COMPLETED,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "entities_count": entities_count,
                "relations_count": relations_count,
                "documents_processed": documents_processed,
                "error_message": None,
                "progress_message": None,  # Clear progress message on completion
            })

            status["sources"] = sources
            self._write_status(status)
            log.info(f"Source ingestion completed: toolkit_id={toolkit_id}, "
                    f"entities={entities_count}, relations={relations_count}")

    def fail_ingestion(
        self,
        toolkit_id: str,
        error_message: str,
        documents_processed: int = 0,
    ) -> None:
        """
        Mark a source ingestion as failed.

        Args:
            toolkit_id: Toolkit ID
            error_message: Error description
            documents_processed: Number of documents processed before failure
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})
            source_key = str(toolkit_id)

            if source_key not in sources:
                sources[source_key] = {
                    "toolkit_id": str(toolkit_id),
                    "toolkit_name": f"toolkit_{toolkit_id}",
                    "toolkit_type": "",
                }

            sources[source_key].update({
                "status": SourceStatus.ERROR,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "documents_processed": documents_processed,
                "error_message": error_message,
                "progress_message": None,  # Clear progress message on error
            })

            status["sources"] = sources
            self._write_status(status)
            log.warning(f"Source ingestion failed: toolkit_id={toolkit_id}, error={error_message}")

    def remove_source(self, toolkit_id: str) -> bool:
        """
        Remove a source from status tracking.

        Args:
            toolkit_id: Toolkit ID to remove

        Returns:
            True if source was removed, False if not found
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})
            source_key = str(toolkit_id)

            if source_key in sources:
                del sources[source_key]
                status["sources"] = sources
                self._write_status(status)
                log.info(f"Source removed from status: toolkit_id={toolkit_id}")
                return True
            return False

    def get_status_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all source statuses.

        Returns:
            Summary with counts by status and list of sources
        """
        with self._lock:
            status = self._read_status()
            sources = status.get("sources", {})

            # Count by status
            status_counts = {
                SourceStatus.PENDING: 0,
                SourceStatus.IN_PROGRESS: 0,
                SourceStatus.COMPLETED: 0,
                SourceStatus.ERROR: 0,
            }

            total_entities = 0
            total_relations = 0

            for source in sources.values():
                src_status = source.get("status", SourceStatus.PENDING)
                if src_status in status_counts:
                    status_counts[src_status] += 1
                total_entities += source.get("entities_count", 0)
                total_relations += source.get("relations_count", 0)

            return {
                "total_sources": len(sources),
                "status_counts": status_counts,
                "total_entities": total_entities,
                "total_relations": total_relations,
                "sources": list(sources.values()),
                "last_modified": status.get("last_modified"),
            }

    def to_json(self) -> str:
        """Export status as JSON string"""
        with self._lock:
            return json.dumps(self._read_status(), indent=2, default=str)

    def load_from_json(self, json_data: str) -> None:
        """Load status from JSON string (for downloading from artifacts)"""
        with self._lock:
            try:
                status = json.loads(json_data)
                self._write_status(status)
            except json.JSONDecodeError as e:
                log.error(f"Failed to parse status JSON: {e}")
                raise
