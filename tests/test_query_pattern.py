"""
Tests for query_pattern (Cypher-like DSL) in KnowledgeGraph.

Covers: pattern parsing, node resolution, BFS execution, edge cases.
"""

import pytest
import sys
import os

# Add both plugin root and pylon root to path for imports
plugin_root = os.path.join(os.path.dirname(__file__), '..')
pylon_root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, plugin_root)
sys.path.insert(0, pylon_root)

from inventory.knowledge_graph import KnowledgeGraph


# ========== Fixtures ==========

@pytest.fixture
def graph():
    """Build a test graph with BOTH code and documentation entities.
    
    Mirrors real graph structure which has:
    - Code: class, function, method, variable, module
    - Docs: fact, requirement, feature, rule, workflow, user_story
    - Relations: contains, RELATED_TO, calls, implements, extends, imports, decorates
    
    Code structure:
        Controller (class) --calls--> UserService (class) --calls--> UserRepo (class)
        Controller (class) --calls--> AuthService (class) --calls--> TokenValidator (function)
        UserService (class) --calls--> Logger (class)
        UserService (class) --extends--> BaseService (class)
        AuthService (class) --extends--> BaseService (class)
        BaseService (class) --imports--> Config (module)
        Config (module) --contains--> DB_URL (variable)
        Controller (class) --contains--> handle_request (method)
    
    Documentation structure:
        auth_req (requirement) --RELATED_TO--> AuthService (class)
        auth_req (requirement) --RELATED_TO--> auth_flow (workflow)
        login_feature (feature) --RELATED_TO--> AuthService (class)
        login_feature (feature) --RELATED_TO--> auth_req (requirement)
        password_rule (rule) --RELATED_TO--> auth_req (requirement)
        auth_fact (fact) --RELATED_TO--> AuthService (class)
        auth_story (user_story) --RELATED_TO--> login_feature (feature)
        auth_story (user_story) --RELATED_TO--> auth_req (requirement)
        login_feature (feature) --implements--> auth_req (requirement)
    """
    kg = KnowledgeGraph()
    
    # Code entities
    code_entities = [
        ("controller_1", "Controller", "class"),
        ("user_service_1", "UserService", "class"),
        ("auth_service_1", "AuthService", "class"),
        ("base_service_1", "BaseService", "class"),
        ("user_repo_1", "UserRepo", "class"),
        ("token_validator_1", "TokenValidator", "function"),
        ("logger_1", "Logger", "class"),
        ("config_1", "Config", "module"),
        ("db_url_1", "DB_URL", "variable"),
        ("handle_request_1", "handle_request", "method"),
    ]
    
    # Documentation entities
    doc_entities = [
        ("auth_req_1", "AuthenticationRequired", "requirement"),
        ("login_feature_1", "UserLogin", "feature"),
        ("password_rule_1", "PasswordComplexity", "rule"),
        ("auth_fact_1", "OAuthSupported", "fact"),
        ("auth_flow_1", "AuthenticationFlow", "workflow"),
        ("auth_story_1", "UserCanLogin", "user_story"),
    ]
    
    for eid, name, etype in code_entities + doc_entities:
        kg.add_entity(eid, name, etype)
    
    # Code relationships
    code_relations = [
        ("controller_1", "user_service_1", "calls"),
        ("controller_1", "auth_service_1", "calls"),
        ("user_service_1", "user_repo_1", "calls"),
        ("user_service_1", "logger_1", "calls"),
        ("auth_service_1", "token_validator_1", "calls"),
        ("user_service_1", "base_service_1", "extends"),
        ("auth_service_1", "base_service_1", "extends"),
        ("base_service_1", "config_1", "imports"),
        ("config_1", "db_url_1", "contains"),
        ("controller_1", "handle_request_1", "contains"),
    ]
    
    # Documentation relationships (RELATED_TO is the primary doc connector)
    doc_relations = [
        ("auth_req_1", "auth_service_1", "RELATED_TO"),
        ("auth_req_1", "auth_flow_1", "RELATED_TO"),
        ("login_feature_1", "auth_service_1", "RELATED_TO"),
        ("login_feature_1", "auth_req_1", "RELATED_TO"),
        ("password_rule_1", "auth_req_1", "RELATED_TO"),
        ("auth_fact_1", "auth_service_1", "RELATED_TO"),
        ("auth_story_1", "login_feature_1", "RELATED_TO"),
        ("auth_story_1", "auth_req_1", "RELATED_TO"),
        # Cross-domain: feature implements requirement
        ("login_feature_1", "auth_req_1", "implements"),
    ]
    
    for src, tgt, rel in code_relations + doc_relations:
        kg.add_relation(src, tgt, rel)
    
    return kg


