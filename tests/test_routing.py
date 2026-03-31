"""
Tests for adaptive query routing (routing.py).

Covers: Four-tier QueryRouter (regex override + embedding centroid + regex rescue + LLM fallback + hybrid),
        EmbeddingRouter, ToolSelector gating, PromptBuilder composition.
"""

import pytest
import sys
import os

# Add both plugin root and pylon root to path for imports
plugin_root = os.path.join(os.path.dirname(__file__), '..')
pylon_root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, plugin_root)
sys.path.insert(0, pylon_root)

from routing import (
    QueryRouter,
    EmbeddingRouter,
    ToolSelector,
    PromptBuilder,
    GraphProfile,
    STRATEGY_ENTITY_LOOKUP,
    STRATEGY_SEARCH,
    STRATEGY_TRAVERSAL,
    STRATEGY_OVERVIEW,
    STRATEGY_HYBRID,
    STRATEGY_TOOL_NAMES,
    _EMBEDDING_CONFIDENCE_THRESHOLD,
)


# ========== Fixtures ==========

class FakeTool:
    """Minimal tool stub for ToolSelector tests."""
    def __init__(self, name):
        self.name = name


def make_tools_dict(*names):
    """Create a {name: FakeTool} dict for the given tool names."""
    return {n: FakeTool(n) for n in names}


ALL_GRAPH_TOOL_NAMES = [
    "search_knowledge_graph", "semantic_search", "get_entity_details",
    "get_related_entities", "query_graph", "query_pattern",
    "get_pattern_vocabulary", "list_entity_types", "impact_analysis",
    "list_communities", "get_community_detail",
    "find_entity_community", "search_within_community",
]

SOURCE_TOOL_NAMES = ["github_search_code", "gitlab_read_file"]


@pytest.fixture
def all_tools():
    """All tools dict (9 graph + 2 source)."""
    return make_tools_dict(*(ALL_GRAPH_TOOL_NAMES + SOURCE_TOOL_NAMES))


@pytest.fixture
def profile_full():
    """GraphProfile with embeddings and source tools."""
    return GraphProfile(has_embeddings=True, has_source_tools=True, source_toolkit_names=["github"])


@pytest.fixture
def profile_no_embeddings():
    """GraphProfile without embeddings, with source tools."""
    return GraphProfile(has_embeddings=False, has_source_tools=True, source_toolkit_names=["github"])


@pytest.fixture
def profile_no_source():
    """GraphProfile with embeddings but no source tools."""
    return GraphProfile(has_embeddings=True, has_source_tools=False)


@pytest.fixture
def profile_minimal():
    """GraphProfile with nothing optional."""
    return GraphProfile(has_embeddings=False, has_source_tools=False)


# ========== QueryRouter Tests (3-tier: regex override → embedding → hybrid) ==========

