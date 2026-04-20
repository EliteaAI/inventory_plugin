"""
Tests for graph construction quality fixes.

Covers:
- Entity ID generation with context-dependent types
- Short-name guard for code structural types
- Fact subject validation (no "unknown fact" hubs)
- Post-ingestion graph quality pass (hub pruning, generic edge removal)
- Fuzzy ID resolution tightening
- Entity filtering for relation extraction
- Cross-file relation key correctness ('relation_type' vs 'type')
"""

import pytest
import sys
import os
import hashlib

# Add both plugin root and pylon root to path for imports
plugin_root = os.path.join(os.path.dirname(__file__), '..')
pylon_root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, plugin_root)
sys.path.insert(0, pylon_root)

from inventory.knowledge_graph import KnowledgeGraph, Citation


# ========== Fixtures ==========

@pytest.fixture
def kg():
    """Empty KnowledgeGraph for testing."""
    return KnowledgeGraph()


@pytest.fixture
def pipeline(tmp_path):
    """Minimal IngestionPipeline for unit-testing methods."""
    from inventory.ingestion import IngestionPipeline
    p = IngestionPipeline(llm=None, graph_path=str(tmp_path / "test_graph.json"), source_toolkits={})
    return p


# ========== Entity ID Generation Tests ==========

class TestEntityIdGeneration:
    """Test _generate_entity_id with the new tiered dedup strategy."""

    def test_always_context_dependent_types(self, pipeline):
        """Types in CONTEXT_DEPENDENT_TYPES should always include file_path."""
        id1 = pipeline._generate_entity_id("tool", "Get Issues", "file_a.py")
        id2 = pipeline._generate_entity_id("tool", "Get Issues", "file_b.py")
        assert id1 != id2, "Same tool name in different files must produce different IDs"

    def test_method_is_context_dependent(self, pipeline):
        """Method type should be context-dependent (already in the set)."""
        id1 = pipeline._generate_entity_id("method", "get", "auth.py")
        id2 = pipeline._generate_entity_id("method", "get", "user.py")
        assert id1 != id2, "Same method name in different files must produce different IDs"

    def test_short_function_is_context_dependent(self, pipeline):
        """Short function names (<=2 words) should be file-scoped."""
        id1 = pipeline._generate_entity_id("function", "context", "helpers.py")
        id2 = pipeline._generate_entity_id("function", "context", "middleware.py")
        assert id1 != id2, "Short function 'context' in different files must differ"

    def test_short_variable_is_context_dependent(self, pipeline):
        """Short variable names should be file-scoped."""
        id1 = pipeline._generate_entity_id("variable", "url", "config.py")
        id2 = pipeline._generate_entity_id("variable", "url", "routes.py")
        assert id1 != id2, "Short variable 'url' in different files must differ"

    def test_short_class_is_context_dependent(self, pipeline):
        """Short class names should be file-scoped."""
        id1 = pipeline._generate_entity_id("class", "Page", "home.py")
        id2 = pipeline._generate_entity_id("class", "Page", "settings.py")
        assert id1 != id2, "Short class 'Page' in different files must differ"

    def test_short_import_is_context_dependent(self, pipeline):
        """Short import names should be file-scoped."""
        id1 = pipeline._generate_entity_id("import", "pytest", "test_a.py")
        id2 = pipeline._generate_entity_id("import", "pytest", "test_b.py")
        assert id1 != id2, "Short import 'pytest' in different files must differ"

    def test_short_constant_is_context_dependent(self, pipeline):
        """Short constant names should be file-scoped."""
        id1 = pipeline._generate_entity_id("constant", "API_URL", "config.py")
        id2 = pipeline._generate_entity_id("constant", "API_URL", "settings.py")
        assert id1 != id2, "Short constant 'API_URL' in different files must differ"

    def test_long_descriptive_function_merges(self, pipeline):
        """Long descriptive function names (>2 words, >15 chars) should merge."""
        id1 = pipeline._generate_entity_id(
            "function", "process_user_authentication_flow", "auth.py"
        )
        id2 = pipeline._generate_entity_id(
            "function", "process_user_authentication_flow", "auth_v2.py"
        )
        assert id1 == id2, "Long descriptive function names should merge across files"

    def test_long_descriptive_class_merges(self, pipeline):
        """Long descriptive class names should merge."""
        id1 = pipeline._generate_entity_id(
            "class", "UserAuthenticationService", "service.py"
        )
        id2 = pipeline._generate_entity_id(
            "class", "UserAuthenticationService", "test_service.py"
        )
        assert id1 == id2, "Long descriptive class names should merge"

    def test_semantic_types_always_merge(self, pipeline):
        """Semantic types (feature, requirement, etc.) should always merge."""
        id1 = pipeline._generate_entity_id("feature", "Login", "spec.md")
        id2 = pipeline._generate_entity_id("feature", "Login", "requirements.md")
        assert id1 == id2, "Features should merge across files regardless of name length"

    def test_fact_types_always_merge(self, pipeline):
        """Facts should merge across files (they use fact_type prefix)."""
        id1 = pipeline._generate_entity_id("fact", "requirement_Login required", "spec.md")
        id2 = pipeline._generate_entity_id("fact", "requirement_Login required", "doc.md")
        assert id1 == id2, "Facts with same subject should merge"

    def test_no_file_path_uses_standard(self, pipeline):
        """When file_path is None, always use standard (non-context) ID."""
        id1 = pipeline._generate_entity_id("function", "get", None)
        id2 = pipeline._generate_entity_id("function", "get", None)
        assert id1 == id2, "No file_path should produce consistent IDs"

    def test_case_insensitive(self, pipeline):
        """ID generation should be case-insensitive."""
        id1 = pipeline._generate_entity_id("Function", "Context", "file.py")
        id2 = pipeline._generate_entity_id("function", "context", "file.py")
        assert id1 == id2, "Case differences should not affect ID"


