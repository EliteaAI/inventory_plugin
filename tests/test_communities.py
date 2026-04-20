"""
Tests for community detection and analysis.

Covers:
- NetworkX to igraph conversion with edge weights
- Leiden macro community detection
- Centroid identification (PageRank + degree + betweenness)
- Auto-labeling heuristics
- Community statistics computation
- KnowledgeGraph community persistence roundtrip
- Retrieval tool formatting
- Micro-clustering (Infomap)
"""

import pytest
import sys
import os
import json

plugin_root = os.path.join(os.path.dirname(__file__), '..')
pylon_root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, plugin_root)
sys.path.insert(0, pylon_root)

from inventory.knowledge_graph import KnowledgeGraph, Citation

try:
    import igraph as ig
    HAS_IGRAPH = True
except ImportError:
    HAS_IGRAPH = False

from inventory.communities import (
    CommunityAnalyzer,
    _get_edge_weight,
    _normalize_scores,
    STRUCTURAL_RELATIONS,
    BEHAVIORAL_RELATIONS,
    SEMANTIC_RELATIONS,
    RELATION_TYPE_WEIGHTS,
    DEFAULT_EDGE_WEIGHT,
    MIN_NODES_FOR_DETECTION,
    TYPE_DOMINANCE_THRESHOLD,
    HAS_IGRAPH as MODULE_HAS_IGRAPH,
)


# ========== Fixtures ==========

@pytest.fixture
def kg():
    """Empty KnowledgeGraph."""
    return KnowledgeGraph()


def _build_three_cluster_graph(kg):
    """
    Build a synthetic graph with 3 clear clusters (~30 nodes).

    Cluster A: Authentication (classes/functions, code layer)
    Cluster B: Documentation (docs layer)
    Cluster C: Data Models (data layer)

    Some inter-cluster edges for realism.
    """
    # Cluster A: Auth (10 nodes)
    auth_nodes = []
    for i, (name, etype) in enumerate([
        ("AuthService", "class"),
        ("authenticate", "function"),
        ("validate_token", "function"),
        ("TokenManager", "class"),
        ("login", "function"),
        ("logout", "function"),
        ("SessionHandler", "class"),
        ("refresh_token", "function"),
        ("hash_password", "function"),
        ("PermissionChecker", "class"),
    ]):
        nid = f"auth_{i}"
        kg.add_entity(nid, name, etype, properties={"layer": "code"})
        auth_nodes.append(nid)

    # Dense internal edges for cluster A
    for u, v, rt in [
        (0, 1, "contains"), (0, 2, "contains"), (0, 4, "contains"),
        (0, 5, "contains"), (3, 2, "calls"), (3, 7, "calls"),
        (1, 2, "calls"), (4, 1, "calls"), (5, 1, "calls"),
        (6, 1, "calls"), (6, 7, "calls"), (9, 2, "calls"),
        (8, 1, "calls"), (7, 3, "calls"),
    ]:
        kg.add_relation(auth_nodes[u], auth_nodes[v], rt, properties={"weight": _get_edge_weight(rt)})

    # Cluster B: Documentation (10 nodes)
    doc_nodes = []
    for i, (name, etype) in enumerate([
        ("API_Guide", "document"),
        ("Installation_Guide", "document"),
        ("Architecture_Overview", "document"),
        ("FAQ", "document"),
        ("Changelog", "document"),
        ("Contributing_Guide", "document"),
        ("README", "document"),
        ("Security_Policy", "document"),
        ("License", "document"),
        ("Glossary", "document"),
    ]):
        nid = f"doc_{i}"
        kg.add_entity(nid, name, etype, properties={"layer": "documentation"})
        doc_nodes.append(nid)

    for u, v, rt in [
        (0, 1, "references"), (0, 2, "references"), (0, 3, "references"),
        (1, 5, "references"), (2, 7, "references"), (3, 9, "references"),
        (4, 0, "references"), (5, 6, "references"), (6, 0, "references"),
        (7, 0, "references"), (8, 5, "references"), (9, 0, "references"),
    ]:
        kg.add_relation(doc_nodes[u], doc_nodes[v], rt, properties={"weight": _get_edge_weight(rt)})

    # Cluster C: Data Models (10 nodes)
    data_nodes = []
    for i, (name, etype) in enumerate([
        ("UserModel", "class"),
        ("SessionModel", "class"),
        ("TokenSchema", "class"),
        ("DatabaseConfig", "configuration"),
        ("MigrationScript", "function"),
        ("UserRepository", "class"),
        ("SessionRepository", "class"),
        ("ConnectionPool", "class"),
        ("QueryBuilder", "class"),
        ("CacheManager", "class"),
    ]):
        nid = f"data_{i}"
        kg.add_entity(nid, name, etype, properties={"layer": "data"})
        data_nodes.append(nid)

    for u, v, rt in [
        (0, 5, "uses"), (1, 6, "uses"), (2, 0, "inherits"),
        (3, 7, "configures"), (4, 5, "calls"), (5, 7, "uses"),
        (6, 7, "uses"), (7, 3, "depends_on"), (8, 7, "uses"),
        (9, 7, "uses"), (0, 1, "references"), (5, 6, "calls"),
    ]:
        kg.add_relation(data_nodes[u], data_nodes[v], rt, properties={"weight": _get_edge_weight(rt)})

    # Inter-cluster edges (sparse)
    kg.add_relation(auth_nodes[0], data_nodes[0], "uses", properties={"weight": 1.0})
    kg.add_relation(auth_nodes[3], data_nodes[2], "uses", properties={"weight": 1.0})
    kg.add_relation(doc_nodes[0], auth_nodes[0], "documents", properties={"weight": 1.0})
    kg.add_relation(doc_nodes[7], auth_nodes[8], "documents", properties={"weight": 1.0})

    return auth_nodes, doc_nodes, data_nodes