@pytest.mark.requires_embeddings
class TestQueryRouter:

    # --- Traversal strategy (Tier 1: regex override) ---

    @pytest.mark.parametrize("query", [
        "what depends on UserService?",
        "what calls the AuthController?",
        "what inherits from BaseModel?",
        "what would break if I change UserService?",
        "impact of changing the config module",
        "upstream dependencies of Logger",
        "what extends BaseClass?",
        "trace the flow from controller to repository",
        "what would be affected if I remove this class?",
    ])
    def test_traversal_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_TRAVERSAL

    # --- Entity lookup strategy (Tier 2: embedding + Tier 2.5: regex rescue) ---

    @pytest.mark.parametrize("query", [
        "what is UserService?",
        "describe the AuthController class",
        "explain how the payment module works",
        "tell me about the Config class",
        "details of the LoginHandler",
        "definition of BaseModel",
        "how does UserService work?",
    ])
    def test_entity_lookup_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_ENTITY_LOOKUP

    # --- Entity lookup — noisy entity names (Tier 2.5 regex rescue) ---

    @pytest.mark.parametrize("query", [
        "what is Scenario_14_Get_Issues.feature?",
        "what is agent_page.py?",
        "tell me about login.feature",
        "describe test_helpers.py",
        "details of config_v2.yaml",
    ])
    def test_entity_lookup_noisy_names(self, query):
        """Filenames with _/./digits should still route to entity_lookup via regex rescue."""
        assert QueryRouter.classify(query) == STRATEGY_ENTITY_LOOKUP

    # --- Search strategy (Tier 2: embedding) ---

    @pytest.mark.parametrize("query", [
        "find all payment handlers",
        "search for authentication classes",
        "where is the database connection defined?",
        "look for error handling logic",
        "locate the API endpoints",
    ])
    def test_search_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_SEARCH

    # --- Overview strategy (Tier 2: embedding — the major improvement) ---

    @pytest.mark.parametrize("query", [
        "list all entity types",
        "how many classes are there?",
        "show all functions in the codebase",
        "architecture overview",
        "what types of entities exist?",
        "give me a summary of the graph",
        "statistics about the codebase",
        # These were misclassified by regex but now work via embeddings:
        "show me details of the architecture breakdown",
        "what is the high-level structure of this codebase",
        "how is the code organized into modules",
        "summarize the main architectural patterns",
        "what are the key subsystems",
        "what does the overall architecture look like",
        "give me a bird eye view of the system",
    ])
    def test_overview_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_OVERVIEW

    # --- Hybrid fallback (Tier 3: low confidence) ---

    @pytest.mark.parametrize("query", [
        "hello",
        "thanks",
        "I need to refactor the payment system",
        "hmm I am not sure what to ask",
    ])
    def test_hybrid_fallback(self, query):
        assert QueryRouter.classify(query) == STRATEGY_HYBRID

    # --- Edge cases ---

    def test_empty_string(self):
        assert QueryRouter.classify("") == STRATEGY_HYBRID

    def test_none_like(self):
        assert QueryRouter.classify("   ") == STRATEGY_HYBRID

    # --- Priority: traversal regex fires before embeddings ---

    def test_traversal_beats_entity_lookup(self):
        # "what calls X" should be traversal via regex, not entity_lookup
        assert QueryRouter.classify("what calls the UserService?") == STRATEGY_TRAVERSAL

    def test_traversal_beats_search(self):
        assert QueryRouter.classify("what depends on AuthModule?") == STRATEGY_TRAVERSAL

    def test_impact_is_traversal(self):
        assert QueryRouter.classify("what would be affected if I remove this class?") == STRATEGY_TRAVERSAL


# ========== EmbeddingRouter Tests ==========

@pytest.mark.requires_embeddings
class TestEmbeddingRouter:
    """Test the embedding classification layer directly."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Ensure clean singleton for each test."""
        EmbeddingRouter.reset()
        yield
        EmbeddingRouter.reset()

    def test_initialization(self):
        router = EmbeddingRouter.get_instance()
        strategy, score, margin = router.classify("architecture overview")
        assert strategy == STRATEGY_OVERVIEW
        assert score > 0.3

    def test_singleton_reuse(self):
        r1 = EmbeddingRouter.get_instance()
        r2 = EmbeddingRouter.get_instance()
        assert r1 is r2

    def test_overview_high_margin(self):
        router = EmbeddingRouter.get_instance()
        strategy, score, margin = router.classify("show me details of the architecture breakdown")
        assert strategy == STRATEGY_OVERVIEW
        assert margin > _EMBEDDING_CONFIDENCE_THRESHOLD

    def test_entity_lookup_classification(self):
        router = EmbeddingRouter.get_instance()
        strategy, _, margin = router.classify("what is the UserService class")
        assert strategy == STRATEGY_ENTITY_LOOKUP
        assert margin > _EMBEDDING_CONFIDENCE_THRESHOLD

    def test_search_classification(self):
        router = EmbeddingRouter.get_instance()
        strategy, _, margin = router.classify("find all payment handlers")
        assert strategy == STRATEGY_SEARCH
        assert margin > _EMBEDDING_CONFIDENCE_THRESHOLD

    def test_ambiguous_low_margin(self):
        router = EmbeddingRouter.get_instance()
        _, _, margin = router.classify("hello")
        assert margin < _EMBEDDING_CONFIDENCE_THRESHOLD

    @pytest.mark.parametrize("query,expected", [
        ("how is the code organized into modules", STRATEGY_OVERVIEW),
        ("summarize the main architectural patterns", STRATEGY_OVERVIEW),
        ("what are the key subsystems", STRATEGY_OVERVIEW),
        ("describe the AuthController class", STRATEGY_ENTITY_LOOKUP),
        ("where is the database config", STRATEGY_SEARCH),
    ])
    def test_embedding_accuracy(self, query, expected):
        router = EmbeddingRouter.get_instance()
        strategy, _, margin = router.classify(query)
        assert strategy == expected
        assert margin > _EMBEDDING_CONFIDENCE_THRESHOLD


# ========== Legacy Regex Tests ==========

