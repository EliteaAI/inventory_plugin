"""
Tests for entity-level embeddings and semantic search.

Covers:
- generate_embeddings() on KnowledgeGraph
- semantic_search() on KnowledgeGraph
- Filters (entity_type, layer, file_pattern, min_score)
- Serialization roundtrip (dump_to_json / load_from_json with embeddings)
- Graceful degradation (no embeddings, no model)
- regenerate_embeddings() on IngestionPipeline
- Chat tool integration (_build_chat_tools conditional)
- Edge cases (empty graph, entities without descriptions, mixed embeddings)
"""
import json
import os
import tempfile
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# Insert plugin root into path so imports resolve
import sys
_plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

from inventory.knowledge_graph import KnowledgeGraph, Citation


# ---------------------------------------------------------------------------
# Mock Embedding Model — deterministic, no GPU / sentence-transformers needed
# ---------------------------------------------------------------------------

class MockEmbeddingModel:
    """
    Deterministic mock that satisfies the LangChain Embeddings interface.

    Produces 8-dim vectors that capture a tiny amount of semantic signal
    by hashing the input text into vector components.  This is enough
    for the tests to verify ordering / filtering without needing a real model.
    """
    model_name = "mock-embeddings"
    DIMS = 8

    @staticmethod
    def _text_to_vector(text: str) -> List[float]:
        """Deterministic text -> vector using char-level hashing."""
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        vec = [float(b) / 255.0 for b in h[:MockEmbeddingModel.DIMS]]
        # normalise
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._text_to_vector(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._text_to_vector(text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_graph_with_entities() -> KnowledgeGraph:
    """
    Build a small KnowledgeGraph with diverse entities for testing.
    """
    kg = KnowledgeGraph()

    # Code-layer entities
    kg.add_entity(
        entity_id="auth_service",
        name="AuthenticationService",
        entity_type="class",
        citation=Citation(file_path="src/auth/service.py", line_start=10, line_end=120),
        properties={
            "description": "Handles user authentication and token generation",
            "purpose": "Main authentication entry point",
        },
    )
    kg.add_entity(
        entity_id="payment_processor",
        name="PaymentProcessor",
        entity_type="class",
        citation=Citation(file_path="src/payments/processor.py", line_start=5, line_end=200),
        properties={
            "description": "Processes credit card and wallet payments",
            "summary": "Integrates with Stripe and PayPal gateways",
        },
    )
    kg.add_entity(
        entity_id="log_parser",
        name="LogParser",
        entity_type="function",
        citation=Citation(file_path="src/utils/log_parser.py", line_start=1, line_end=45),
        properties={
            "description": "Parses structured log entries from application output",
        },
    )

    # Data-layer entity
    kg.add_entity(
        entity_id="user_model",
        name="UserModel",
        entity_type="model",
        citation=Citation(file_path="src/models/user.py", line_start=1, line_end=30),
        properties={
            "description": "Database model for user accounts",
        },
    )

    # Service-layer entity
    kg.add_entity(
        entity_id="login_endpoint",
        name="POST /api/login",
        entity_type="api_endpoint",
        citation=Citation(file_path="src/routes/auth.py", line_start=15, line_end=40),
        properties={
            "description": "Login endpoint accepting username and password",
        },
    )

    # Entity with minimal data (name only → still embeddable)
    kg.add_entity(
        entity_id="empty_desc",
        name="UtilityHelper",
        entity_type="function",
        citation=Citation(file_path="src/utils/helpers.py"),
    )

    return kg


@pytest.fixture
def kg():
    return _make_graph_with_entities()


@pytest.fixture
def mock_model():
    return MockEmbeddingModel()


@pytest.fixture
def embedded_kg(kg, mock_model):
    """KnowledgeGraph with embeddings already generated."""
    kg.generate_embeddings(mock_model)
    return kg


@pytest.fixture
def tmp_json(tmp_path):
    """Path to a temporary JSON file for serialisation tests."""
    return str(tmp_path / "test_graph.json")


# =========================================================================
# 1. generate_embeddings()
# =========================================================================

class TestGenerateEmbeddings:
    """Tests for KnowledgeGraph.generate_embeddings()."""

    def test_generates_embeddings_for_all_nodes(self, kg, mock_model):
        count = kg.generate_embeddings(mock_model)
        assert count == 6  # all 6 entities

        stats = kg.get_stats()
        assert stats['has_embeddings'] is True
        assert stats['embeddings_count'] == 6
        assert 'MockEmbeddingModel' in stats['embeddings_model']

    def test_embedding_is_list_of_floats(self, kg, mock_model):
        kg.generate_embeddings(mock_model)
        for _, data in kg._graph.nodes(data=True):
            emb = data.get('embedding')
            assert isinstance(emb, list)
            assert len(emb) == MockEmbeddingModel.DIMS
            assert all(isinstance(v, float) for v in emb)

    def test_skip_already_embedded_nodes(self, kg, mock_model):
        # First run embeds all
        count1 = kg.generate_embeddings(mock_model)
        assert count1 == 6
        # Second run skips (no force)
        count2 = kg.generate_embeddings(mock_model)
        assert count2 == 0

    def test_force_regenerates_all(self, kg, mock_model):
        kg.generate_embeddings(mock_model)
        count = kg.generate_embeddings(mock_model, force=True)
        assert count == 6

    def test_stores_embedding_model_reference(self, kg, mock_model):
        kg.generate_embeddings(mock_model)
        assert kg._embedding_model is mock_model

    def test_metadata_tracks_model_info(self, kg, mock_model):
        kg.generate_embeddings(mock_model)
        assert 'embeddings_model' in kg._metadata
        assert 'mock-embeddings' in kg._metadata['embeddings_model']
        assert 'embeddings_generated_at' in kg._metadata
        assert kg._metadata['embeddings_count'] == 6

    def test_batch_processing(self, kg, mock_model):
        """Verify batching works by using batch_size=2."""
        count = kg.generate_embeddings(mock_model, batch_size=2)
        assert count == 6
        # All nodes should still have embeddings
        assert kg.get_stats()['embeddings_count'] == 6


# =========================================================================
# 2. semantic_search()
# =========================================================================

class TestSemanticSearch:
    """Tests for KnowledgeGraph.semantic_search()."""

    def test_returns_results_sorted_by_score(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search("authentication", embedding_model=mock_model)
        assert len(results) > 0
        scores = [r['score'] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_result_structure(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search("payment", embedding_model=mock_model, top_k=1)
        assert len(results) >= 1
        r = results[0]
        assert 'entity' in r
        assert 'score' in r
        assert r['match_field'] == 'semantic'
        assert isinstance(r['score'], float)
        # Entity should have id, name, type
        assert 'id' in r['entity']
        assert 'name' in r['entity']
        assert 'type' in r['entity']

    def test_embedding_excluded_from_results(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search("authentication", embedding_model=mock_model)
        for r in results:
            assert 'embedding' not in r['entity']

    def test_top_k_limits_results(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search("service", embedding_model=mock_model, top_k=2)
        assert len(results) <= 2

    def test_uses_stored_model_if_none_provided(self, embedded_kg):
        # No embedding_model arg → should use self._embedding_model
        results = embedded_kg.semantic_search("user")
        assert len(results) > 0

    def test_raises_without_model(self, kg):
        """No embeddings generated and no model passed → ValueError."""
        with pytest.raises(ValueError, match="No embedding model"):
            kg.semantic_search("anything")

    def test_identical_query_returns_exact_match_first(self, embedded_kg, mock_model):
        """Searching for exact entity text should rank that entity highest."""
        # The composed text for auth_service includes "AuthenticationService"
        results = embedded_kg.semantic_search(
            "AuthenticationService class Handles user authentication and token generation",
            embedding_model=mock_model,
        )
        assert results[0]['entity']['name'] == 'AuthenticationService'


# =========================================================================
# 3. Filters & min_score
# =========================================================================

class TestSemanticSearchFilters:
    """Tests for entity_type, layer, file_pattern, and min_score filters."""

    def test_filter_by_entity_type(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search(
            "service", embedding_model=mock_model, entity_type="class"
        )
        for r in results:
            assert r['entity']['type'].lower() == 'class'

    def test_filter_by_layer(self, embedded_kg, mock_model):
        # "data" layer has model types
        results = embedded_kg.semantic_search(
            "database user", embedding_model=mock_model, layer="data"
        )
        for r in results:
            assert r['entity']['type'].lower() in KnowledgeGraph.LAYER_TYPE_MAPPING['data']

    def test_filter_by_file_pattern(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search(
            "payment", embedding_model=mock_model, file_pattern="src/payments/*"
        )
        for r in results:
            citations = r['entity'].get('citations', [])
            file_paths = [c.get('file_path', '') for c in citations if isinstance(c, dict)]
            assert any('src/payments/' in fp for fp in file_paths)

    def test_min_score_filters_low_quality(self, embedded_kg, mock_model):
        # With an absurdly high threshold, nothing should match
        results = embedded_kg.semantic_search(
            "something", embedding_model=mock_model, min_score=0.9999
        )
        assert len(results) == 0

    def test_min_score_zero_returns_all(self, embedded_kg, mock_model):
        results = embedded_kg.semantic_search(
            "something", embedding_model=mock_model, min_score=0.0
        )
        # Should return up to top_k results with any positive similarity
        assert len(results) > 0


# =========================================================================
# 4. Serialization roundtrip
# =========================================================================

class TestSerializationRoundtrip:
    """dump_to_json / load_from_json should preserve embeddings."""

    def test_embeddings_survive_roundtrip(self, embedded_kg, tmp_json, mock_model):
        embedded_kg.dump_to_json(tmp_json)

        loaded = KnowledgeGraph()
        loaded.load_from_json(tmp_json)

        stats = loaded.get_stats()
        assert stats['has_embeddings'] is True
        assert stats['embeddings_count'] == 6

        # Verify actual vectors
        for node_id, data in loaded._graph.nodes(data=True):
            emb = data.get('embedding')
            assert emb is not None
            assert len(emb) == MockEmbeddingModel.DIMS

    def test_semantic_search_works_after_reload(self, embedded_kg, tmp_json, mock_model):
        embedded_kg.dump_to_json(tmp_json)

        loaded = KnowledgeGraph()
        loaded.load_from_json(tmp_json)

        results = loaded.semantic_search("authentication", embedding_model=mock_model)
        assert len(results) > 0

    def test_exclude_embeddings_strips_vectors(self, embedded_kg, tmp_json):
        embedded_kg.dump_to_json(tmp_json, exclude_embeddings=True)

        with open(tmp_json) as f:
            data = json.load(f)

        for node in data.get('nodes', []):
            assert 'embedding' not in node

    def test_exclude_embeddings_results_in_no_embeddings_on_reload(self, embedded_kg, tmp_json):
        embedded_kg.dump_to_json(tmp_json, exclude_embeddings=True)

        loaded = KnowledgeGraph()
        loaded.load_from_json(tmp_json)

        stats = loaded.get_stats()
        assert stats['has_embeddings'] is False
        assert stats['embeddings_count'] == 0


# =========================================================================
# 5. Graceful degradation
# =========================================================================

class TestGracefulDegradation:
    """Semantic search / embedding generation should degrade gracefully."""

    def test_search_on_graph_without_embeddings_raises(self, kg, mock_model):
        """If model is passed but no nodes have embeddings, returns empty list."""
        results = kg.semantic_search("anything", embedding_model=mock_model)
        assert results == []

    def test_search_without_model_raises_value_error(self, kg):
        with pytest.raises(ValueError, match="No embedding model"):
            kg.semantic_search("anything")

    def test_generate_on_empty_graph(self, mock_model):
        kg = KnowledgeGraph()
        count = kg.generate_embeddings(mock_model)
        assert count == 0

    def test_retrieval_wrapper_returns_message_no_embeddings(self, kg, tmp_json):
        """InventoryRetrievalApiWrapper.semantic_search returns helpful message."""
        kg.dump_to_json(tmp_json)

        from inventory.retrieval import InventoryRetrievalApiWrapper
        wrapper = InventoryRetrievalApiWrapper.model_construct(
            graph_path=tmp_json,
        )
        wrapper._knowledge_graph = kg
        wrapper._embedding = None

        result = wrapper.semantic_search(query="test")
        assert "unavailable" in result.lower()


# =========================================================================
# 6. regenerate_embeddings (IngestionPipeline)
# =========================================================================

class TestRegenerateEmbeddings:
    """Tests for IngestionPipeline.regenerate_embeddings()."""

    def test_regenerate_adds_embeddings(self, kg, mock_model, tmp_json):
        kg.dump_to_json(tmp_json)

        from inventory.ingestion import IngestionPipeline

        pipeline = IngestionPipeline.model_construct(
            graph_path=tmp_json,
        )
        pipeline._knowledge_graph = kg
        pipeline._embedding = mock_model

        count = pipeline.regenerate_embeddings()
        assert count == 6
        assert kg.get_stats()['has_embeddings'] is True

    def test_regenerate_with_explicit_model(self, kg, tmp_json):
        kg.dump_to_json(tmp_json)

        from inventory.ingestion import IngestionPipeline

        pipeline = IngestionPipeline.model_construct(
            graph_path=tmp_json,
        )
        pipeline._knowledge_graph = kg
        pipeline._embedding = None  # no default model

        model = MockEmbeddingModel()
        count = pipeline.regenerate_embeddings(embedding_model=model)
        assert count == 6

    def test_regenerate_force_overwrites(self, kg, mock_model, tmp_json):
        kg.dump_to_json(tmp_json)
        kg.generate_embeddings(mock_model)  # first pass

        from inventory.ingestion import IngestionPipeline

        pipeline = IngestionPipeline.model_construct(
            graph_path=tmp_json,
        )
        pipeline._knowledge_graph = kg
        pipeline._embedding = mock_model

        count = pipeline.regenerate_embeddings(force=True)
        assert count == 6

    def test_regenerate_raises_without_model(self, kg, tmp_json):
        from inventory.ingestion import IngestionPipeline

        pipeline = IngestionPipeline.model_construct(
            graph_path=tmp_json,
        )
        pipeline._knowledge_graph = kg
        pipeline._embedding = None

        with pytest.raises(ValueError, match="No embedding model"):
            pipeline.regenerate_embeddings()


# =========================================================================
# 7. Chat tool integration
# =========================================================================

class TestChatToolIntegration:
    """Verify semantic_search tool conditionally appears in chat tools."""

    def test_tool_present_when_embeddings_exist(self, embedded_kg, tmp_json, mock_model):
        """_build_chat_tools should include semantic_search when graph has embeddings."""
        stats = embedded_kg.get_stats()
        assert stats['has_embeddings'] is True

        # We cant easily call _build_chat_tools (it needs full chat setup),
        # so we test the condition it checks:
        assert stats.get('has_embeddings') is True
        assert stats.get('embeddings_count', 0) > 0

    def test_tool_absent_when_no_embeddings(self, kg):
        stats = kg.get_stats()
        assert stats.get('has_embeddings') is False


# =========================================================================
# 8. Edge cases
# =========================================================================

class TestEdgeCases:
    """Edge cases for embedding operations."""

    def test_empty_graph_semantic_search(self, mock_model):
        """Search on empty graph returns empty list."""
        kg = KnowledgeGraph()
        kg.generate_embeddings(mock_model)
        results = kg.semantic_search("anything", embedding_model=mock_model)
        assert results == []

    def test_entity_with_no_description_still_embeds(self, mock_model):
        """Entity with only name+type should still get an embedding."""
        kg = KnowledgeGraph()
        kg.add_entity(
            entity_id="bare_entity",
            name="BareFunction",
            entity_type="function",
        )
        count = kg.generate_embeddings(mock_model)
        assert count == 1
        emb = kg._graph.nodes["bare_entity"].get('embedding')
        assert emb is not None
        assert len(emb) == MockEmbeddingModel.DIMS

    def test_mixed_embeddings_search_skips_unembedded(self, mock_model):
        """When only some nodes have embeddings, search uses only those."""
        kg = KnowledgeGraph()
        kg.add_entity("a", "AlphaService", "class",
                       properties={"description": "Alpha"})
        kg.add_entity("b", "BetaService", "class",
                       properties={"description": "Beta"})

        # Embed only node 'a'
        kg.generate_embeddings(mock_model)

        # Manually remove embedding from 'b' to simulate mixed state
        kg._graph.nodes['b'].pop('embedding', None)

        results = kg.semantic_search("alpha", embedding_model=mock_model)
        # Only 'a' should appear (b has no embedding)
        entity_ids = [r['entity']['id'] for r in results]
        assert 'a' in entity_ids
        assert 'b' not in entity_ids

    def test_compose_embedding_text_full(self):
        """_compose_embedding_text includes name, type, description, properties."""
        text = KnowledgeGraph._compose_embedding_text({
            'name': 'PaymentService',
            'type': 'class',
            'description': 'Processes payments',
            'properties': {
                'purpose': 'Handle billing',
                'signature': 'class PaymentService(Base):',
            },
        })
        assert 'PaymentService' in text
        assert 'class' in text
        assert 'Processes payments' in text
        assert 'Handle billing' in text
        assert 'class PaymentService(Base):' in text

    def test_compose_embedding_text_empty(self):
        """Empty node data → empty text."""
        text = KnowledgeGraph._compose_embedding_text({})
        assert text == ''

    def test_compose_embedding_text_description_from_properties(self):
        """If top-level description is empty, uses properties.description."""
        text = KnowledgeGraph._compose_embedding_text({
            'name': 'Foo',
            'properties': {'description': 'A foo thing'},
        })
        assert 'A foo thing' in text

    def test_get_stats_embedding_fields_no_embeddings(self):
        kg = KnowledgeGraph()
        kg.add_entity("x", "X", "class")
        stats = kg.get_stats()
        assert stats['has_embeddings'] is False
        assert stats['embeddings_count'] == 0
        assert stats['embeddings_model'] is None

    def test_get_stats_embedding_fields_with_embeddings(self, embedded_kg):
        stats = embedded_kg.get_stats()
        assert stats['has_embeddings'] is True
        assert stats['embeddings_count'] == 6
        assert stats['embeddings_model'] is not None
