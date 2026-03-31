#!/usr/bin/python3
# coding=utf-8

"""
Adaptive Query Routing for Inventory Chat

Four-tier intent classification and tool routing:
1. High-precision regex overrides for traversal verbs (0ms) to capture
   graph-walk / impact-analysis intents before anything else.
2. Embedding centroid similarity using local all-MiniLM-L6-v2 (~5ms) for
   semantic routing across the main strategies.
3. Regex "rescue" pass for classic keyword patterns when embedding
   confidence is low or ambiguous.
4. Optional LLM-assisted / hybrid routing that can consider all tools when
   confidence remains low or multiple strategies are plausible.

When embeddings or LLM support are unavailable, the router degrades
gracefully to regex-only classification.

Strategies:
- entity_lookup: "What is X?", "Describe X" → 3 tools
- search: "Find X", "Where is X?" → 3 graph + source tools
- traversal: "What calls X?", "Impact of X" → 6 tools
- overview: "List all X", "Architecture" → 3 tools
- hybrid: ambiguous / no match → ALL tools (backward compatible)
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# =============================================================================
# Strategies
# =============================================================================

STRATEGY_ENTITY_LOOKUP = "entity_lookup"
STRATEGY_SEARCH = "search"
STRATEGY_TRAVERSAL = "traversal"
STRATEGY_OVERVIEW = "overview"
STRATEGY_HYBRID = "hybrid"

ALL_STRATEGIES = [
    STRATEGY_TRAVERSAL,
    STRATEGY_ENTITY_LOOKUP,
    STRATEGY_SEARCH,
    STRATEGY_OVERVIEW,
    STRATEGY_HYBRID,
]

# =============================================================================
# GraphProfile — computed once per request from KnowledgeGraph.get_stats()
# =============================================================================


@dataclass
class GraphProfile:
    """Snapshot of graph capabilities for tool gating."""
    has_embeddings: bool = False
    has_source_tools: bool = False
    source_toolkit_names: List[str] = field(default_factory=list)

    @classmethod
    def from_stats(cls, stats: Dict[str, Any], has_source_tools: bool = False,
                   source_toolkit_names: Optional[List[str]] = None) -> "GraphProfile":
        return cls(
            has_embeddings=bool(stats.get("has_embeddings", False)),
            has_source_tools=has_source_tools,
            source_toolkit_names=source_toolkit_names or [],
        )


# =============================================================================
# QueryRouter — regex-based strategy classification (legacy, used as fallback)
# =============================================================================

# Priority order: traversal > entity_lookup > search > overview > hybrid
# Traversal checked first because "what calls X" should NOT match "what is X"
_STRATEGY_PATTERNS: Dict[str, List[re.Pattern]] = {
    STRATEGY_TRAVERSAL: [
        re.compile(r"\b(?:depend(?:s|encies|ency)?|depended)\b", re.IGNORECASE),
        re.compile(r"\b(?:call(?:s|ed|ing)?|invoke(?:s|d)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:import(?:s|ed)?|require(?:s|d)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:extend(?:s|ed)?|inherit(?:s|ed|ance)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:implement(?:s|ed)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:impact|affect(?:s|ed)?|break(?:s|ing)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:trace|chain|flow|path)\b", re.IGNORECASE),
        re.compile(r"\b(?:upstream|downstream)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+(?:calls|uses|depends\s+on|extends|implements)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+would\s+(?:break|be\s+affected)\b", re.IGNORECASE),
    ],
    STRATEGY_ENTITY_LOOKUP: [
        re.compile(r"\b(?:what\s+is|what\s+are)\b", re.IGNORECASE),
        re.compile(r"\b(?:describe|explain|tell\s+me\s+about)\b", re.IGNORECASE),
        re.compile(r"\b(?:details?\s+(?:of|about|for))\b", re.IGNORECASE),
        re.compile(r"\b(?:define|definition\s+of)\b", re.IGNORECASE),
        re.compile(r"\bhow\s+does\s+\w+\s+work\b", re.IGNORECASE),
    ],
    STRATEGY_SEARCH: [
        re.compile(r"\b(?:find|locate|discover)\b", re.IGNORECASE),
        re.compile(r"\b(?:search|look\s+for|look\s+up)\b", re.IGNORECASE),
        re.compile(r"\b(?:where\s+is|where\s+are|where\s+can\s+I\s+find)\b", re.IGNORECASE),
        re.compile(r"\b(?:show\s+me)\b", re.IGNORECASE),
        re.compile(r"\b(?:which\s+\w+\s+(?:has|have|contains?|handles?))\b", re.IGNORECASE),
    ],
    STRATEGY_OVERVIEW: [
        re.compile(r"\b(?:list\s+all|show\s+all|display\s+all)\b", re.IGNORECASE),
        re.compile(r"\b(?:how\s+many|count|total)\b", re.IGNORECASE),
        re.compile(r"\b(?:what\s+types?|what\s+kinds?)\b", re.IGNORECASE),
        re.compile(r"\b(?:overview|architecture|structure|summary)\b", re.IGNORECASE),
        re.compile(r"\b(?:statistics?|stats)\b", re.IGNORECASE),
        re.compile(r"\b(?:communit(?:y|ies))\b", re.IGNORECASE),
        re.compile(r"\b(?:group(?:s|ing)?|cluster(?:s|ing)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:breakdown|decompos(?:e|ition))\b", re.IGNORECASE),
    ],
}

# Classification priority order
_CLASSIFICATION_ORDER = [
    STRATEGY_TRAVERSAL,
    STRATEGY_ENTITY_LOOKUP,
    STRATEGY_SEARCH,
    STRATEGY_OVERVIEW,
]


# =============================================================================
# High-precision traversal regex overrides
# =============================================================================
# Embeddings conflate "what calls X" with "what is X" because the sentence
# structure is similar. These patterns fire BEFORE embeddings for traversal
# verbs that are unambiguously about graph relationships.

_TRAVERSAL_OVERRIDE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bwhat\s+(?:calls|uses|depends\s+on|extends|implements|imports)\b", re.IGNORECASE),
    re.compile(r"\b(?:depend(?:s|encies|ency)?|depended)\b", re.IGNORECASE),
    re.compile(r"\b(?:call(?:s|ed|ing)\s+\w+|invoke(?:s|d)\s+\w+)\b", re.IGNORECASE),
    re.compile(r"\b(?:extend(?:s|ed)\s+\w+|inherit(?:s|ed|ance))\b", re.IGNORECASE),
    re.compile(r"\b(?:impact|affect(?:s|ed)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:upstream|downstream)\b", re.IGNORECASE),
    re.compile(r"\b(?:trace|chain|flow)\s+(?:from|to|of)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+would\s+(?:break|be\s+affected)\b", re.IGNORECASE),
    re.compile(r"\bimport(?:s|ed)?\s+(?:from|by)\b", re.IGNORECASE),
]


# =============================================================================
# High-precision entity-lookup regex overrides
# =============================================================================
# Embeddings struggle with queries containing noisy entity names (filenames
# with underscores, dots, numbers) because tokens dilute the intent signal.
# "what is Scenario_14_Get_Issues.feature?" → entity_lookup, not hybrid.
# These fire AFTER traversal (so "what calls X" still wins) but BEFORE
# embeddings.

_ENTITY_LOOKUP_OVERRIDE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bwhat\s+is\s+\S+", re.IGNORECASE),
    re.compile(r"\bwhat\s+are\s+\S+", re.IGNORECASE),
    re.compile(r"\b(?:describe|explain)\s+(?:the\s+)?\S+", re.IGNORECASE),
    re.compile(r"\btell\s+me\s+about\s+\S+", re.IGNORECASE),
    re.compile(r"\bdetails?\s+(?:of|about|for)\s+\S+", re.IGNORECASE),
    re.compile(r"\bdefinition\s+of\s+\S+", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+\S+\s+work\b", re.IGNORECASE),
]


# =============================================================================
# EmbeddingRouter — semantic routing via sentence-transformer centroids
# =============================================================================

# Exemplar queries for each strategy. These are encoded into embeddings and
# averaged into centroids. New queries are classified by cosine similarity.
_STRATEGY_EXEMPLARS: Dict[str, List[str]] = {
    STRATEGY_TRAVERSAL: [
        "what depends on UserService",
        "what calls the AuthController",
        "trace the call chain from API to Database",
        "what inherits from BaseModel",
        "impact of changing the config module",
        "upstream dependencies of Logger",
        "what would break if I change this",
        "show the import chain for this module",
        "what extends BaseClass",
        "downstream effects of modifying",
    ],
    STRATEGY_ENTITY_LOOKUP: [
        "what is UserService",
        "describe the AuthController class",
        "explain how the payment module works",
        "tell me about the Config class",
        "how does the database connection work",
        "definition of BaseModel",
        "define the term BaseModel",
        "details of the LoginHandler",
        "what does this class do",
        "help me understand this class",
    ],
    STRATEGY_SEARCH: [
        "find all payment handlers",
        "search for authentication classes",
        "where is the database connection defined",
        "which class handles user registration",
        "look for error handling logic",
        "locate the configuration file",
        "show me the API endpoints",
    ],
    STRATEGY_OVERVIEW: [
        "list all entity types",
        "how many classes are there",
        "architecture overview",
        "give me a summary of the graph",
        "show me the community structure",
        "what groups exist in the codebase",
        "breakdown of the architecture",
        "decomposition of the system",
        "what are the main modules",
        "statistics about the codebase",
        "high-level structure of the code",
        "how is the code organized",
        "major components and subsystems",
        "what are the key architectural areas",
        "show me the clusters",
        "show all functions in the codebase",
        "display all classes and modules",
        "give me all the entity types",
    ],
}

# Minimum margin between top and second-best centroid score for confident
# classification. Below this threshold, the query is treated as ambiguous
# and routed to hybrid (all tools).
_EMBEDDING_CONFIDENCE_THRESHOLD = 0.05


class EmbeddingRouter:
    """
    Semantic query classifier using sentence-transformer centroids.

    Lazy-loaded singleton — the model is loaded on first classify() call
    and reused for all subsequent calls. Uses the same all-MiniLM-L6-v2
    model already cached locally for entity embeddings.
    """

    _instance: Optional["EmbeddingRouter"] = None

    def __init__(self):
        self._model = None
        self._centroids: Dict[str, np.ndarray] = {}
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "EmbeddingRouter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None

    def _ensure_initialized(self) -> bool:
        """Load model and compute centroids on first use. Returns False if unavailable."""
        if self._initialized:
            return self._model is not None

        try:
            from sentence_transformers import SentenceTransformer

            cache_dir = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/data/embeddings")
            self._model = SentenceTransformer("all-MiniLM-L6-v2", cache_folder=cache_dir)

            # Pre-compute normalized centroids for each strategy
            for strategy, exemplars in _STRATEGY_EXEMPLARS.items():
                embeddings = self._model.encode(exemplars, normalize_embeddings=True)
                centroid = embeddings.mean(axis=0)
                centroid = centroid / np.linalg.norm(centroid)
                self._centroids[strategy] = centroid

            logger.info(
                f"[EmbeddingRouter] Initialized with {len(self._centroids)} strategy centroids "
                f"({sum(len(v) for v in _STRATEGY_EXEMPLARS.values())} exemplars)"
            )
            self._initialized = True
            return True

        except Exception as e:
            logger.warning(f"[EmbeddingRouter] Failed to initialize, will use regex fallback: {e}")
            self._model = None
            self._initialized = True
            return False

    def classify(self, text: str) -> Tuple[str, float, float]:
        """
        Classify query by cosine similarity to strategy centroids.

        Returns:
            (strategy, score, margin) — strategy name, best cosine score,
            and margin between best and second-best scores.
            Returns (STRATEGY_HYBRID, 0.0, 0.0) when model is unavailable.
        """
        if not self._ensure_initialized():
            return STRATEGY_HYBRID, 0.0, 0.0

        query_emb = self._model.encode([text], normalize_embeddings=True)[0]
        scores = {
            strategy: float(np.dot(query_emb, centroid))
            for strategy, centroid in self._centroids.items()
        }
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        best_strategy, best_score = ranked[0]
        second_score = ranked[1][1]
        margin = best_score - second_score

        return best_strategy, best_score, margin


class QueryRouter:
    """
    Four-tier query classifier.

    Tier 1: High-precision traversal regex overrides (~0ms)
    Tier 2: Embedding centroid similarity (~5ms, lazy-loaded)
    Tier 2.5: Entity-lookup regex rescue for noisy entity names (~0ms)
    Tier 3: LLM intent classification (~200ms, optional, only low-confidence)
    Tier 4: Hybrid fallback — all tools when nothing else works

    Falls back to regex-only classification when embeddings are unavailable.
    """

    # Minimal LLM classification prompt — ~80 input tokens, ~1 output token
    _LLM_CLASSIFY_PROMPT = (
        "Classify this user question about a code repository into exactly one category.\n\n"
        "Categories:\n"
        "- entity_lookup: asking about a specific entity (class, function, file, module)\n"
        "- search: looking for code matching criteria or locating something\n"
        "- traversal: asking about relationships, dependencies, call chains, impact\n"
        "- overview: asking about architecture, statistics, structure, communities\n"
        "- hybrid: unclear, greeting, or unrelated to code\n\n"
        "Respond with ONLY the category name, nothing else.\n\n"
        "Query: {query}\n"
        "Category:"
    )

    _VALID_LLM_STRATEGIES = {
        STRATEGY_ENTITY_LOOKUP, STRATEGY_SEARCH,
        STRATEGY_TRAVERSAL, STRATEGY_OVERVIEW, STRATEGY_HYBRID,
    }

    @staticmethod
    def _classify_with_llm(text: str, llm) -> Optional[str]:
        """
        Classify via LLM when embeddings have low confidence.

        Args:
            text: User query
            llm: LangChain-compatible LLM instance

        Returns:
            Strategy string or None if LLM call fails.
        """
        try:
            prompt = QueryRouter._LLM_CLASSIFY_PROMPT.format(query=text)
            response = llm.invoke(prompt)

            # Handle both string and AIMessage responses
            content = response.content if hasattr(response, "content") else str(response)
            strategy = content.strip().lower().split()[0] if content.strip() else ""

            # Strip quotes/punctuation
            strategy = strategy.strip("\"'.,;:")

            if strategy in QueryRouter._VALID_LLM_STRATEGIES:
                logger.info(
                    f"[QueryRouter] T3-llm: {strategy} | query={text[:80]}"
                )
                return strategy

            logger.warning(
                f"[QueryRouter] LLM returned invalid strategy '{strategy}', "
                f"ignoring | query={text[:80]}"
            )
            return None

        except Exception as e:
            logger.warning(f"[QueryRouter] LLM classification failed: {e}")
            return None

    @staticmethod
    def classify(message: str, llm=None) -> str:
        """
        Classify a user message into a routing strategy.

        Args:
            message: The user's chat message
            llm: Optional LangChain LLM for low-confidence fallback.
                 Only called when regex + embeddings can't decide.

        Returns:
            Strategy name string
        """
        if not message or not message.strip():
            return STRATEGY_HYBRID

        text = message.strip()

        # Tier 1a: High-precision traversal regex overrides
        # Embeddings can't distinguish "what calls X" from "what is X"
        for pattern in _TRAVERSAL_OVERRIDE_PATTERNS:
            if pattern.search(text):
                logger.debug(f"[QueryRouter] T1-regex: traversal ({pattern.pattern})")
                return STRATEGY_TRAVERSAL

        # Tier 2: Embedding centroid similarity
        router = EmbeddingRouter.get_instance()
        strategy, score, margin = router.classify(text)

        # Model unavailable → fall back to regex-only classification
        if score == 0.0 and margin == 0.0:
            logger.debug("[QueryRouter] Embeddings unavailable, falling back to regex")
            return QueryRouter.classify_regex(message)

        if margin >= _EMBEDDING_CONFIDENCE_THRESHOLD:
            logger.debug(
                f"[QueryRouter] T2-embed: {strategy} "
                f"(score={score:.3f}, margin={margin:.3f})"
            )
            return strategy

        # Tier 2.5: Entity-lookup regex rescue
        # When embeddings are low-confidence (noisy entity names like
        # filenames with _/./digits), check if the query structure
        # matches unambiguous entity-lookup patterns.
        for pattern in _ENTITY_LOOKUP_OVERRIDE_PATTERNS:
            if pattern.search(text):
                logger.debug(
                    f"[QueryRouter] T2.5-regex-rescue: entity_lookup "
                    f"(embed margin={margin:.3f} < {_EMBEDDING_CONFIDENCE_THRESHOLD})"
                )
                return STRATEGY_ENTITY_LOOKUP

        # Tier 3: LLM classification (only when an LLM is provided)
        # ~200ms but only fires for the ~10-15% of ambiguous queries
        if llm is not None:
            llm_strategy = QueryRouter._classify_with_llm(text, llm)
            if llm_strategy:
                return llm_strategy

        # Tier 4: Low confidence, no LLM → hybrid (all tools)
        logger.debug(
            f"[QueryRouter] T4-hybrid: best={strategy} "
            f"(score={score:.3f}, margin={margin:.3f} < {_EMBEDDING_CONFIDENCE_THRESHOLD})"
        )
        return STRATEGY_HYBRID

    @staticmethod
    def classify_regex(message: str) -> str:
        """
        Legacy regex-only classification.

        Kept for backward compatibility and as fallback when embeddings
        are unavailable. Used internally by tests that validate regex behavior.
        """
        if not message or not message.strip():
            return STRATEGY_HYBRID

        text = message.strip()
        for strategy in _CLASSIFICATION_ORDER:
            patterns = _STRATEGY_PATTERNS[strategy]
            for pattern in patterns:
                if pattern.search(text):
                    return strategy

        return STRATEGY_HYBRID


# =============================================================================
# ToolSelector — strategy → tool subset mapping
# =============================================================================

# Tool names that each strategy should include (graph-native tools only)
STRATEGY_TOOL_NAMES: Dict[str, List[str]] = {
    STRATEGY_ENTITY_LOOKUP: [
        "search_knowledge_graph",
        "get_entity_details",
        "get_related_entities",
    ],
    STRATEGY_SEARCH: [
        "search_knowledge_graph",
        "semantic_search",       # gated by has_embeddings
        "get_related_entities",
        # + source tools (gated by has_source_tools)
    ],
    STRATEGY_TRAVERSAL: [
        "search_knowledge_graph",
        "get_related_entities",
        "query_pattern",
        "get_pattern_vocabulary",
        "query_graph",
        "impact_analysis",
    ],
    STRATEGY_OVERVIEW: [
        "list_entity_types",
        "query_graph",
        "search_knowledge_graph",
        "list_communities",
        "get_community_detail",
        "find_entity_community",
        "search_within_community",
    ],
    # hybrid = all tools, handled specially in select()
}


class ToolSelector:
    """Select focused tool subsets based on strategy and graph profile."""

    @staticmethod
    def select(strategy: str, all_tools: Dict[str, Any],
               profile: GraphProfile) -> List[Any]:
        """
        Select tools for the given strategy.

        Args:
            strategy: One of the STRATEGY_* constants
            all_tools: Dict mapping tool name → Tool object (all available tools)
            profile: GraphProfile with capability flags

        Returns:
            List of Tool objects for the agent
        """
        if strategy == STRATEGY_HYBRID:
            # Hybrid = all tools, backward compatible
            return list(all_tools.values())

        # Get the base tool names for this strategy
        tool_names = STRATEGY_TOOL_NAMES.get(strategy, [])

        selected = []
        for name in tool_names:
            # Gate conditional tools
            if name == "semantic_search" and not profile.has_embeddings:
                continue
            if name in all_tools:
                selected.append(all_tools[name])

        # Add source toolkit tools for strategies that use them
        if strategy == STRATEGY_SEARCH and profile.has_source_tools:
            for name, tool in all_tools.items():
                if name not in STRATEGY_TOOL_NAMES.get(strategy, []) and _is_source_tool(name):
                    selected.append(tool)

        return selected


def _is_source_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to a source toolkit (prefixed names)."""
    # Source tools are prefixed with toolkit name: "github_search_code", "gitlab_read_file", etc.
    # Graph-native tools don't have such prefixes
    _GRAPH_TOOL_NAMES = {
        "search_knowledge_graph", "semantic_search", "get_entity_details",
        "get_related_entities", "query_graph", "query_pattern",
        "get_pattern_vocabulary", "list_entity_types", "impact_analysis",
        "list_communities", "get_community_detail",
        "find_entity_community", "search_within_community",
    }
    return tool_name not in _GRAPH_TOOL_NAMES