@pytest.fixture
def three_cluster_kg(kg):
    """KnowledgeGraph with 3 synthetic clusters."""
    auth, doc, data = _build_three_cluster_graph(kg)
    return kg, auth, doc, data


# ========== Edge Weight Tests ==========

class TestEdgeWeightComputation:

    def test_structural_types(self):
        for rt in ['contains', 'extends', 'implements', 'defines', 'exports',
                    'decorates', 'annotates', 'part_of', 'provides']:
            assert _get_edge_weight(rt) == RELATION_TYPE_WEIGHTS['structural']

    def test_behavioral_types(self):
        for rt in ['calls', 'returns', 'triggers', 'depends_on', 'publishes',
                    'subscribes_to', 'stores_in', 'reads_from', 'maps_to',
                    'transforms', 'shown_on', 'navigates_to', 'validates',
                    'tests', 'covers', 'reproduces', 'blocks']:
            assert _get_edge_weight(rt) == RELATION_TYPE_WEIGHTS['behavioral']

    def test_semantic_types(self):
        for rt in ['uses', 'references', 'imports', 'related_to', 'documents',
                    'duplicates', 'contradicts', 'synonym_of', 'mentions',
                    'owned_by', 'maintained_by', 'assigned_to', 'reviewed_by',
                    'introduced_in', 'modified_in', 'removed_in', 'supersedes']:
            assert _get_edge_weight(rt) == RELATION_TYPE_WEIGHTS['semantic']

    def test_unknown_type_default(self):
        assert _get_edge_weight('some_unknown_relation') == DEFAULT_EDGE_WEIGHT

    def test_case_insensitive(self):
        assert _get_edge_weight('CONTAINS') == RELATION_TYPE_WEIGHTS['structural']
        assert _get_edge_weight('Calls') == RELATION_TYPE_WEIGHTS['behavioral']


# ========== Score Normalization Tests ==========