class TestQueryRouterRegex:
    """Test the legacy regex-only classification (backward compatibility)."""

    def test_traversal_regex(self):
        assert QueryRouter.classify_regex("what depends on UserService?") == STRATEGY_TRAVERSAL

    def test_entity_lookup_regex(self):
        assert QueryRouter.classify_regex("what is UserService?") == STRATEGY_ENTITY_LOOKUP

    def test_search_regex(self):
        assert QueryRouter.classify_regex("find all handlers") == STRATEGY_SEARCH

    def test_overview_regex(self):
        assert QueryRouter.classify_regex("architecture overview") == STRATEGY_OVERVIEW

    def test_hybrid_regex(self):
        assert QueryRouter.classify_regex("hello") == STRATEGY_HYBRID


# ========== LLM Fallback Tests ==========

@pytest.mark.requires_embeddings
class TestQueryRouterLLMFallback:
    """Test Tier 3 LLM classification for low-confidence queries."""

    class FakeLLM:
        """Mock LLM that returns a predetermined strategy."""
        def __init__(self, response):
            self._response = response
            self.call_count = 0

        def invoke(self, prompt):
            self.call_count += 1

            class Msg:
                content = self._response
            return Msg()

    def test_llm_called_on_low_confidence(self):
        """LLM should be called when embeddings have low confidence."""
        llm = self.FakeLLM("entity_lookup")
        # Query that embeddings can't classify confidently
        result = QueryRouter.classify("what Scenario_14_Get_Issues.feature implements?", llm=llm)
        # Either the regex rescue or LLM should handle it — not hybrid
        assert result != STRATEGY_HYBRID

    def test_llm_not_called_on_high_confidence(self):
        """LLM should NOT be called when embeddings are confident."""
        llm = self.FakeLLM("search")  # wrong answer — should not be used
        result = QueryRouter.classify("architecture overview", llm=llm)
        assert result == STRATEGY_OVERVIEW
        assert llm.call_count == 0

    def test_llm_not_called_for_traversal_regex(self):
        """Traversal regex should short-circuit before LLM."""
        llm = self.FakeLLM("overview")  # wrong answer — should not be used
        result = QueryRouter.classify("what depends on UserService?", llm=llm)
        assert result == STRATEGY_TRAVERSAL
        assert llm.call_count == 0

    def test_llm_invalid_response_falls_to_hybrid(self):
        """Invalid LLM response should fall through to hybrid."""
        llm = self.FakeLLM("definitely_not_a_strategy")
        # Use a query that bypasses both regex rescue and embeddings
        result = QueryRouter.classify("hmm", llm=llm)
        assert result == STRATEGY_HYBRID

    def test_llm_exception_falls_to_hybrid(self):
        """LLM exception should fall through to hybrid gracefully."""

        class BrokenLLM:
            def invoke(self, prompt):
                raise RuntimeError("LLM service down")

        result = QueryRouter.classify("hmm", llm=BrokenLLM())
        assert result == STRATEGY_HYBRID

    def test_no_llm_falls_to_hybrid(self):
        """Without LLM, low-confidence queries should get hybrid."""
        # "hmm" — no regex match, low embedding confidence, no LLM
        result = QueryRouter.classify("hmm")
        assert result == STRATEGY_HYBRID

    @pytest.mark.parametrize("llm_response,expected", [
        ("entity_lookup", STRATEGY_ENTITY_LOOKUP),
        ("search", STRATEGY_SEARCH),
        ("traversal", STRATEGY_TRAVERSAL),
        ("overview", STRATEGY_OVERVIEW),
        ("hybrid", STRATEGY_HYBRID),
    ])
    def test_llm_valid_strategies(self, llm_response, expected):
        """LLM should accept all valid strategy names."""
        llm = self.FakeLLM(llm_response)
        result = QueryRouter._classify_with_llm("test query", llm)
        assert result == expected

    def test_llm_strips_whitespace_and_quotes(self):
        """LLM response with extra whitespace/quotes should still parse."""
        llm = self.FakeLLM('  "entity_lookup"  \n')
        result = QueryRouter._classify_with_llm("test query", llm)
        assert result == STRATEGY_ENTITY_LOOKUP


# ========== ToolSelector Tests ==========

