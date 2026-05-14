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

__all__ = [
    'GraphCacheManager',
    'IngestionTracker',
    'IngestionSlotError',
    'SourceStatusManager',
    'SourceStatus',
]
