"""
Tests for adaptive query routing (routing.py).

Covers: QueryRouter classification, ToolSelector gating, PromptBuilder composition.
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
    ToolSelector,
    PromptBuilder,
    GraphProfile,
    STRATEGY_ENTITY_LOOKUP,
    STRATEGY_SEARCH,
    STRATEGY_TRAVERSAL,
    STRATEGY_OVERVIEW,
    STRATEGY_HYBRID,
    STRATEGY_TOOL_NAMES,
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


# ========== QueryRouter Tests ==========

class TestQueryRouter:

    # --- Traversal strategy (highest priority) ---

    @pytest.mark.parametrize("query", [
        "what depends on UserService?",
        "what calls the AuthController?",
        "trace the call chain from API to Database",
        "show the import chain for this module",
        "what inherits from BaseModel?",
        "what would break if I change UserService?",
        "impact of changing the config module",
        "flow from controller to repository",
        "upstream dependencies of Logger",
        "what extends BaseClass?",
        "what implements the AuthInterface?",
    ])
    def test_traversal_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_TRAVERSAL

    # --- Entity lookup strategy ---

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

    # --- Search strategy ---

    @pytest.mark.parametrize("query", [
        "find all payment handlers",
        "search for authentication classes",
        "where is the database connection defined?",
        "look for error handling logic",
        "show me the API endpoints",
        "which class handles user registration?",
    ])
    def test_search_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_SEARCH

    # --- Overview strategy ---

    @pytest.mark.parametrize("query", [
        "list all entity types",
        "how many classes are there?",
        "show all functions in the codebase",
        "architecture overview",
        "what types of entities exist?",
        "give me a summary of the graph",
        "statistics about the codebase",
    ])
    def test_overview_queries(self, query):
        assert QueryRouter.classify(query) == STRATEGY_OVERVIEW

    # --- Hybrid fallback ---

    @pytest.mark.parametrize("query", [
        "hello",
        "thanks",
        "can you help me understand this better?",
        "I need to refactor the payment system",
        "the code has performance issues",
    ])
    def test_hybrid_fallback(self, query):
        assert QueryRouter.classify(query) == STRATEGY_HYBRID

    # --- Edge cases ---

    def test_empty_string(self):
        assert QueryRouter.classify("") == STRATEGY_HYBRID

    def test_none_like(self):
        assert QueryRouter.classify("   ") == STRATEGY_HYBRID

    # --- Priority: traversal beats entity_lookup ---

    def test_traversal_beats_entity_lookup(self):
        # "what calls X" should be traversal, not entity_lookup ("what is")
        assert QueryRouter.classify("what calls the UserService?") == STRATEGY_TRAVERSAL

    def test_traversal_beats_search(self):
        # "find the dependency chain" — "find" is search, but "dependency" is traversal
        assert QueryRouter.classify("what depends on AuthModule?") == STRATEGY_TRAVERSAL

    def test_impact_is_traversal(self):
        assert QueryRouter.classify("what would be affected if I remove this class?") == STRATEGY_TRAVERSAL


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
        assert names == {"list_entity_types", "query_graph", "search_knowledge_graph"}

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