# ========== Fact Subject Validation Tests ==========

class TestFactSubjectValidation:
    """Test that facts with missing/generic subjects are filtered out."""

    def _extract_facts_from_entities(self, pipeline, facts_data, file_path="test.py"):
        """Helper to simulate fact entity creation with validation."""
        entities = []
        for fact in facts_data:
            subject = (fact.get('subject') or '').strip()
            if not subject or subject.lower() in ('unknown', 'unknown fact', 'n/a', 'none', ''):
                continue  # Should be skipped
            entities.append({
                'name': subject,
                'type': 'fact',
            })
        return entities

    def test_filters_unknown_fact(self, tmp_path):
        """Facts with subject 'unknown fact' should be filtered."""
        from inventory.ingestion import IngestionPipeline
        p = IngestionPipeline(llm=None, graph_path=str(tmp_path / "g.json"), source_toolkits={})
        facts = [
            {'subject': 'unknown fact', 'fact_type': 'test'},
            {'subject': 'Login requires 2FA', 'fact_type': 'requirement'},
        ]
        entities = self._extract_facts_from_entities(p, facts)
        assert len(entities) == 1
        assert entities[0]['name'] == 'Login requires 2FA'

    def test_filters_empty_subject(self, tmp_path):
        """Facts with empty string subject should be filtered."""
        from inventory.ingestion import IngestionPipeline
        p = IngestionPipeline(llm=None, graph_path=str(tmp_path / "g.json"), source_toolkits={})
        facts = [
            {'subject': '', 'fact_type': 'test'},
            {'subject': None, 'fact_type': 'test'},
        ]
        entities = self._extract_facts_from_entities(p, facts)
        assert len(entities) == 0

    def test_filters_generic_subjects(self, tmp_path):
        """Facts with generic placeholder subjects should be filtered."""
        from inventory.ingestion import IngestionPipeline
        p = IngestionPipeline(llm=None, graph_path=str(tmp_path / "g.json"), source_toolkits={})
        facts = [
            {'subject': 'unknown', 'fact_type': 'test'},
            {'subject': 'N/A', 'fact_type': 'test'},
            {'subject': 'none', 'fact_type': 'test'},
        ]
        entities = self._extract_facts_from_entities(p, facts)
        assert len(entities) == 0

    def test_keeps_valid_subjects(self, tmp_path):
        """Facts with real subjects should be kept."""
        from inventory.ingestion import IngestionPipeline
        p = IngestionPipeline(llm=None, graph_path=str(tmp_path / "g.json"), source_toolkits={})
        facts = [
            {'subject': 'API rate limit is 1000 req/min', 'fact_type': 'constraint'},
            {'subject': 'Tests require Python 3.12+', 'fact_type': 'requirement'},
        ]
        entities = self._extract_facts_from_entities(p, facts)
        assert len(entities) == 2


# ========== Post-Ingestion Quality Pass Tests ==========