# =============================================================================
# PromptBuilder — compose strategy-specific system prompts
# =============================================================================

# Modular prompt sections — imported from constants.py at runtime to avoid
# circular imports and keep constants.py as the single source of truth.
# See constants.py for PROMPT_BASE, PROMPT_RULES, STRATEGY_INTROS, etc.


class PromptBuilder:
    """Compose strategy-specific system prompts from modular sections."""

    @staticmethod
    def compose(strategy: str, tool_names: List[str], filters_text: str) -> str:
        """
        Build a focused system prompt for the given strategy.

        Imports modular prompt sections from constants.py and assembles
        only the relevant parts for this strategy.

        Args:
            strategy: Strategy name
            tool_names: Names of tools available to the agent
            filters_text: Formatted filter settings string

        Returns:
            Complete system prompt string
        """
        # Late import to avoid circular dependency
        # Support both relative (production) and absolute (test) import paths
        try:
            from .constants import (
                PROMPT_BASE,
                PROMPT_RULES,
                STRATEGY_INTROS,
                STRATEGY_RULE_KEYS,
                STRATEGY_WORKFLOWS,
                TOOL_DESCRIPTIONS,
                INVENTORY_CHAT_SYSTEM_PROMPT,
            )
        except ImportError:
            from constants import (
                PROMPT_BASE,
                PROMPT_RULES,
                STRATEGY_INTROS,
                STRATEGY_RULE_KEYS,
                STRATEGY_WORKFLOWS,
                TOOL_DESCRIPTIONS,
                INVENTORY_CHAT_SYSTEM_PROMPT,
            )

        # Hybrid uses the full backward-compatible prompt
        if strategy == STRATEGY_HYBRID:
            return INVENTORY_CHAT_SYSTEM_PROMPT.format(filters=filters_text)

        # Build focused prompt
        sections = []

        # 1. Base role
        sections.append(PROMPT_BASE)

        # 2. Strategy-specific focus intro
        intro = STRATEGY_INTROS.get(strategy)
        if intro:
            sections.append(f"\n## Focus\n{intro}")

        # 3. Relevant rules only
        rule_keys = STRATEGY_RULE_KEYS.get(strategy, [])
        if rule_keys:
            sections.append("\n## CRITICAL RULES")
            for i, key in enumerate(rule_keys, 1):
                rule_text = PROMPT_RULES.get(key, "")
                if rule_text:
                    sections.append(f"\n### Rule {i}: {rule_text}")

        # 4. Tool descriptions for available tools only
        sections.append("\n## Tool Usage\n")
        for name in tool_names:
            desc = TOOL_DESCRIPTIONS.get(name)
            if desc:
                sections.append(f"**{name}** - {desc}\n")

        # 5. Strategy-specific workflow example
        workflow = STRATEGY_WORKFLOWS.get(strategy)
        if workflow:
            sections.append(f"\n{workflow}")

        # 6. Filters
        sections.append(f"\n## Current Settings\n{filters_text}")

        return "\n".join(sections)