class TestNormalizeScores:

    def test_basic_normalization(self):
        result = _normalize_scores([1.0, 2.0, 3.0])
        assert result[0] == 0.0
        assert result[2] == 1.0
        assert abs(result[1] - 0.5) < 1e-9

    def test_all_same(self):
        result = _normalize_scores([5.0, 5.0, 5.0])
        # When all same, returns uniform
        assert len(result) == 3
        assert all(abs(v - 1.0 / 3) < 1e-9 for v in result)

    def test_empty(self):
        assert _normalize_scores([]) == []

    def test_single_element(self):
        result = _normalize_scores([42.0])
        assert len(result) == 1


# ========== Community Detection Tests ==========

@pytest.mark.skipif(not HAS_IGRAPH, reason="igraph not installed")
class TestNetworkXToIgraphConversion:

    def test_basic_conversion(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        ig_graph = analyzer._nx_to_igraph(kg._graph)

        assert ig_graph.vcount() == 30
        assert ig_graph.ecount() > 0
        # Should be undirected
        assert not ig_graph.is_directed()

    def test_edge_weights_mapped(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        ig_graph = analyzer._nx_to_igraph(kg._graph)

        weights = ig_graph.es['weight']
        assert all(w > 0 for w in weights)

    def test_nx_name_attribute(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        ig_graph = analyzer._nx_to_igraph(kg._graph)

        # _nx_name should map back to original NetworkX node IDs
        nx_names = ig_graph.vs['_nx_name']
        assert 'auth_0' in nx_names
        assert 'doc_0' in nx_names
        assert 'data_0' in nx_names


@pytest.mark.skipif(not HAS_IGRAPH, reason="igraph not installed")
class TestLeidenMacro:

    def test_detects_communities(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        assert result, "Should produce community data"
        assert result['algorithm'] in ('leiden', 'louvain')
        assert result['modularity'] > 0
        assert result['num_communities'] >= 2  # At least 2 communities

    def test_all_nodes_assigned(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        all_members = set()
        for cinfo in result['communities'].values():
            all_members.update(cinfo['members'])

        all_graph_nodes = set(kg._graph.nodes())
        assert all_members == all_graph_nodes, "Every node should be assigned to a community"

    def test_communities_have_centroids(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        for cid, cinfo in result['communities'].items():
            centroids = cinfo['centroids']
            assert len(centroids) > 0, f"Community {cid} should have centroids"
            for c in centroids:
                assert 'id' in c
                assert 'score' in c
                assert 'name' in c
                assert 'type' in c
                assert 0 <= c['score'] <= 1

    def test_resolution_affects_count(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        low_res = CommunityAnalyzer(resolution=0.3)
        high_res = CommunityAnalyzer(resolution=3.0)

        result_low = low_res.detect_communities(kg._graph)
        result_high = high_res.detect_communities(kg._graph)

        # Higher resolution should produce >= as many communities
        assert result_high['num_communities'] >= result_low['num_communities']

    def test_small_graph_skipped(self, kg):
        """Graph with fewer than MIN_NODES should return empty."""
        for i in range(5):
            kg.add_entity(f"n{i}", f"Node{i}", "class")
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)
        assert result == {}


@pytest.mark.skipif(not HAS_IGRAPH, reason="igraph not installed")
class TestCentroidIdentification:

    def test_centroids_scored(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        for cinfo in result['communities'].values():
            centroids = cinfo['centroids']
            # Should be sorted by score descending
            scores = [c['score'] for c in centroids]
            assert scores == sorted(scores, reverse=True)

    def test_centroid_top_k_limit(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        for cinfo in result['communities'].values():
            size = len(cinfo['members'])
            max_k = min(15, max(1, round(size ** 0.5)))
            assert len(cinfo['centroids']) <= max_k


class TestAutoLabeling:

    def test_type_dominant_label(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()

        # Create a cluster that's 100% "document" type
        doc_members = [f"doc_{i}" for i in range(10)]
        centroids = [{"id": "doc_0", "score": 1.0, "name": "API_Guide", "type": "document"}]
        dominant_types = [("document", 10)]
        dominant_layers = [("documentation", 10)]

        label = analyzer._auto_label(
            kg._graph, doc_members, centroids, dominant_types, dominant_layers
        )
        assert "document" in label.lower() or "documentation" in label.lower()

    def test_mixed_cluster_label(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()

        # Mix of types — no dominance
        mixed_members = ["auth_0", "doc_0", "data_0"]
        centroids = [{"id": "auth_0", "score": 0.9, "name": "AuthService", "type": "class"}]
        dominant_types = [("class", 1), ("document", 1), ("configuration", 1)]
        dominant_layers = [("code", 1), ("documentation", 1)]

        label = analyzer._auto_label(
            kg._graph, mixed_members, centroids, dominant_types, dominant_layers
        )
        assert "AuthService" in label

    def test_empty_centroids(self, kg):
        analyzer = CommunityAnalyzer()
        label = analyzer._auto_label(kg._graph, [], [], [], [])
        assert "empty" in label.lower()


class TestCommunityStats:

    def test_stats_computed(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_communities(kg._graph)

        for cinfo in result['communities'].values():
            stats = cinfo['stats']
            assert stats['size'] > 0
            assert 0 <= stats['density'] <= 1
            assert 0 <= stats['cohesion'] <= 1

    def test_single_node_community(self, kg):
        analyzer = CommunityAnalyzer()
        stats = analyzer._compute_stats(kg._graph, ["nonexistent"])
        assert stats['size'] == 1
        assert stats['density'] == 0.0
        assert stats['cohesion'] == 0.0


# ========== KnowledgeGraph Integration Tests ==========

class TestKnowledgeGraphCommunityMethods:

    def test_set_and_get_communities(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        overview = kg.get_communities()
        assert overview['num_communities'] > 0

    def test_community_id_on_nodes(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        # Every node should have a community_id
        for nid, ndata in kg._graph.nodes(data=True):
            assert 'community_id' in ndata, f"Node {nid} missing community_id"

    def test_get_community_for_entity(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        cid = kg.get_community_for_entity('auth_0')
        assert cid is not None
        assert cid.startswith('community_')

    def test_get_community_members(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        members = kg.get_community_members('community_0')
        assert len(members) > 0

    def test_community_index_rebuilt(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        assert len(kg._community_index) == community_data['num_communities']

    def test_get_stats_includes_community_info(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        stats = kg.get_stats()
        assert stats['has_communities'] is True
        assert stats['num_communities'] > 0

    def test_get_stats_without_communities(self, kg):
        stats = kg.get_stats()
        assert stats['has_communities'] is False
        assert stats['num_communities'] == 0


# ========== Persistence Roundtrip Tests ==========

class TestPersistenceRoundtrip:

    def test_dump_and_load_preserves_community_data(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        path = str(tmp_path / "graph_with_communities.json")
        kg.dump_to_json(path)

        # Load into new graph
        kg2 = KnowledgeGraph()
        kg2.load_from_json(path)

        # Verify community data survived
        assert kg2._metadata.get('community_data') is not None
        loaded_cd = kg2._metadata['community_data']
        assert loaded_cd['num_communities'] == community_data['num_communities']
        assert loaded_cd['algorithm'] == community_data['algorithm']

    def test_community_index_rebuilt_on_load(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        path = str(tmp_path / "graph_cd.json")
        kg.dump_to_json(path)

        kg2 = KnowledgeGraph()
        kg2.load_from_json(path)

        assert len(kg2._community_index) == community_data['num_communities']
        for cid in community_data['communities']:
            assert cid in kg2._community_index

    def test_node_community_id_persisted(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)
        kg.set_community_data(community_data)

        path = str(tmp_path / "graph_node_cd.json")
        kg.dump_to_json(path)

        kg2 = KnowledgeGraph()
        kg2.load_from_json(path)

        for nid in kg._graph.nodes():
            original_cid = kg._graph.nodes[nid].get('community_id')
            loaded_cid = kg2._graph.nodes[nid].get('community_id')
            assert original_cid == loaded_cid


# ========== Micro-Clustering Tests ==========

@pytest.mark.skipif(not HAS_IGRAPH, reason="igraph not installed")
class TestInfomapMicro:

    def test_micro_clustering(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        community_data = analyzer.detect_communities(kg._graph)

        # Try micro-clustering on the largest community
        largest_cid = max(
            community_data['communities'],
            key=lambda c: len(community_data['communities'][c]['members'])
        )

        micro = analyzer.detect_micro_clusters(
            kg._graph, community_data, largest_cid
        )
        # Micro-clustering may or may not find sub-clusters depending on structure
        # Just verify it doesn't crash and returns valid output
        if micro is not None:
            for mid, minfo in micro.items():
                assert 'members' in minfo
                assert 'centroids' in minfo
                assert 'label' in minfo

    def test_micro_on_small_community(self, three_cluster_kg):
        """Communities with < 5 members should skip micro-clustering."""
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()

        # Fake a tiny community
        fake_cd = {
            "communities": {
                "community_tiny": {"members": ["auth_0", "auth_1"]}
            }
        }
        result = analyzer.detect_micro_clusters(kg._graph, fake_cd, "community_tiny")
        assert result is None

    def test_micro_on_missing_community(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        result = analyzer.detect_micro_clusters(kg._graph, {"communities": {}}, "nonexistent")
        assert result is None


# ========== Retrieval Formatting Tests ==========

class TestRetrievalFormatting:

    def _make_wrapper(self, kg, tmp_path):
        """Build a minimal InventoryRetrievalApiWrapper."""
        from inventory.retrieval import InventoryRetrievalApiWrapper
        path = str(tmp_path / "graph.json")
        kg.dump_to_json(path)
        wrapper = InventoryRetrievalApiWrapper.model_construct(graph_path=path)
        wrapper._knowledge_graph = kg
        wrapper._embedding = None
        return wrapper

    def test_list_communities_formatting(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)
        output = wrapper.list_communities()
        assert "Communities" in output
        assert "community_" in output

    def test_get_community_detail_formatting(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)
        output = wrapper.get_community_detail("community_0")
        assert "Statistics" in output
        assert "community_0" in output

    def test_get_community_detail_not_found(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)
        output = wrapper.get_community_detail("nonexistent")
        assert "not found" in output.lower()

    def test_find_entity_community_formatting(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)
        output = wrapper.find_entity_community("AuthService")
        assert "community" in output.lower()

    def test_search_within_community(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)

        # Find a community that has auth nodes
        auth_cid = kg.get_community_for_entity('auth_0')
        if auth_cid:
            output = wrapper.search_within_community(auth_cid, "Auth")
            assert "Search results" in output or "No entities" in output

    def test_has_communities_flag(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        wrapper_no_cd = self._make_wrapper(kg, tmp_path)
        assert not wrapper_no_cd._has_communities()

        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper_with_cd = self._make_wrapper(kg, tmp_path)
        assert wrapper_with_cd._has_communities()

    def test_available_tools_include_community_tools(self, three_cluster_kg, tmp_path):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)
        kg.set_community_data(cd)

        wrapper = self._make_wrapper(kg, tmp_path)
        tools = wrapper.get_available_tools()
        tool_names = {t['name'] for t in tools}
        assert 'list_communities' in tool_names
        assert 'get_community_detail' in tool_names
        assert 'find_entity_community' in tool_names
        assert 'search_within_community' in tool_names

    def test_available_tools_no_community_without_data(self, kg, tmp_path):
        for i in range(3):
            kg.add_entity(f"n{i}", f"Node{i}", "class")
        wrapper = self._make_wrapper(kg, tmp_path)
        tools = wrapper.get_available_tools()
        tool_names = {t['name'] for t in tools}
        assert 'list_communities' not in tool_names


# ========== LLM Label Tests ==========

class TestLLMLabels:

    def test_label_prompt_construction(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        first_cid = list(cd['communities'].keys())[0]
        community = cd['communities'][first_cid]

        prompt = analyzer._build_label_prompt(kg._graph, community)
        assert "Key Entities" in prompt
        assert "Key Relationships" in prompt
        assert "3-7 words" in prompt
        assert "Reply with ONLY the label" in prompt
        # Heuristic label appears as reference
        assert community['label'] in prompt

    def test_generate_labels(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        # Save original heuristic labels
        original_labels = {
            cid: c['label'] for cid, c in cd['communities'].items()
        }

        def mock_llm(prompt):
            return "Authentication & Session Management"

        count = analyzer.generate_labels(kg._graph, cd, mock_llm)
        assert count == cd['num_communities']

        # All labels should be updated
        for cid, cinfo in cd['communities'].items():
            assert cinfo['label'] == "Authentication & Session Management"
            assert cinfo['label'] != original_labels[cid]

    def test_generate_labels_cleans_output(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        def mock_llm(prompt):
            return '  "REST API Request Handling."  '

        count = analyzer.generate_labels(kg._graph, cd, mock_llm)
        assert count > 0
        for cinfo in cd['communities'].values():
            label = cinfo['label']
            assert not label.startswith('"')
            assert not label.endswith('.')
            assert not label.startswith(' ')

    def test_generate_labels_truncates_long_output(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        def mock_llm(prompt):
            return "A" * 100   # Way too long

        analyzer.generate_labels(kg._graph, cd, mock_llm)
        for cinfo in cd['communities'].values():
            assert len(cinfo['label']) <= 80

    def test_generate_labels_keeps_heuristic_on_failure(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        original_labels = {
            cid: c['label'] for cid, c in cd['communities'].items()
        }

        def failing_llm(prompt):
            raise RuntimeError("LLM unavailable")

        count = analyzer.generate_labels(kg._graph, cd, failing_llm)
        assert count == 0

        # Labels should be unchanged (heuristic preserved)
        for cid, cinfo in cd['communities'].items():
            assert cinfo['label'] == original_labels[cid]

    def test_generate_labels_empty_communities(self, kg):
        analyzer = CommunityAnalyzer()
        count = analyzer.generate_labels(
            kg._graph, {"communities": {}}, lambda p: "label"
        )
        assert count == 0

    def test_labels_run_before_summaries(self, three_cluster_kg):
        """Labels should improve summary prompts — verify the summary prompt
        uses the updated label after generate_labels() runs."""
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        def mock_llm(prompt):
            return "Auth & Security Layer"

        analyzer.generate_labels(kg._graph, cd, mock_llm)

        first_cid = list(cd['communities'].keys())[0]
        community = cd['communities'][first_cid]
        prompt = analyzer._build_summary_prompt(kg._graph, community, max_tokens=200)
        assert "Auth & Security Layer" in prompt


# ========== LLM Summary Tests ==========

class TestLLMSummaries:

    def test_summary_prompt_construction(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        # Grab first community
        first_cid = list(cd['communities'].keys())[0]
        community = cd['communities'][first_cid]

        prompt = analyzer._build_summary_prompt(kg._graph, community, max_tokens=200)
        assert "Key Entities" in prompt
        assert "Key Relationships" in prompt
        assert "Other Members" in prompt

    def test_generate_summaries(self, three_cluster_kg):
        kg, auth, doc, data = three_cluster_kg
        analyzer = CommunityAnalyzer()
        cd = analyzer.detect_communities(kg._graph)

        # Mock LLM callable
        def mock_llm(prompt):
            return "This community handles authentication and session management."

        count = analyzer.generate_summaries(kg._graph, cd, mock_llm)
        assert count == cd['num_communities']

        for cinfo in cd['communities'].values():
            assert cinfo['summary'] is not None