# ========== Parser Tests ==========

class TestParsePattern:
    """Test _parse_pattern static/internal method."""
    
    def test_simple_forward(self, graph):
        result = graph._parse_pattern("(UserService)-[:calls]->(UserRepo)")
        assert len(result) == 1
        assert result[0]['direction'] == 'forward'
        assert result[0]['rel_types'] == ['calls']
        assert result[0]['min_hops'] == 1
        assert result[0]['max_hops'] == 1
    
    def test_simple_backward(self, graph):
        result = graph._parse_pattern("(UserRepo)<-[:calls]-(UserService)")
        assert result[0]['direction'] == 'backward'
    
    def test_hop_range(self, graph):
        result = graph._parse_pattern("(Controller)-[:calls*1..3]->(?)")
        assert result[0]['min_hops'] == 1
        assert result[0]['max_hops'] == 3
    
    def test_single_hop_star(self, graph):
        result = graph._parse_pattern("(A)-[:calls*2]->(B)")
        assert result[0]['min_hops'] == 2
        assert result[0]['max_hops'] == 2
    
    def test_any_relation(self, graph):
        result = graph._parse_pattern("(A)-[:]->(B)")
        assert result[0]['rel_types'] is None
    
    def test_any_relation_with_hops(self, graph):
        result = graph._parse_pattern("(A)-[:*1..3]->(B)")
        assert result[0]['rel_types'] is None
        assert result[0]['max_hops'] == 3
    
    def test_multiple_relation_types(self, graph):
        result = graph._parse_pattern("(A)-[:calls,extends]->(B)")
        assert set(result[0]['rel_types']) == {'calls', 'extends'}
    def test_max_hops_exceeded_raises(self, graph):
        with pytest.raises(ValueError, match="Maximum.*hops"):
            graph._parse_pattern("(A)-[:calls*1..10]->(B)")
    
    def test_min_greater_than_max_raises(self, graph):
        with pytest.raises(ValueError, match="min_hops.*max_hops"):
            graph._parse_pattern("(A)-[:calls*3..1]->(B)")
    
    def test_invalid_hop_spec_range(self, graph):
        """Non-numeric hop range gives helpful error, not raw ValueError."""
        with pytest.raises(ValueError, match="Invalid hop specification"):
            graph._parse_pattern("(A)-[:calls*a..b]->(B)")
    
    def test_invalid_hop_spec_single(self, graph):
        """Non-numeric single hop gives helpful error."""
        with pytest.raises(ValueError, match="Invalid hop specification"):
            graph._parse_pattern("(A)-[:calls*x]->(B)")


class TestParseNodeSpec:
    """Test _parse_node_spec static method."""
    
    def test_wildcard(self):
        result = KnowledgeGraph._parse_node_spec("?")
        assert result['name'] is None
        assert result['types'] is None
    
    def test_typed_wildcard(self):
        result = KnowledgeGraph._parse_node_spec("?:class")
        assert result['name'] is None
        assert result['types'] == ['class']
    
    def test_multi_typed_wildcard(self):
        result = KnowledgeGraph._parse_node_spec("?:class,function")
        assert set(result['types']) == {'class', 'function'}
    
    def test_named(self):
        result = KnowledgeGraph._parse_node_spec("UserService")
        assert result['name'] == 'UserService'
        assert result['types'] is None
    
    def test_named_typed(self):
        result = KnowledgeGraph._parse_node_spec("UserService:class")
        assert result['name'] == 'UserService'
        assert result['types'] == ['class']
    
    def test_empty_is_wildcard(self):
        result = KnowledgeGraph._parse_node_spec("")
        assert result['name'] is None
        assert result['types'] is None