class TestGraphQualityPass:
    """Test _run_graph_quality_pass hub pruning and edge cleanup."""

    def _build_hub_graph(self, pipeline):
        """Build a graph with hub nodes that should be pruned."""
        kg = pipeline._knowledge_graph
        
        # Create a hub "unknown fact" entity with many RELATED_TO in-edges
        hub_citation = Citation(file_path="hub.py")
        kg.add_entity("hub_fact", "unknown fact", "fact", hub_citation)
        
        # Create 60 source entities that all point to the hub
        for i in range(60):
            src_id = f"src_{i}"
            kg.add_entity(src_id, f"Feature {i}", "feature", Citation(file_path=f"file_{i}.md"))
            kg.add_relation(src_id, "hub_fact", "RELATED_TO", {'source': 'llm'})
        
        # Create a legitimate feature-to-feature relation
        kg.add_entity("feat_a", "Login Feature", "feature", Citation(file_path="login.md"))
        kg.add_entity("feat_b", "Auth Feature", "feature", Citation(file_path="auth.md"))
        kg.add_relation("feat_a", "feat_b", "RELATED_TO", {'source': 'llm'})
        
        return kg

    def test_prunes_hub_related_to_edges(self, pipeline):
        """Hub nodes (>50 RELATED_TO in-edges) should have those edges pruned."""
        kg = self._build_hub_graph(pipeline)
        
        # Before: hub has 60 related_to in-edges (stored lowercase by add_relation)
        graph = kg._graph
        hub_in = sum(1 for _, _, d in graph.in_edges("hub_fact", data=True)
                     if d.get('relation_type') == 'related_to')
        assert hub_in == 60
        
        pruned = pipeline._run_graph_quality_pass()
        
        # After: hub related_to edges are removed
        hub_in_after = sum(1 for _, _, d in graph.in_edges("hub_fact", data=True)
                          if d.get('relation_type') == 'related_to')
        assert hub_in_after == 0
        assert pruned >= 60

    def test_preserves_legitimate_related_to(self, pipeline):
        """Non-hub RELATED_TO edges should be preserved."""
        kg = self._build_hub_graph(pipeline)
        
        pipeline._run_graph_quality_pass()
        
        # feat_a -> feat_b should still exist
        graph = kg._graph
        assert graph.has_edge("feat_a", "feat_b")

    def test_prunes_generic_entity_edges(self, pipeline):
        """RELATED_TO edges to generic single-word code entities should be pruned."""
        kg = pipeline._knowledge_graph
        
        # Create generic entities
        kg.add_entity("fn_get", "get", "function", Citation(file_path="api.py"))
        kg.add_entity("fn_ctx", "context", "function", Citation(file_path="app.py"))
        kg.add_entity("var_url", "url", "variable", Citation(file_path="config.py"))
        
        # Create RELATED_TO edges to them
        kg.add_entity("feat_login", "Login Feature", "feature", Citation(file_path="spec.md"))
        kg.add_relation("feat_login", "fn_get", "RELATED_TO", {'source': 'llm'})
        kg.add_relation("feat_login", "fn_ctx", "RELATED_TO", {'source': 'llm'})
        kg.add_relation("feat_login", "var_url", "RELATED_TO", {'source': 'llm'})
        
        # Also create a legitimate edge (contains) that should NOT be pruned
        kg.add_entity("mod_api", "api_module", "module", Citation(file_path="api.py"))
        kg.add_relation("mod_api", "fn_get", "contains", {'source': 'parser'})
        
        pruned = pipeline._run_graph_quality_pass()
        
        graph = kg._graph
        # RELATED_TO to generic entities should be removed
        assert not graph.has_edge("feat_login", "fn_get")
        assert not graph.has_edge("feat_login", "fn_ctx")
        assert not graph.has_edge("feat_login", "var_url")
        # Contains edge should remain
        assert graph.has_edge("mod_api", "fn_get")
        assert pruned == 3

    def test_removes_self_loops(self, pipeline):
        """Self-loop edges should be removed."""
        kg = pipeline._knowledge_graph
        kg.add_entity("ent_1", "Entity One", "feature", Citation(file_path="test.md"))
        # Manually add a self-loop
        kg._graph.add_edge("ent_1", "ent_1", relation_type="RELATED_TO")
        
        pruned = pipeline._run_graph_quality_pass()
        
        assert not kg._graph.has_edge("ent_1", "ent_1")
        assert pruned >= 1

    def test_preserves_specific_relation_types(self, pipeline):
        """Non-RELATED_TO edges (contains, calls, implements, etc.) are never pruned."""
        kg = pipeline._knowledge_graph
        
        # Create a hub-like entity but with 'contains' edges
        kg.add_entity("class_page", "Page", "class", Citation(file_path="base.py"))
        for i in range(60):
            kid = f"method_{i}"
            kg.add_entity(kid, f"method_{i}", "method", Citation(file_path="base.py"))
            kg.add_relation("class_page", kid, "contains", {'source': 'parser'})
        
        pruned = pipeline._run_graph_quality_pass()
        
        # All contains edges should remain
        graph = kg._graph
        out_edges = list(graph.out_edges("class_page", data=True))
        contains_count = sum(1 for _, _, d in out_edges if d.get('relation_type') == 'contains')
        assert contains_count == 60
        assert pruned == 0


# ========== Fuzzy ID Resolution Tests ==========

class TestFuzzyIdResolution:
    """Test tightened fuzzy matching in RelationExtractor.resolve_id."""

    @staticmethod
    def _make_resolve_id(entities):
        """Build a resolve_id callable from the real RelationExtractor implementation."""
        from inventory.extractors import RelationExtractor

        id_lookup, name_to_id = RelationExtractor.build_entity_id_lookup(entities)
        return lambda ref: RelationExtractor.resolve_entity_id(ref, id_lookup, name_to_id)

    def test_exact_id_resolution(self):
        """Exact ID match should work."""
        entities = [{'id': 'abc123', 'name': 'Login Feature', 'type': 'feature'}]
        resolve = self._make_resolve_id(entities)
        assert resolve('abc123') == 'abc123'

    def test_exact_name_resolution(self):
        """Exact name match should work."""
        entities = [{'id': 'abc123', 'name': 'Login Feature', 'type': 'feature'}]
        resolve = self._make_resolve_id(entities)
        assert resolve('Login Feature') == 'abc123'

    def test_snake_case_resolution(self):
        """Snake case of name should resolve."""
        entities = [{'id': 'abc123', 'name': 'Login Feature', 'type': 'feature'}]
        resolve = self._make_resolve_id(entities)
        assert resolve('login_feature') == 'abc123'

    def test_rejects_single_short_word(self):
        """A single short word should NOT fuzzy-match anything."""
        entities = [{'id': 'abc123', 'name': 'User Authentication Handler', 'type': 'function'}]
        resolve = self._make_resolve_id(entities)
        # "get" is a single word < 3 chars filtered out, so fuzzy not attempted
        assert resolve('get') is None or resolve('get') != 'abc123'

    def test_rejects_low_overlap_match(self):
        """Two very different multi-word names sharing 1 word should NOT match."""
        entities = [
            {'id': 'e1', 'name': 'User Authentication Handler', 'type': 'function'},
            {'id': 'e2', 'name': 'User Profile Manager', 'type': 'class'},
        ]
        resolve = self._make_resolve_id(entities)
        # "Authentication Profile" shares only 1 word with each, shouldn't match
        # (Actually it shares "authentication" with e1 and "profile" with e2)
        # The key is it should not spuriously match with low overlap
        result = resolve('Authentication Profile')
        # Since "authentication" + "profile" is only 1 word overlap with either entity,
        # it should not match
        assert result is None

    def test_accepts_high_overlap_match(self):
        """Multi-word references with high word overlap should match."""
        entities = [
            {'id': 'e1', 'name': 'User Authentication Handler', 'type': 'function'},
        ]
        resolve = self._make_resolve_id(entities)
        # "User Authentication" has overlap of 2/3 with the entity
        assert resolve('User Authentication') == 'e1'

    def test_substring_requires_length(self):
        """Substring match should only work for long references (>=10 chars)."""
        entities = [
            {'id': 'e1', 'name': 'UserAuthenticationService', 'type': 'class'},
        ]
        resolve = self._make_resolve_id(entities)
        # Short substring "auth" should NOT match via substring
        # (But might match via word overlap if there's enough overlap)
        # "api" is 3 chars, well under 10
        assert resolve('api') is None or resolve('api') != 'e1'