class TestToolSelector:

    def test_entity_lookup_tools(self, all_tools, profile_full):
        result = ToolSelector.select(STRATEGY_ENTITY_LOOKUP, all_tools, profile_full)
        names = {t.name for t in result}
        assert names == {"search_knowledge_graph", "get_entity_details", "get_related_entities"}

    def test_search_tools_with_source(self, all_tools, profile_full):
        result = ToolSelector.select(STRATEGY_SEARCH, all_tools, profile_full)
        names = {t.name for t in result}
        # 3 graph + 2 source tools
        assert "search_knowledge_graph" in names
        assert "semantic_search" in names
        assert "get_related_entities" in names
        assert "github_search_code" in names
        assert "gitlab_read_file" in names

    def test_search_tools_no_embeddings(self, all_tools, profile_no_embeddings):
        result = ToolSelector.select(STRATEGY_SEARCH, all_tools, profile_no_embeddings)
        names = {t.name for t in result}
        # semantic_search should be excluded
        assert "semantic_search" not in names
        assert "search_knowledge_graph" in names

    def test_search_tools_no_source(self, all_tools, profile_no_source):
        result = ToolSelector.select(STRATEGY_SEARCH, all_tools, profile_no_source)
        names = {t.name for t in result}
        # No source tools
        assert "github_search_code" not in names
        assert "gitlab_read_file" not in names

    def test_traversal_tools(self, all_tools, profile_full):
        result = ToolSelector.select(STRATEGY_TRAVERSAL, all_tools, profile_full)
        names = {t.name for t in result}
        assert names == {
            "search_knowledge_graph", "get_related_entities",
            "query_pattern", "get_pattern_vocabulary",
            "query_graph", "impact_analysis",
        }

    def test_overview_tools(self, all_tools, profile_full):
        result = ToolSelector.select(STRATEGY_OVERVIEW, all_tools, profile_full)
        names = {t.name for t in result}
        assert names == {
            "list_entity_types", "query_graph", "search_knowledge_graph",
            "list_communities", "get_community_detail",
            "find_entity_community", "search_within_community",
        }

    def test_hybrid_returns_all(self, all_tools, profile_full):
        result = ToolSelector.select(STRATEGY_HYBRID, all_tools, profile_full)
        names = {t.name for t in result}
        expected = set(ALL_GRAPH_TOOL_NAMES + SOURCE_TOOL_NAMES)
        assert names == expected

    def test_hybrid_with_minimal_profile(self, all_tools, profile_minimal):
        """Hybrid always returns all tools regardless of profile."""
        result = ToolSelector.select(STRATEGY_HYBRID, all_tools, profile_minimal)
        assert len(result) == len(all_tools)

    def test_missing_tools_graceful(self, profile_full):
        """If a strategy tool isn't in all_tools, it's silently skipped."""
        partial = make_tools_dict("search_knowledge_graph", "get_entity_details")
        result = ToolSelector.select(STRATEGY_ENTITY_LOOKUP, partial, profile_full)
        names = {t.name for t in result}
        # get_related_entities is in the strategy but not in all_tools
        assert names == {"search_knowledge_graph", "get_entity_details"}


# ========== GraphProfile Tests ==========

class TestGraphProfile:

    def test_from_stats_with_embeddings(self):
        stats = {"has_embeddings": True, "entity_count": 100}
        profile = GraphProfile.from_stats(stats, has_source_tools=True, source_toolkit_names=["gh"])
        assert profile.has_embeddings is True
        assert profile.has_source_tools is True
        assert profile.source_toolkit_names == ["gh"]

    def test_from_stats_no_embeddings(self):
        stats = {"has_embeddings": False}
        profile = GraphProfile.from_stats(stats)
        assert profile.has_embeddings is False
        assert profile.has_source_tools is False
        assert profile.source_toolkit_names == []

    def test_from_stats_missing_key(self):
        stats = {}
        profile = GraphProfile.from_stats(stats)
        assert profile.has_embeddings is False


# ========== PromptBuilder Tests ==========