# ========== Execution Tests ==========

class TestQueryPatternExecution:
    """Test query_pattern end-to-end on the fixture graph."""
    
    def test_direct_call(self, graph):
        """Controller calls UserService (1 hop)."""
        results = graph.query_pattern("(Controller)-[:calls]->(UserService)")
        assert len(results) == 1
        assert results[0]['length'] == 1
        assert results[0]['path'][0]['name'] == 'Controller'
        assert results[0]['path'][1]['name'] == 'UserService'
    
    def test_multi_hop_calls(self, graph):
        """Controller -> UserService -> UserRepo (2 hops)."""
        results = graph.query_pattern("(Controller)-[:calls*1..2]->(UserRepo)")
        assert any(r['length'] == 2 for r in results)
        # Verify path goes through UserService
        two_hop = [r for r in results if r['length'] == 2][0]
        names = [n['name'] for n in two_hop['path']]
        assert names == ['Controller', 'UserService', 'UserRepo']
    
    def test_wildcard_target(self, graph):
        """Controller calls anything (1 hop)."""
        results = graph.query_pattern("(Controller)-[:calls]->(?)") 
        names = {r['path'][-1]['name'] for r in results}
        assert 'UserService' in names
        assert 'AuthService' in names
    
    def test_typed_wildcard_target(self, graph):
        """UserService calls any function."""
        results = graph.query_pattern("(UserService)-[:calls]->(?:function)")
        # UserService doesn't directly call a function; it calls classes and Logger
        # Only AuthService calls TokenValidator (function)
        assert len(results) == 0
    
    def test_backward_direction(self, graph):
        """What calls UserService? (backward)."""
        results = graph.query_pattern("(UserService)<-[:calls]-(?)")
        names = {r['path'][-1]['name'] for r in results}
        assert 'Controller' in names
    
    def test_any_relation_type(self, graph):
        """All 1-hop connections from UserService."""
        results = graph.query_pattern("(UserService)-[:]->(?)") 
        edges = {r['edges'][0] for r in results}
        assert 'calls' in edges
        assert 'extends' in edges
    
    def test_extends_chain(self, graph):
        """UserService extends BaseService."""
        results = graph.query_pattern("(UserService)-[:extends]->(BaseService)")
        assert len(results) == 1
    
    def test_multi_hop_mixed_relations(self, graph):
        """Controller to Config via calls+imports (3 hops max)."""
        results = graph.query_pattern("(Controller)-[:*1..3]->(Config)")
        assert any(r['length'] <= 3 for r in results)
    
    def test_no_path_exists(self, graph):
        """No direct call from Logger to Controller."""
        results = graph.query_pattern("(Logger)-[:calls]->(Controller)")
        assert len(results) == 0
    
    def test_max_results_cap(self, graph):
        """Max results should be respected."""
        results = graph.query_pattern("(?)-[:*1..3]->(?)", max_results=3)
        assert len(results) <= 3
    
    def test_wildcard_source_typed(self, graph):
        """Any class that extends BaseService."""
        results = graph.query_pattern("(?:class)-[:extends]->(BaseService)")
        names = {r['path'][0]['name'] for r in results}
        assert 'UserService' in names
        assert 'AuthService' in names
    
    def test_contains_relation(self, graph):
        """Config contains DB_URL."""
        results = graph.query_pattern("(Config)-[:contains]->(DB_URL)")
        assert len(results) == 1
    
    def test_path_format(self, graph):
        """Verify path result structure."""
        results = graph.query_pattern("(Controller)-[:calls]->(UserService)")
        result = results[0]
        assert 'path' in result
        assert 'edges' in result
        assert 'length' in result
        assert len(result['path']) == 2
        assert len(result['edges']) == 1
        assert result['path'][0]['id'] is not None
        assert result['path'][0]['name'] == 'Controller'
        assert result['path'][0]['type'] is not None
    
    def test_multiple_rel_types_filter(self, graph):
        """Filter by calls OR extends."""
        results = graph.query_pattern("(UserService)-[:calls,extends]->(?)") 
        edges = {r['edges'][0] for r in results}
        assert 'calls' in edges or 'extends' in edges
    
    def test_deep_chain(self, graph):
        """Controller -> ... -> DB_URL (up to 5 hops)."""
        results = graph.query_pattern("(Controller)-[:*1..5]->(DB_URL)")
        # Path: Controller->UserService->BaseService->Config->DB_URL (4 hops via one route)
        # or Controller->AuthService->BaseService->Config->DB_URL (4 hops via another)
        assert len(results) >= 1
        assert all(r['length'] <= 5 for r in results)


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_graph(self):
        """Query on empty graph returns no results."""
        kg = KnowledgeGraph()
        results = kg.query_pattern("(?)-[:calls]->(?)") 
        assert results == []
    
    def test_nonexistent_entity(self, graph):
        """Named entity that doesn't exist returns empty."""
        results = graph.query_pattern("(NonExistent)-[:calls]->(?)") 
        assert results == []
    
    def test_syntax_help_on_bad_input(self, graph):
        """Bad syntax raises ValueError with help text."""
        with pytest.raises(ValueError):
            graph.query_pattern("bad syntax")
    
    def test_cycle_avoidance(self):
        """BFS should not loop on cycles."""
        kg = KnowledgeGraph()
        kg.add_entity("a", "A", "class")
        kg.add_entity("b", "B", "class")
        kg.add_relation("a", "b", "calls")
        kg.add_relation("b", "a", "calls")
        
        results = kg.query_pattern("(A)-[:calls*1..3]->(?)") 
        # Should get A->B (1 hop) and A->B->A (2 hops) but not infinite loop
        assert len(results) <= 5  # bounded
        assert all(r['length'] <= 3 for r in results)


