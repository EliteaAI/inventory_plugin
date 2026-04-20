"""
Pytest configuration — plugin imports, embedding model management.

The EmbeddingRouter requires a sentence-transformers model (all-MiniLM-L6-v2).
In CI the model may not be cached.  This conftest:

1. Downloads the model to a temporary directory on first use.
2. Sets ``SENTENCE_TRANSFORMERS_HOME`` so EmbeddingRouter finds it.
3. Registers a ``requires_embeddings`` marker to skip tests when the
   model cannot be loaded (e.g. offline / constrained CI runners).
"""

import logging
import os
import shutil
import sys
import tempfile

import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plugin import path
# ---------------------------------------------------------------------------

# Add pylon_inventory/ to sys.path so `plugins.inventory_plugin.*` resolves
_pylon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _pylon_root not in sys.path:
    sys.path.insert(0, _pylon_root)


# ---------------------------------------------------------------------------
# Embedding model availability
# ---------------------------------------------------------------------------

_EMBEDDING_MODEL_AVAILABLE: bool | None = None   # None = not probed yet
_TEMP_MODEL_DIR: str | None = None


def _probe_embedding_model() -> bool:
    """Try to load the embedding model.  Returns True on success."""
    global _EMBEDDING_MODEL_AVAILABLE, _TEMP_MODEL_DIR

    if _EMBEDDING_MODEL_AVAILABLE is not None:
        return _EMBEDDING_MODEL_AVAILABLE

    try:
        from sentence_transformers import SentenceTransformer

        # Re-use existing cache when the env-var is already set (local dev)
        cache_dir = os.environ.get("SENTENCE_TRANSFORMERS_HOME")
        if not cache_dir:
            _TEMP_MODEL_DIR = tempfile.mkdtemp(prefix="st_cache_")
            cache_dir = _TEMP_MODEL_DIR
            os.environ["SENTENCE_TRANSFORMERS_HOME"] = cache_dir

        # ~80 MB download on first run, then cached
        SentenceTransformer("all-MiniLM-L6-v2", cache_folder=cache_dir)
        _EMBEDDING_MODEL_AVAILABLE = True
        logger.info("[conftest] Embedding model available (cache=%s)", cache_dir)
    except Exception as exc:
        _EMBEDDING_MODEL_AVAILABLE = False
        logger.warning("[conftest] Embedding model unavailable: %s", exc)

    return _EMBEDDING_MODEL_AVAILABLE


# ---------------------------------------------------------------------------
# Pytest hooks & markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_embeddings: skip when the sentence-transformers model is unavailable",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked ``requires_embeddings`` when the model is missing."""
    if _probe_embedding_model():
        return  # model loaded — nothing to skip

    skip_marker = pytest.mark.skip(
        reason="sentence-transformers model (all-MiniLM-L6-v2) not available"
    )
    for item in items:
        if "requires_embeddings" in item.keywords:
            item.add_marker(skip_marker)


def pytest_sessionfinish(session, exitstatus):
    """Clean up temporary model directory if we created one."""
    global _TEMP_MODEL_DIR
    if _TEMP_MODEL_DIR and os.path.isdir(_TEMP_MODEL_DIR):
        shutil.rmtree(_TEMP_MODEL_DIR, ignore_errors=True)
        _TEMP_MODEL_DIR = None
