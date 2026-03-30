#!/usr/bin/python3
# coding=utf-8

"""
Adaptive Query Routing for Inventory Chat

Classifies user queries into strategies, selects focused tool subsets,
and composes strategy-specific system prompts. Reduces prompt tokens
by ~60% when intent is clear, falls back to full toolset when ambiguous.

Strategies:
- entity_lookup: "What is X?", "Describe X" → 3 tools
- search: "Find X", "Where is X?" → 3 graph + source tools
- traversal: "What calls X?", "Impact of X" → 6 tools
- overview: "List all X", "Architecture" → 3 tools
- hybrid: ambiguous / no match → ALL tools (backward compatible)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
# QueryRouter — regex-based strategy classification
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
    ],
}

# Classification priority order
_CLASSIFICATION_ORDER = [
    STRATEGY_TRAVERSAL,
    STRATEGY_ENTITY_LOOKUP,
    STRATEGY_SEARCH,
    STRATEGY_OVERVIEW,
]


class QueryRouter:
    """Classify user queries into routing strategies using regex patterns."""

    @staticmethod
    def classify(message: str) -> str:
        """
        Classify a user message into a routing strategy.

        Checks patterns in priority order: traversal → entity_lookup → search → overview.
        Returns 'hybrid' if no pattern matches (safe fallback with all tools).

        Args:
            message: The user's chat message

        Returns:
            Strategy name string
        """
        if not message or not message.strip():
            return STRATEGY_HYBRID

        text = message.strip()

        for strategy in _CLASSIFICATION_ORDER:
            patterns = _STRATEGY_PATTERNS[strategy]
            for pattern in patterns:
                if pattern.search(text):
                    logger.debug(f"[QueryRouter] Classified as '{strategy}': matched {pattern.pattern}")
                    return strategy

        logger.debug(f"[QueryRouter] No pattern matched, using '{STRATEGY_HYBRID}'")
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