# ========== Relation Synonym Tests ==========

class TestRelationSynonyms:
    """Test that relation synonym normalization works transparently."""
    
    def test_inherit_maps_to_extends(self, graph):
        """'inherit' in pattern → 'extends' in execution."""
        results = graph.query_pattern("(UserService)-[:inherit]->(BaseService)")
        assert len(results) == 1
        assert results[0]['edges'] == ['extends']
    
    def test_inherits_maps_to_extends(self, graph):
        results = graph.query_pattern("(?:class)-[:inherits]->(BaseService)")
        assert len(results) >= 2  # UserService + AuthService extend BaseService
    
    def test_invoke_maps_to_calls(self, graph):
        results = graph.query_pattern("(Controller)-[:invoke]->(UserService)")
        assert len(results) == 1
    
    def test_require_maps_to_imports(self, graph):
        results = graph.query_pattern("(BaseService)-[:require]->(Config)")
        assert len(results) == 1
        assert results[0]['edges'] == ['imports']
    
    def test_has_maps_to_contains(self, graph):
        """'has' → 'contains'."""
        results = graph.query_pattern("(Config)-[:has]->(DB_URL)")
        assert len(results) == 1
        assert results[0]['edges'] == ['contains']
    
    def test_related_maps_to_related_to(self, graph):
        """'related' → 'related_to' (the doc↔code connector)."""
        results = graph.query_pattern("(AuthenticationRequired)-[:related]->(AuthService)")
        assert len(results) == 1
        assert results[0]['edges'] == ['related_to']
    
    def test_references_is_first_class_relation(self, graph):
        """'references' is a first-class relation type, not a synonym for 'related_to'.
        
        Cross-file extraction can produce REFERENCES edges, so 'references'
        must query for 'references' edges specifically — not 'related_to'.
        """
        # The test graph has no 'references' edges, so querying with
        # [:references] should return no results (correct behavior)
        results = graph.query_pattern("(OAuthSupported)-[:references]->(AuthService)")
        assert len(results) == 0
        
        # But querying with [:related_to] should find the RELATED_TO edge
        results = graph.query_pattern("(OAuthSupported)-[:related_to]->(AuthService)")
        assert len(results) == 1
    
    def test_describes_maps_to_related_to(self, graph):
        """'describes' → 'related_to'."""
        results = graph.query_pattern("(?:fact)-[:describes]->(?:class)")
        assert len(results) >= 1
    
    def test_depends_maps_to_imports(self, graph):
        """'depends' → 'imports'."""
        results = graph.query_pattern("(BaseService)-[:depends]->(Config)")
        assert len(results) == 1
        assert results[0]['edges'] == ['imports']
    
    def test_satisfies_maps_to_implements(self, graph):
        """'satisfies' → 'implements'."""
        results = graph.query_pattern("(UserLogin)-[:satisfies]->(AuthenticationRequired)")
        assert len(results) == 1
        assert results[0]['edges'] == ['implements']
    
    def test_unknown_relation_passes_through(self, graph):
        """Relations not in synonym map are used as-is."""
        results = graph.query_pattern("(?)-[:calls]->(?)") 
        assert len(results) > 0  # 'calls' is real, returns results
    
    def test_synonym_with_hops(self, graph):
        """Synonyms work with multi-hop patterns."""
        results = graph.query_pattern("(Controller)-[:invoke*1..2]->(?)") 
        assert len(results) > 0
    
    def test_parse_normalizes_synonyms(self, graph):
        """_parse_pattern converts synonyms."""
        parsed = graph._parse_pattern("(A)-[:inherit,invoke*1..2]->(B)")
        assert 'extends' in parsed[0]['rel_types']
        assert 'calls' in parsed[0]['rel_types']
        assert 'inherit' not in parsed[0]['rel_types']
        assert 'invoke' not in parsed[0]['rel_types']
    
    def test_doc_synonym_about_maps_to_related_to(self, graph):
        """'about' → 'related_to'."""
        parsed = graph._parse_pattern("(?:fact)-[:about]->(?:class)")
        assert parsed[0]['rel_types'] == ['related_to']


