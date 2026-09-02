#!/usr/bin/python3
# coding=utf-8

"""Artifact bucket helpers for Inventory."""

from __future__ import annotations

from typing import Any, List

INVENTORY_ARTIFACT_BUCKET = "inventory-graphs"
LEGACY_INVENTORY_ARTIFACT_BUCKETS = (
    "inventory_graphs",
    "graphs",
)


def _clean_bucket_name(value: Any) -> str:
    """Return a normalized bucket name or an empty string."""
    if value is None:
        return ""
    return str(value).strip()


def resolve_inventory_artifact_bucket(*bucket_hints: Any, allow_custom: bool = False) -> str:
    """Return the canonical inventory artifact bucket.

    Inventory now writes to a single hardcoded artifact bucket. Legacy bucket names
    still normalize to the canonical value. Unknown custom values are ignored unless
    allow_custom=True.
    """
    for bucket_hint in bucket_hints:
        bucket_name = _clean_bucket_name(bucket_hint)
        if not bucket_name:
            continue
        if bucket_name == INVENTORY_ARTIFACT_BUCKET:
            return INVENTORY_ARTIFACT_BUCKET
        if bucket_name in LEGACY_INVENTORY_ARTIFACT_BUCKETS:
            return INVENTORY_ARTIFACT_BUCKET
        if allow_custom:
            return bucket_name
    return INVENTORY_ARTIFACT_BUCKET


def get_inventory_artifact_read_candidates(*bucket_hints: Any) -> List[str]:
    """Return artifact buckets to probe when downloading legacy data."""
    candidates: List[str] = [INVENTORY_ARTIFACT_BUCKET]

    for bucket_hint in bucket_hints:
        bucket_name = _clean_bucket_name(bucket_hint)
        if bucket_name and bucket_name not in candidates:
            candidates.append(bucket_name)

    for legacy_bucket in LEGACY_INVENTORY_ARTIFACT_BUCKETS:
        if legacy_bucket not in candidates:
            candidates.append(legacy_bucket)

    return candidates