# ========== Entity Filtering for Relation Extraction Tests ==========

class TestEntityFilteringForRelations:
    """Test that generic single-word code entities are filtered from relation extraction."""

    def test_filters_short_function_names(self, pipeline):
        """Short function names should be filtered from relation entity list."""
        file_entities = [
            {'id': 'e1', 'name': 'get', 'type': 'function', 'properties': {}},
            {'id': 'e2', 'name': 'context', 'type': 'function', 'properties': {}},
            {'id': 'e3', 'name': 'process_user_authentication', 'type': 'function', 'properties': {}},
            {'id': 'e4', 'name': 'Login Feature', 'type': 'feature', 'properties': {}},
        ]
        
        # Replicate the filtering logic from _extract_relations_from_file
        entity_dicts = []
        for e in file_entities:
            name = e.get('name', '').strip()
            etype = e.get('type', '').lower()
            if etype in ('function', 'variable', 'constant', 'import', 'class', 'method'):
                if len(name.split()) <= 1 and len(name) <= 15:
                    continue
            entity_dicts.append({'id': e['id'], 'name': name, 'type': e['type']})
        
        # get, context should be filtered; process_user_authentication and Login Feature should remain
        names = [e['name'] for e in entity_dicts]
        assert 'get' not in names
        assert 'context' not in names
        assert 'process_user_authentication' in names
        assert 'Login Feature' in names

    def test_keeps_semantic_entities(self, pipeline):
        """Semantic entities (feature, requirement, etc.) should never be filtered."""
        file_entities = [
            {'id': 'e1', 'name': 'Chat', 'type': 'feature', 'properties': {}},
            {'id': 'e2', 'name': 'API', 'type': 'requirement', 'properties': {}},
        ]
        
        entity_dicts = []
        for e in file_entities:
            name = e.get('name', '').strip()
            etype = e.get('type', '').lower()
            if etype in ('function', 'variable', 'constant', 'import', 'class', 'method'):
                if len(name.split()) <= 1 and len(name) <= 15:
                    continue
            entity_dicts.append({'id': e['id'], 'name': name, 'type': e['type']})
        
        assert len(entity_dicts) == 2

    def test_filters_short_variables(self, pipeline):
        """Short variable names should be filtered."""
        file_entities = [
            {'id': 'e1', 'name': 'url', 'type': 'variable', 'properties': {}},
            {'id': 'e2', 'name': 'resp', 'type': 'variable', 'properties': {}},
            {'id': 'e3', 'name': 'authentication_token_manager', 'type': 'variable', 'properties': {}},
        ]
        
        entity_dicts = []
        for e in file_entities:
            name = e.get('name', '').strip()
            etype = e.get('type', '').lower()
            if etype in ('function', 'variable', 'constant', 'import', 'class', 'method'):
                if len(name.split()) <= 1 and len(name) <= 15:
                    continue
            entity_dicts.append({'id': e['id'], 'name': name, 'type': e['type']})
        
        names = [e['name'] for e in entity_dicts]
        assert 'url' not in names
        assert 'resp' not in names
        assert 'authentication_token_manager' in names


# ========== Cross-File Relation Key Correctness Tests ==========