# ========== Documentation Query Tests ==========

class TestDocumentationQueries:
    """Test pattern queries involving documentation entities (fact, requirement, feature, etc.)."""
    
    def test_requirement_related_to_class(self, graph):
        """Find requirements related to a code class."""
        results = graph.query_pattern("(AuthenticationRequired)-[:related_to]->(AuthService)")
        assert len(results) == 1
        path = results[0]['path']
        assert path[0]['type'] == 'requirement'
        assert path[1]['type'] == 'class'
    
    def test_all_docs_related_to_class(self, graph):
        """Find all documentation entities pointing to AuthService."""
        results = graph.query_pattern("(?)-[:related_to]->(AuthService)")
        assert len(results) >= 3  # requirement, feature, fact all → AuthService
        source_types = {r['path'][0]['type'] for r in results}
        assert 'requirement' in source_types
        assert 'feature' in source_types
        assert 'fact' in source_types
    
    def test_typed_wildcard_requirement(self, graph):
        """Find requirements related to any class."""
        results = graph.query_pattern("(?:requirement)-[:related_to]->(?:class)")
        assert len(results) >= 1
        assert all(r['path'][0]['type'] == 'requirement' for r in results)
        assert all(r['path'][-1]['type'] == 'class' for r in results)
    
    def test_feature_implements_requirement(self, graph):
        """Feature implements requirement — cross-domain relation."""
        results = graph.query_pattern("(?:feature)-[:implements]->(?:requirement)")
        assert len(results) == 1
        assert results[0]['path'][0]['name'] == 'UserLogin'
        assert results[0]['path'][1]['name'] == 'AuthenticationRequired'
    
    def test_multi_hop_doc_chain(self, graph):
        """Trace: user_story --RELATED_TO--> feature --RELATED_TO--> class (2 hops)."""
        results = graph.query_pattern("(?:user_story)-[:related_to*1..2]->(?:class)")
        # auth_story → login_feature → AuthService (2 hops)
        assert len(results) >= 1
        assert any(r['length'] == 2 for r in results)
    
    def test_rule_to_class_via_requirement(self, graph):
        """Trace: rule --RELATED_TO--> requirement --RELATED_TO--> class (2 hops)."""
        results = graph.query_pattern("(PasswordComplexity)-[:related_to*1..2]->(?:class)")
        # PasswordComplexity → AuthenticationRequired → AuthService
        assert len(results) >= 1
        two_hop = [r for r in results if r['length'] == 2]
        assert len(two_hop) >= 1
        assert two_hop[0]['path'][-1]['type'] == 'class'
    
    def test_backward_doc_query(self, graph):
        """What documentation mentions AuthService? (backward)."""
        results = graph.query_pattern("(AuthService)<-[:related_to]-(?)")
        assert len(results) >= 3
        source_types = {r['path'][-1]['type'] for r in results}
        assert 'requirement' in source_types or 'feature' in source_types
    
    def test_mixed_code_and_doc_any_relation(self, graph):
        """Any relation from AuthService — should include both code and doc edges."""
        results = graph.query_pattern("(AuthService)<-[:*1..1]-(?)") 
        # Incoming: requirement→RELATED_TO, feature→RELATED_TO, fact→RELATED_TO, 
        #           Controller→calls
        assert len(results) >= 3
    
    def test_workflow_connected_to_requirement(self, graph):
        """Workflow connected to requirement via RELATED_TO."""
        results = graph.query_pattern("(?:requirement)-[:related_to]->(?:workflow)")
        assert len(results) >= 1
        assert results[0]['path'][0]['type'] == 'requirement'
        assert results[0]['path'][-1]['type'] == 'workflow'