class TestPromptBuilder:

    def test_hybrid_returns_full_prompt(self):
        """Hybrid strategy uses the backward-compatible monolithic prompt."""
        result = PromptBuilder.compose(STRATEGY_HYBRID, ALL_GRAPH_TOOL_NAMES, "Depth: 2")
        # Should contain the full prompt's characteristic markers
        assert "CRITICAL RULES" in result
        assert "Rule 1:" in result or "Rule 2:" in result
        assert "Depth: 2" in result

    def test_entity_lookup_prompt_sections(self):
        tool_names = ["search_knowledge_graph", "get_entity_details", "get_related_entities"]
        result = PromptBuilder.compose(STRATEGY_ENTITY_LOOKUP, tool_names, "Depth: 2")

        # Should have base role
        assert "KNOWLEDGE GRAPH" in result
        # Should have focus intro
        assert "Focus" in result
        # Should have Rule 1 (always_relate)
        assert "get_related_entities" in result
        # Should have tool descriptions
        assert "search_knowledge_graph" in result
        # Should have workflow
        assert "authentication" in result
        # Should have filters
        assert "Depth: 2" in result

    def test_entity_lookup_excludes_code_rules(self):
        tool_names = ["search_knowledge_graph", "get_entity_details", "get_related_entities"]
        result = PromptBuilder.compose(STRATEGY_ENTITY_LOOKUP, tool_names, "")

        # Should NOT have rules about code search
        assert "LAST RESORT" not in result
        assert "query_pattern" not in result

    def test_traversal_prompt_has_pattern_rules(self):
        tool_names = list(STRATEGY_TOOL_NAMES[STRATEGY_TRAVERSAL])
        result = PromptBuilder.compose(STRATEGY_TRAVERSAL, tool_names, "Depth: 3")

        # Should have pattern-related rules
        assert "query_pattern" in result
        assert "CHAIN" in result
        assert "impact_analysis" in result

    def test_search_prompt_has_graph_before_code(self):
        tool_names = ["search_knowledge_graph", "semantic_search", "get_related_entities"]
        result = PromptBuilder.compose(STRATEGY_SEARCH, tool_names, "")

        # Should have graph-before-code and code-last-resort rules
        assert "LAST RESORT" in result
        assert "Graph tools BEFORE" in result

    def test_overview_prompt_is_lightweight(self):
        tool_names = ["list_entity_types", "query_graph", "search_knowledge_graph"]
        result = PromptBuilder.compose(STRATEGY_OVERVIEW, tool_names, "")

        # Overview has no heavy rules
        assert "LAST RESORT" not in result
        assert "query_pattern" not in result

    def test_focused_prompt_shorter_than_hybrid(self):
        """Focused strategies should produce shorter prompts than hybrid."""
        hybrid = PromptBuilder.compose(STRATEGY_HYBRID, ALL_GRAPH_TOOL_NAMES, "Depth: 2")
        entity = PromptBuilder.compose(
            STRATEGY_ENTITY_LOOKUP,
            ["search_knowledge_graph", "get_entity_details", "get_related_entities"],
            "Depth: 2",
        )
        overview = PromptBuilder.compose(
            STRATEGY_OVERVIEW,
            ["list_entity_types", "query_graph", "search_knowledge_graph"],
            "Depth: 2",
        )

        assert len(entity) < len(hybrid), (
            f"entity_lookup prompt ({len(entity)} chars) should be shorter than hybrid ({len(hybrid)} chars)"
        )
        assert len(overview) < len(hybrid), (
            f"overview prompt ({len(overview)} chars) should be shorter than hybrid ({len(hybrid)} chars)"
        )

    def test_only_requested_tools_in_prompt(self):
        """PromptBuilder should only include descriptions for tools in tool_names."""
        result = PromptBuilder.compose(
            STRATEGY_OVERVIEW,
            ["list_entity_types", "query_graph"],
            "",
        )
        # These tools should NOT appear in the prompt
        assert "impact_analysis" not in result.lower().split("## tool usage")[1] if "## Tool Usage" in result else True
        assert "semantic_search" not in result


# ========== Integration: Router → Selector → Builder ==========

class TestRoutingIntegration:

    def test_end_to_end_entity_lookup(self, all_tools, profile_full):
        query = "what is the UserService class?"
        strategy = QueryRouter.classify(query)
        assert strategy == STRATEGY_ENTITY_LOOKUP

        tools = ToolSelector.select(strategy, all_tools, profile_full)
        tool_names = [t.name for t in tools]
        assert len(tool_names) == 3

        prompt = PromptBuilder.compose(strategy, tool_names, "Depth: 2")
        assert "KNOWLEDGE GRAPH" in prompt
        assert len(prompt) > 100

    def test_end_to_end_traversal(self, all_tools, profile_full):
        query = "what would break if I change the Config module?"
        strategy = QueryRouter.classify(query)
        assert strategy == STRATEGY_TRAVERSAL

        tools = ToolSelector.select(strategy, all_tools, profile_full)
        tool_names = [t.name for t in tools]
        assert "impact_analysis" in tool_names
        assert "query_pattern" in tool_names

        prompt = PromptBuilder.compose(strategy, tool_names, "Depth: 3")
        assert "impact_analysis" in prompt

    def test_end_to_end_hybrid_fallback(self, all_tools, profile_full):
        query = "hello, can you help me?"
        strategy = QueryRouter.classify(query)
        assert strategy == STRATEGY_HYBRID

        tools = ToolSelector.select(strategy, all_tools, profile_full)
        assert len(tools) == len(all_tools)

        prompt = PromptBuilder.compose(strategy, [t.name for t in tools], "Depth: 2")
        # Hybrid uses the full monolithic prompt
        assert "CRITICAL RULES" in prompt
