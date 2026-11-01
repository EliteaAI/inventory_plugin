#!/usr/bin/python3
# coding=utf-8

"""Utils module"""

from .source_status import SourceStatusManager, SourceStatus

try:
    from .cache_manager import GraphCacheManager
except ModuleNotFoundError as exc:
    if exc.name != "pylon":
        raise
    GraphCacheManager = None

try:
    from .ingestion_tracker import IngestionTracker, IngestionSlotError
except ModuleNotFoundError as exc:
    if exc.name != "pylon":
        raise
    IngestionTracker = None
    IngestionSlotError = None

from .artifact_bucket import (
    INVENTORY_ARTIFACT_BUCKET,
    LEGACY_INVENTORY_ARTIFACT_BUCKETS,
    get_inventory_artifact_read_candidates,
    resolve_inventory_artifact_bucket,
)

__all__ = [
    'GraphCacheManager',
    'IngestionTracker',
    'IngestionSlotError',
    'INVENTORY_ARTIFACT_BUCKET',
    'LEGACY_INVENTORY_ARTIFACT_BUCKETS',
    'get_inventory_artifact_read_candidates',
    'resolve_inventory_artifact_bucket',
    'SourceStatusManager',
    'SourceStatus',
]