# ========== Chain Pattern Parser Tests ==========

class TestChainParsing:
    """Test multi-segment chain pattern parsing."""
    
    def test_two_segment_chain(self, graph):
        """Parse a 2-segment chain pattern."""
        segments = graph._parse_pattern(
            "(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
        )
        assert len(segments) == 2
        # First segment: feature -> requirement via implements
        assert segments[0]['direction'] == 'forward'
        assert segments[0]['rel_types'] == ['implements']
        # Second segment: requirement -> class via related_to
        assert segments[1]['direction'] == 'forward'
        assert segments[1]['rel_types'] == ['related_to']
    
    def test_three_segment_chain(self, graph):
        """Parse a 3-segment chain."""
        segments = graph._parse_pattern(
            "(?:user_story)-[:related_to]->(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
        )
        assert len(segments) == 3
        assert segments[0]['rel_types'] == ['related_to']
        assert segments[1]['rel_types'] == ['implements']
        assert segments[2]['rel_types'] == ['related_to']
    
    def test_chain_with_named_entities(self, graph):
        """Chain with named nodes."""
        segments = graph._parse_pattern(
            "(Controller)-[:calls]->(?:class)-[:extends]->(BaseService)"
        )
        assert len(segments) == 2
        assert segments[0]['source']['name'] == 'Controller'
        assert segments[1]['target']['name'] == 'BaseService'
    
    def test_chain_with_hops(self, graph):
        """Chain segments can have multi-hop."""
        segments = graph._parse_pattern(
            "(?)-[:calls*1..2]->(?:class)-[:extends*1..3]->(BaseService)"
        )
        assert len(segments) == 2
        assert segments[0]['max_hops'] == 2
        assert segments[1]['max_hops'] == 3
    
    def test_chain_mixed_directions(self, graph):
        """Chain segments can have different directions."""
        segments = graph._parse_pattern(
            "(AuthService)<-[:related_to]-(?:requirement)-[:related_to]->(?:workflow)"
        )
        assert len(segments) == 2
        assert segments[0]['direction'] == 'backward'
        assert segments[1]['direction'] == 'forward'
    
    def test_chain_synonym_normalization(self, graph):
        """Synonyms work in chain segments."""
        segments = graph._parse_pattern(
            "(?:feature)-[:satisfies]->(?:requirement)-[:related]->(?:class)"
        )
        assert segments[0]['rel_types'] == ['implements']  # satisfies → implements
        assert segments[1]['rel_types'] == ['related_to']  # related → related_to
    
    def test_max_segments_exceeded(self, graph):
        """More than MAX_CHAIN_SEGMENTS raises ValueError."""
        with pytest.raises(ValueError, match="segments"):
            graph._parse_pattern(
                "(?)-[:a]->(?)-[:b]->(?)-[:c]->(?)-[:d]->(?)-[:e]->(?)"
            )
    
    def test_single_segment_returns_list(self, graph):
        """Single segment still returns a list of length 1."""
        result = graph._parse_pattern("(A)-[:calls]->(B)")
        assert isinstance(result, list)
        assert len(result) == 1