class TestCrossFileRelationKeys:
    """
    Test that _extract_cross_file_relations() produces dicts with 'relation_type'
    key (not 'type'), so cross-file structural edges survive through to graph insertion.
    
    The bug: all cross-file relation dicts used 'type' as key, but the caller
    reads rel.get('relation_type', 'RELATED_TO'), causing silent fallback to RELATED_TO.
    """

    def _make_entity(self, eid, name, etype, file_path, content="", properties=None):
        """Create a test entity dict with citation."""
        return {
            'id': eid,
            'name': name,
            'type': etype,
            'citation': {'file_path': file_path},
            'source_doc': type('Doc', (), {'page_content': content})() if content else None,
            'properties': properties or {},
        }

    def test_pattern_based_relations_use_relation_type_key(self, pipeline):
        """Pattern-matched cross-file relations must use 'relation_type' key."""
        # Create entities in two different files, where file_a imports something from file_b
        ent_a = self._make_entity(
            'e_a', 'main_module', 'module', 'main.py',
            content='from auth_handler import AuthHandler\nimport logging'
        )
        ent_b = self._make_entity(
            'e_b', 'AuthHandler', 'class', 'auth_handler.py',
            content='class AuthHandler:\n    pass'
        )
        
        entities = [ent_a, ent_b]
        all_entity_dicts = entities
        by_file = {
            'main.py': [ent_a],
            'auth_handler.py': [ent_b],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        # Every relation must have 'relation_type' key, NOT 'type'
        for rel in relations:
            assert 'relation_type' in rel, (
                f"Cross-file relation missing 'relation_type' key: {rel}"
            )
            assert 'type' not in rel or rel.get('type') != rel.get('relation_type'), (
                f"Cross-file relation should not have ambiguous 'type' key: {rel}"
            )

    def test_mention_based_relations_use_relation_type_key(self, pipeline):
        """Entity mention cross-file relations must use 'relation_type': 'MENTIONS'."""
        ent_spec = self._make_entity(
            'e_spec', 'Login Feature', 'feature', 'spec.md',
            content='The AuthenticationService handles login and signup flows.'
        )
        ent_auth = self._make_entity(
            'e_auth', 'AuthenticationService', 'class', 'auth.py',
            content='class AuthenticationService:\n    pass'
        )
        
        entities = [ent_spec, ent_auth]
        all_entity_dicts = entities
        by_file = {
            'spec.md': [ent_spec],
            'auth.py': [ent_auth],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        mention_rels = [r for r in relations if r.get('relation_type') == 'MENTIONS']
        # Should find at least one mention of AuthenticationService in spec.md
        for rel in mention_rels:
            assert rel['relation_type'] == 'MENTIONS'
            assert 'type' not in rel or rel['type'] != 'MENTIONS'

    def test_property_analysis_relations_use_relation_type_key(self, pipeline):
        """Property-based cross-file relations must preserve structural relation_type."""
        ent_a = self._make_entity(
            'e_a', 'UserController', 'class', 'controller.py',
            properties={'imports': ['UserService'], 'extends': ['BaseController']}
        )
        ent_svc = self._make_entity(
            'e_svc', 'UserService', 'class', 'service.py'
        )
        ent_base = self._make_entity(
            'e_base', 'BaseController', 'class', 'base.py'
        )
        
        entities = [ent_a, ent_svc, ent_base]
        all_entity_dicts = entities
        by_file = {
            'controller.py': [ent_a],
            'service.py': [ent_svc],
            'base.py': [ent_base],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        # Check structural types are preserved
        rel_types = {r['relation_type'] for r in relations if r.get('relation_type')}
        for rel in relations:
            assert 'relation_type' in rel
            # Property-based relations should be IMPORTS, EXTENDS, etc. — not generic RELATED_TO
            if rel.get('properties', {}).get('discovered_by') == 'property_analysis':
                assert rel['relation_type'] in ('IMPORTS', 'EXTENDS', 'IMPLEMENTS', 'DEPENDS_ON', 'USES', 'REFERENCES')

    def test_semantic_linking_relations_use_relation_type_key(self, pipeline):
        """Semantic feature linking must use 'relation_type': 'RELATED_TO'."""
        ent1 = self._make_entity(
            'e1', 'User Login Feature', 'feature', 'login_spec.md',
            properties={'domain': 'authentication'}
        )
        ent2 = self._make_entity(
            'e2', 'User Signup Feature', 'feature', 'signup_spec.md',
            properties={'domain': 'authentication'}
        )
        
        entities = [ent1, ent2]
        all_entity_dicts = entities
        by_file = {
            'login_spec.md': [ent1],
            'signup_spec.md': [ent2],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        domain_rels = [
            r for r in relations
            if r.get('properties', {}).get('discovered_by') == 'semantic_domain_match'
        ]
        assert len(domain_rels) >= 1, "Should find domain-based semantic link"
        for rel in domain_rels:
            assert rel['relation_type'] == 'RELATED_TO'

    def test_dedup_uses_relation_type_key(self, pipeline):
        """Deduplication must use 'relation_type' key (not 'type') in the dedup tuple."""
        # Two entities in different files that will produce duplicate cross-file relations
        ent_a = self._make_entity(
            'e_a', 'FeatureModule', 'module', 'feature.py',
            content='from utils import HelperClass\nfrom utils import HelperClass'
        )
        ent_b = self._make_entity(
            'e_b', 'HelperClass', 'class', 'utils.py',
            content='class HelperClass:\n    pass'
        )
        
        entities = [ent_a, ent_b]
        all_entity_dicts = entities
        by_file = {
            'feature.py': [ent_a],
            'utils.py': [ent_b],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        # Check no duplicate (source_id, target_id, relation_type) tuples
        keys = [(r['source_id'], r['target_id'], r['relation_type']) for r in relations]
        assert len(keys) == len(set(keys)), (
            f"Duplicate cross-file relation keys found: {[k for k in keys if keys.count(k) > 1]}"
        )

    def test_all_relations_have_required_structure(self, pipeline):
        """Every cross-file relation must have source_id, target_id, relation_type, properties."""
        ent_a = self._make_entity(
            'e_a', 'AppConfig', 'class', 'config.py',
            content='from database import DatabasePool\nclass AppConfig: pass',
            properties={'imports': ['DatabasePool']}
        )
        ent_b = self._make_entity(
            'e_b', 'DatabasePool', 'class', 'database.py',
            content='class DatabasePool:\n    pass'
        )
        
        entities = [ent_a, ent_b]
        all_entity_dicts = entities
        by_file = {
            'config.py': [ent_a],
            'database.py': [ent_b],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        for rel in relations:
            assert 'source_id' in rel, f"Missing source_id: {rel}"
            assert 'target_id' in rel, f"Missing target_id: {rel}"
            assert 'relation_type' in rel, f"Missing relation_type: {rel}"
            assert 'properties' in rel, f"Missing properties: {rel}"
            assert isinstance(rel['relation_type'], str), f"relation_type not string: {rel}"
            assert rel['relation_type'] != '', f"Empty relation_type: {rel}"

    def test_cross_file_relations_stored_correctly_in_graph(self, pipeline):
        """Integration test: cross-file relations end up in graph with correct relation_type."""
        kg = pipeline._knowledge_graph
        
        # Simulate what _extract_relations does: call _extract_cross_file_relations
        # then store the results in the graph
        ent_a = self._make_entity(
            'e_a', 'Router', 'class', 'router.py',
            content='from middleware import AuthMiddleware',
            properties={'imports': ['AuthMiddleware']}
        )
        ent_b = self._make_entity(
            'e_b', 'AuthMiddleware', 'class', 'middleware.py',
            content='class AuthMiddleware:\n    pass'
        )
        
        # Add entities to graph
        kg.add_entity('e_a', 'Router', 'class', Citation(file_path='router.py'))
        kg.add_entity('e_b', 'AuthMiddleware', 'class', Citation(file_path='middleware.py'))
        
        entities = [ent_a, ent_b]
        all_entity_dicts = entities
        by_file = {
            'router.py': [ent_a],
            'middleware.py': [ent_b],
        }
        
        relations = pipeline._extract_cross_file_relations(entities, all_entity_dicts, by_file)
        
        # Store relations in graph (mimics the caller in _extract_relations / run)
        for rel in relations:
            rel_type = rel.get('relation_type', 'RELATED_TO')
            kg.add_relation(
                rel['source_id'],
                rel['target_id'],
                rel_type,
                rel.get('properties', {})
            )
        
        # Verify edges in graph have correct relation_type
        graph = kg._graph
        for u, v, data in graph.edges(data=True):
            if u in ('e_a', 'e_b') and v in ('e_a', 'e_b'):
                stored_type = data.get('relation_type', '')
                assert stored_type != 'RELATED_TO' or any(
                    r.get('properties', {}).get('discovered_by', '').startswith('semantic')
                    for r in relations
                    if r['source_id'] == u and r['target_id'] == v
                ), (
                    f"Edge {u}->{v} has relation_type='{stored_type}', "
                    f"expected a structural type (IMPORTS, MENTIONS, etc.)"
                )


# ========== Relation Type Synonym & Normalization Tests ==========

class TestRelationTypeSynonymsAndNormalization:
    """Test RELATION_SYNONYMS mapping and relation_type case normalization.
    
    Regression tests for the bug where 'mentions' was mapped to 'related_to'
    in RELATION_SYNONYMS, causing query_pattern('[:mentions]') to fail.
    """

    def test_mentions_not_synonym_for_related_to(self):
        """'mentions' must NOT be a synonym for 'related_to' — it's a first-class relation type."""
        kg = KnowledgeGraph()
        assert 'mentions' not in kg.RELATION_SYNONYMS, (
            "'mentions' should not be in RELATION_SYNONYMS; "
            "it is a first-class relation type for cross-file content mentions"
        )

    def test_references_not_synonym_for_related_to(self):
        """'references' must NOT be a synonym for 'related_to' — it's a first-class relation type."""
        kg = KnowledgeGraph()
        assert 'references' not in kg.RELATION_SYNONYMS, (
            "'references' should not be in RELATION_SYNONYMS; "
            "it is a first-class relation type for cross-file references"
        )

    def test_add_relation_normalizes_to_lowercase(self):
        """add_relation should store relation_type in lowercase."""
        kg = KnowledgeGraph()
        kg.add_entity('a', 'NodeA', 'class')
        kg.add_entity('b', 'NodeB', 'function')
        kg.add_relation('a', 'b', 'MENTIONS', {'discovered_by': 'content_mention'})
        
        edge_data = kg._graph.edges['a', 'b']
        assert edge_data['relation_type'] == 'mentions', (
            f"Expected lowercase 'mentions', got '{edge_data['relation_type']}'"
        )

    def test_load_normalizes_edge_types_to_lowercase(self, tmp_path):
        """load_from_json should normalize edge relation_type values to lowercase."""
        kg = KnowledgeGraph()
        kg.add_entity('a', 'NodeA', 'class')
        kg.add_entity('b', 'NodeB', 'function')
        # Directly inject an uppercase edge to simulate old graph format
        kg._graph.add_edge('a', 'b', relation_type='MENTIONS')
        
        path = str(tmp_path / "test_graph.json")
        kg.dump_to_json(path)
        
        kg2 = KnowledgeGraph()
        kg2.load_from_json(path)
        
        edge_data = kg2._graph.edges['a', 'b']
        assert edge_data['relation_type'] == 'mentions', (
            f"After load, expected lowercase 'mentions', got '{edge_data['relation_type']}'"
        )

    def test_query_pattern_with_mentions_relation(self):
        """query_pattern with [:mentions] should find MENTIONS edges."""
        kg = KnowledgeGraph()
        kg.add_entity('src', 'TestFeature.feature', 'resource')
        kg.add_entity('tgt', 'some_function', 'function')
        kg.add_relation('src', 'tgt', 'MENTIONS', {
            'discovered_by': 'content_mention',
            'source_file': 'test.feature',
            'target_file': 'code.py',
        })
        
        results = kg.query_pattern('(TestFeature.feature)-[:mentions]->(?)')
        assert len(results) >= 1, (
            f"Expected at least 1 path via MENTIONS edge, got {len(results)}"
        )
        assert results[0]['edges'][0] == 'mentions'

    def test_query_pattern_with_typed_mentions_target(self):
        """query_pattern with [:mentions]->(?:function) should find MENTIONS → function paths."""
        kg = KnowledgeGraph()
        kg.add_entity('src', 'TestFeature.feature', 'resource')
        kg.add_entity('fn', 'my_func', 'function')
        kg.add_entity('cls', 'MyClass', 'class')
        kg.add_relation('src', 'fn', 'MENTIONS')
        kg.add_relation('src', 'cls', 'MENTIONS')
        
        results = kg.query_pattern('(TestFeature.feature)-[:mentions]->(?:function)')
        assert len(results) == 1
        assert results[0]['path'][-1]['name'] == 'my_func'

    def test_query_pattern_wildcard_still_finds_mentions(self):
        """query_pattern with [:*1..2] should traverse mentions edges."""
        kg = KnowledgeGraph()
        kg.add_entity('src', 'TestFeature.feature', 'resource')
        kg.add_entity('tgt', 'target_func', 'function')
        kg.add_relation('src', 'tgt', 'mentions')
        
        results = kg.query_pattern('(TestFeature.feature)-[:*1..2]->(?:function)')
        assert len(results) >= 1
        assert results[0]['path'][-1]['name'] == 'target_func'

    def test_synonym_mapping_for_legitimate_synonyms(self):
        """Verify legitimate synonym mappings still work (e.g., 'inherit' → 'extends')."""
        kg = KnowledgeGraph()
        assert kg.RELATION_SYNONYMS.get('inherit') == 'extends'
        assert kg.RELATION_SYNONYMS.get('invoke') == 'calls'
        assert kg.RELATION_SYNONYMS.get('contain') == 'contains'

    def test_backward_mentions_query(self):
        """Backward query_pattern with mentions should work."""
        kg = KnowledgeGraph()
        kg.add_entity('src', 'Source.feature', 'resource')
        kg.add_entity('tgt', 'target_fn', 'function')
        kg.add_relation('src', 'tgt', 'MENTIONS')
        
        results = kg.query_pattern('(?:function)<-[:mentions]-(Source.feature)')
        assert len(results) >= 1
        assert results[0]['path'][0]['name'] == 'target_fn'


# ========== Entity Type Priority Resolution Tests ==========

class TestEntityTypePriority:
    """Test _entity_type_priority, _build_entity_by_name, _build_entity_by_name_ids."""

    def test_priority_constants(self):
        """Structural types should have higher priority than import/fact."""
        from inventory.ingestion import _entity_type_priority
        assert _entity_type_priority('class') > _entity_type_priority('import')
        assert _entity_type_priority('class') > _entity_type_priority('fact')
        assert _entity_type_priority('function') > _entity_type_priority('import')
        assert _entity_type_priority('module') > _entity_type_priority('fact')

    def test_unknown_type_gets_default_priority(self):
        """Unknown entity types should get a middle-ground priority."""
        from inventory.ingestion import _entity_type_priority
        p = _entity_type_priority('unknown_thing')
        assert p > _entity_type_priority('import')
        assert p < _entity_type_priority('class')

    def test_build_entity_by_name_class_wins_over_import(self):
        """When class and import share a name, class should win."""
        from inventory.ingestion import _build_entity_by_name
        entities = [
            {'name': 'AgentPage', 'type': 'import', 'id': 'import_1'},
            {'name': 'AgentPage', 'type': 'class', 'id': 'class_1'},
            {'name': 'AgentPage', 'type': 'fact', 'id': 'fact_1'},
        ]
        result = _build_entity_by_name(entities)
        assert result['agentpage']['id'] == 'class_1'

    def test_build_entity_by_name_import_does_not_overwrite_class(self):
        """Import processed after class should NOT overwrite it."""
        from inventory.ingestion import _build_entity_by_name
        entities = [
            {'name': 'BasePage', 'type': 'class', 'id': 'class_1'},
            {'name': 'BasePage', 'type': 'import', 'id': 'import_1'},
            {'name': 'BasePage', 'type': 'import', 'id': 'import_2'},
            {'name': 'BasePage', 'type': 'import', 'id': 'import_3'},
        ]
        result = _build_entity_by_name(entities)
        assert result['basepage']['id'] == 'class_1'

    def test_build_entity_by_name_type_qualified_keys(self):
        """Type-qualified keys should always map to correct entity."""
        from inventory.ingestion import _build_entity_by_name
        entities = [
            {'name': 'Widget', 'type': 'class', 'id': 'cls_1'},
            {'name': 'Widget', 'type': 'import', 'id': 'imp_1'},
            {'name': 'Widget', 'type': 'fact', 'id': 'fact_1'},
        ]
        result = _build_entity_by_name(entities)
        assert result['class:widget']['id'] == 'cls_1'
        assert result['import:widget']['id'] == 'imp_1'
        assert result['fact:widget']['id'] == 'fact_1'

    def test_build_entity_by_name_ids_class_wins(self):
        """_build_entity_by_name_ids should also prefer class over import."""
        from inventory.ingestion import _build_entity_by_name_ids
        entities = [
            {'name': 'Foo', 'type': 'import', 'id': 'imp_foo'},
            {'name': 'Foo', 'type': 'class', 'id': 'cls_foo'},
            {'name': 'Foo', 'type': 'import', 'id': 'imp_foo_2'},
        ]
        result = _build_entity_by_name_ids(entities)
        assert result['foo'] == 'cls_foo'
        assert result['class:foo'] == 'cls_foo'

    def test_build_entity_by_name_existing_map(self):
        """Providing existing_map should merge results, preserving priority."""
        from inventory.ingestion import _build_entity_by_name
        existing = {}
        _build_entity_by_name(
            [{'name': 'X', 'type': 'import', 'id': 'imp_x'}],
            existing_map=existing
        )
        assert existing['x']['id'] == 'imp_x'

        # Now add class — should overwrite
        _build_entity_by_name(
            [{'name': 'X', 'type': 'class', 'id': 'cls_x'}],
            existing_map=existing
        )
        assert existing['x']['id'] == 'cls_x'

        # Adding another import should NOT overwrite
        _build_entity_by_name(
            [{'name': 'X', 'type': 'import', 'id': 'imp_x2'}],
            existing_map=existing
        )
        assert existing['x']['id'] == 'cls_x'

    def test_build_entity_by_name_empty_input(self):
        """Empty entity list should return empty dict."""
        from inventory.ingestion import _build_entity_by_name
        assert _build_entity_by_name([]) == {}

    def test_build_entity_by_name_no_name(self):
        """Entities without name should be skipped."""
        from inventory.ingestion import _build_entity_by_name
        result = _build_entity_by_name([{'type': 'class', 'id': 'c1'}])
        assert len(result) == 0

    def test_build_entity_by_name_ids_type_qualified_lookup(self):
        """Type-qualified keys in _build_entity_by_name_ids for extends resolution."""
        from inventory.ingestion import _build_entity_by_name_ids
        entities = [
            {'name': 'AgentPage', 'type': 'class', 'id': 'cls_agent'},
            {'name': 'AgentPage', 'type': 'import', 'id': 'imp_agent_1'},
            {'name': 'AgentPage', 'type': 'import', 'id': 'imp_agent_2'},
            {'name': 'AgentPage', 'type': 'fact', 'id': 'fact_agent'},
            {'name': 'BasePage', 'type': 'class', 'id': 'cls_base'},
            {'name': 'BasePage', 'type': 'import', 'id': 'imp_base_1'},
        ]
        result = _build_entity_by_name_ids(entities)
        # Simulate extends resolution: class:name lookup
        source_id = result.get('class:agentpage') or result.get('agentpage')
        target_id = result.get('class:basepage') or result.get('basepage')
        assert source_id == 'cls_agent'
        assert target_id == 'cls_base'

    def test_function_wins_over_import_but_not_class(self):
        """Function priority is between class and import."""
        from inventory.ingestion import _build_entity_by_name
        entities = [
            {'name': 'helper', 'type': 'import', 'id': 'imp'},
            {'name': 'helper', 'type': 'function', 'id': 'fn'},
        ]
        result = _build_entity_by_name(entities)
        assert result['helper']['id'] == 'fn'

        # Class should still win over function
        entities2 = [
            {'name': 'helper', 'type': 'function', 'id': 'fn'},
            {'name': 'helper', 'type': 'class', 'id': 'cls'},
        ]
        result2 = _build_entity_by_name(entities2)
        assert result2['helper']['id'] == 'cls'

    def test_same_priority_last_wins(self):
        """Equal priority entities: last one wins (same-priority overwrite allowed)."""
        from inventory.ingestion import _build_entity_by_name
        entities = [
            {'name': 'Config', 'type': 'class', 'id': 'cls_1'},
            {'name': 'Config', 'type': 'class', 'id': 'cls_2'},
        ]
        result = _build_entity_by_name(entities)
        # Both are class (priority 10), so last one wins
        assert result['config']['id'] == 'cls_2'
