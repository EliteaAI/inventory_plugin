#!/usr/bin/python3
# coding=utf-8

"""
Constants for Inventory Chat

Contains system prompts and other constant values used by the inventory chat agent.
"""

# System prompt for the inventory knowledge graph assistant
INVENTORY_CHAT_SYSTEM_PROMPT = """You are an intelligent assistant that helps users explore and understand a knowledge graph of code, documentation, and other software artifacts.

You have access to tools that let you search the knowledge graph, get entity details, and explore relationships.

When answering questions:
1. First search the knowledge graph to find relevant entities using search_knowledge_graph
2. Get details for the most relevant entities using get_entity_details
3. Explore relationships using get_related_entities if needed to understand connections
4. Synthesize the information into a clear, helpful answer
5. Always cite your sources by mentioning the entity names and their source locations

Current search settings:
{filters}

IMPORTANT: When using search_knowledge_graph, pass the input as JSON with 'query' and optionally 'top_k' to limit results. Example: {{"query": "authentication", "top_k": 20}}
The depth setting indicates how deeply to explore relationships - use get_related_entities multiple times (up to the depth level) to traverse the graph.

Answer the user's question using the tools available. Be thorough but concise."""

# ReAct agent format template
REACT_FORMAT_TEMPLATE = """

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Question: {input}
Thought:{agent_scratchpad}"""

# Tool descriptions
TOOL_DESCRIPTIONS = {
    "search_knowledge_graph": "Search the knowledge graph for entities (code, documentation, etc.) matching a query. Input should be a JSON object with: 'query' (required string), 'top_k' (optional int, default from Max Nodes setting). Example: {\"query\": \"authentication\", \"top_k\": 20}. Or just pass a plain text query string.",
    "get_entity_details": "Get detailed information about a specific entity by name. Use this after search to get full details including source code content.",
    "get_related_entities": "Get entities that are related to a specific entity (dependencies, callers, etc.). Use this to understand how components are connected. Call multiple times following the Depth setting to traverse the graph deeper.",
    "list_entity_types": "List all entity types (class, function, module, etc.) and their counts in the knowledge graph. Use this to understand what's available.",
}

# Read-only tool patterns for filtering source toolkit tools
READ_ONLY_PREFIXES = (
    'get_', 'list_', 'search_', 'read_', 'fetch_', 'find_',
    'query_', 'describe_', 'show_', 'view_', 'retrieve_',
    'check_', 'verify_', 'validate_', 'lookup_', 'browse_',
)

WRITE_OPERATION_PATTERNS = (
    'create', 'update', 'delete', 'write', 'post', 'put',
    'remove', 'add_', 'set_', 'modify', 'edit', 'insert',
    'patch', 'upload', 'send_', 'submit', 'publish', 'assign',
    'unassign', 'approve', 'reject', 'close_', 'open_', 'merge',
    'fork', 'clone', 'push', 'commit', 'branch', 'tag',
)

# Agent configuration defaults
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_LLM_TEMPERATURE = 0.1
DEFAULT_LLM_MAX_TOKENS = 4096
DEFAULT_TOOL_LLM_MAX_TOKENS = 1024