# ========== Chain Pattern Execution Tests ==========

class TestChainExecution:
    """Test multi-segment chain pattern execution."""
    
    def test_two_segment_feature_to_class(self, graph):
        """feature -[implements]-> requirement -[related_to]-> class."""
        results = graph.query_pattern(
            "(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
        )
        # login_feature → auth_req → AuthService
        assert len(results) >= 1
        for r in results:
            assert r['path'][0]['type'] == 'feature'
            assert r['path'][1]['type'] == 'requirement'
            assert r['path'][-1]['type'] == 'class'
            assert r['length'] == 2
    
    def test_three_segment_story_to_class(self, graph):
        """user_story -[related_to]-> feature -[implements]-> requirement -[related_to]-> class."""
        results = graph.query_pattern(
            "(?:user_story)-[:related_to]->(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
        )
        # auth_story → login_feature → auth_req → AuthService
        assert len(results) >= 1
        for r in results:
            assert len(r['path']) == 4
            assert r['path'][0]['type'] == 'user_story'
            assert r['path'][1]['type'] == 'feature'
            assert r['path'][2]['type'] == 'requirement'
            assert r['path'][3]['type'] == 'class'
            assert r['length'] == 3
    
    def test_chain_named_endpoints(self, graph):
        """Named start/end with chain."""
        results = graph.query_pattern(
            "(Controller)-[:calls]->(?:class)-[:extends]->(BaseService)"
        )
        # Controller → UserService → BaseService or Controller → AuthService → BaseService
        assert len(results) >= 1
        for r in results:
            assert r['path'][0]['name'] == 'Controller'
            assert r['path'][-1]['name'] == 'BaseService'
            assert r['length'] == 2
    
    def test_chain_backward_segment(self, graph):
        """Chain with backward first segment."""
        results = graph.query_pattern(
            "(AuthService)<-[:related_to]-(?:requirement)-[:related_to]->(?:workflow)"
        )
        # AuthService ←[related_to]← auth_req →[related_to]→ auth_flow
        assert len(results) >= 1
        for r in results:
            assert r['path'][0]['name'] == 'AuthService'
            assert r['path'][-1]['type'] == 'workflow'
    
    def test_chain_no_intermediate_results(self, graph):
        """Chain where first segment finds nothing returns empty."""
        results = graph.query_pattern(
            "(NonExistent)-[:calls]->(?)-[:extends]->(BaseService)"
        )
        assert results == []
    
    def test_chain_no_continuation_results(self, graph):
        """Chain where second segment continuation is empty."""
        results = graph.query_pattern(
            "(Controller)-[:calls]->(?:class)-[:decorates]->(BaseService)"
        )
        # Controller calls classes, but none of them decorate BaseService
        assert results == []
    
    def test_chain_with_multi_hop_segment(self, graph):
        """Chain where a segment has multi-hop."""
        results = graph.query_pattern(
            "(?:class)-[:calls*1..2]->(?:class)-[:extends]->(BaseService)"
        )
        # Controller calls UserService/AuthService (1 hop), those extend BaseService
        assert len(results) >= 1
        for r in results:
            assert r['path'][-1]['name'] == 'BaseService'
    
    def test_chain_max_results_respected(self, graph):
        """Chain respects max_results cap."""
        results = graph.query_pattern(
            "(?)-[:related_to]->(?)-[:related_to]->(?)",
            max_results=2,
        )
        assert len(results) <= 2
    
    def test_chain_path_stitching(self, graph):
        """Verify stitched path has no duplicate at join point."""
        results = graph.query_pattern(
            "(UserLogin)-[:implements]->(AuthenticationRequired)-[:related_to]->(AuthService)"
        )
        assert len(results) == 1
        path = results[0]['path']
        # Should be 3 nodes: UserLogin, AuthenticationRequired, AuthService
        assert len(path) == 3
        names = [n['name'] for n in path]
        assert names == ['UserLogin', 'AuthenticationRequired', 'AuthService']
        # Edges should be 2
        assert len(results[0]['edges']) == 2
    
    def test_chain_edges_stitched_correctly(self, graph):
        """Verify edge types from different segments are stitched in order."""
        results = graph.query_pattern(
            "(UserLogin)-[:implements]->(AuthenticationRequired)-[:related_to]->(AuthService)"
        )
        assert results[0]['edges'] == ['implements', 'related_to']


# ========== Vocabulary Discovery Tests ==========

class TestPatternVocabulary:
    """Test get_pattern_vocabulary method."""
    
    def test_returns_code_entity_types(self, graph):
        vocab = graph.get_pattern_vocabulary()
        etypes = vocab['entity_types']
        assert 'class' in etypes
        assert 'function' in etypes
        assert 'module' in etypes
    
    def test_returns_doc_entity_types(self, graph):
        """Vocabulary includes documentation entity types."""
        vocab = graph.get_pattern_vocabulary()
        etypes = vocab['entity_types']
        assert 'requirement' in etypes
        assert 'feature' in etypes
        assert 'rule' in etypes
        assert 'fact' in etypes
        assert 'workflow' in etypes
        assert 'user_story' in etypes
    
    def test_returns_all_relation_types(self, graph):
        vocab = graph.get_pattern_vocabulary()
        rtypes = vocab['relation_types']
        assert 'calls' in rtypes
        assert 'extends' in rtypes
        assert 'imports' in rtypes
        assert 'contains' in rtypes
        # RELATED_TO stored lowercase after edge traversal
        related_key = [k for k in rtypes if 'related' in k.lower()]
        assert len(related_key) >= 1  # related_to present
    
    def test_sorted_by_count_descending(self, graph):
        vocab = graph.get_pattern_vocabulary()
        etypes = vocab['entity_types']
        counts = list(etypes.values())
        assert counts == sorted(counts, reverse=True)
        
        rtypes = vocab['relation_types']
        counts = list(rtypes.values())
        assert counts == sorted(counts, reverse=True)
    
    def test_generates_example_patterns(self, graph):
        vocab = graph.get_pattern_vocabulary()
        examples = vocab['example_patterns']
        assert len(examples) >= 1
        for ex in examples:
            assert '(' in ex and ')' in ex
            assert '[' in ex and ']' in ex
    
    def test_empty_graph_vocabulary(self):
        kg = KnowledgeGraph()
        vocab = kg.get_pattern_vocabulary()
        assert vocab['entity_types'] == {}
        assert vocab['relation_types'] == {}
        assert vocab['example_patterns'] == []
