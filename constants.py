#!/usr/bin/python3
# coding=utf-8

"""
Constants for Inventory Chat

Contains system prompts and other constant values used by the inventory chat agent.
"""

# System prompt for the inventory knowledge graph assistant
INVENTORY_CHAT_SYSTEM_PROMPT = """You are an assistant that explores a KNOWLEDGE GRAPH containing pre-extracted code entities and their relationships.

## CRITICAL RULES - READ CAREFULLY

### Rule 1: ALWAYS use get_related_entities after search_knowledge_graph
When you find an entity, you MUST call get_related_entities on it to understand how it works.
The relationships (CALLS, CONTAINS, IMPLEMENTS, EXTENDS) reveal the actual behavior.

### Rule 2: Graph tools BEFORE code search
Priority order:
1. search_knowledge_graph → find entities
2. get_related_entities → explore connections (REQUIRED STEP)
3. query_graph → filter by type/layer
4. Source toolkit tools (search_code, search_index) → ONLY if graph tools fail

### Rule 3: Code search is a LAST RESORT
Use source toolkit code search tools ONLY when:
- Graph search returns nothing relevant
- You need exact syntax not in the graph
Do NOT use code search as your primary tool.

### Rule 4: Use query_pattern for multi-hop tracing
When tracing call chains, dependency paths, or inheritance hierarchies across multiple steps:
1. If you don't know the graph's entity types or relation names, call `get_pattern_vocabulary` first
2. Use query_pattern with Cypher-like syntax: `(Source)-[:calls*1..3]->(?)`
3. Supports typed wildcards: `(?:class)`, named entities: `(UserService)`, both directions: `->` and `<-`
4. Prefer query_pattern over repeated get_related_entities calls for multi-hop exploration
5. Use CHAIN patterns when different relation types are needed per hop:
   `(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)` traces feature→requirement→class
6. Chain patterns avoid multiple separate queries — combine when the path crosses different relation types

## Tool Usage

**search_knowledge_graph(query)** - Find entities by name/token matching
- First step for any question when you know specific names or keywords
- Returns classes, functions, concepts, facts matching query tokens

**semantic_search(query)** - Find entities by concept similarity
- Use when searching by meaning rather than exact names
- Best for: "authentication logic", "error handling", "data validation patterns"
- Returns results ranked by embedding similarity score
- Available only when the graph has embeddings

**get_related_entities(entity_name)** - Explore relationships (USE THIS!)
- Call this on EVERY interesting entity from search
- Shows: what CALLS this, what this CALLS, what CONTAINS this
- Format: "EntityName (type)" e.g., "UserService (class)", "authenticate (function)"

**query_graph(filter)** - Structured queries
- Filter by type: `type:class`, `type:function`
- Find related: `related:"Entity (type)"`

**query_pattern(pattern)** - Multi-hop traversal (single or chain)
- Trace call chains: `(Service)-[:calls*1..3]->(?)`
- Find inheritance: `(?:class)-[:extends]->(Base)`
- Reverse lookup: `(Target)<-[:calls*1..2]-(?)`
- Chain patterns: `(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)` (up to 4 segments)
- If unsure about types/relations, call get_pattern_vocabulary first
- Use chains instead of multiple queries when each hop needs a different relation type

**get_pattern_vocabulary()** - Discover graph schema
- Lists entity types and relation types with counts
- Call before query_pattern if you don't know the vocabulary

## Example Workflow for "how does authentication work?"

CORRECT:
1. search_knowledge_graph("authentication") → finds AuthService, login, etc.
2. get_related_entities("AuthService (class)") → shows login(), validate(), dependencies
3. get_related_entities("login (method)") → shows what login calls, its implementation
4. Answer from relationships
5. Code search only if more detail needed

## Example Workflow for "trace the call chain from Controller to Database"

CORRECT:
1. get_pattern_vocabulary() → learn types: class, function... relations: calls, imports, contains...
2. query_pattern("(Controller)-[:calls*1..4]->(?:class)") → get multi-hop call paths
3. Answer from structured paths

## Example Workflow for "which requirements trace through features to code?"

CORRECT (use chain pattern — different relation per hop):
1. get_pattern_vocabulary() → learn types and relations
2. query_pattern("(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)") → chain: feature→requirement→code
3. Answer from stitched paths

## When to use CHAIN patterns vs SINGLE-segment:
- **Single**: Same relation type across hops → `(A)-[:calls*1..4]->(B)` (one relation, multiple hops)
- **Chain**: Different relation types per hop → `(A)-[:rel1]->(B)-[:rel2]->(C)` (each hop has its own relation)
- **Chain**: Mixed entity types along the path → trace from docs to code via intermediaries
- **Avoid chains** when a single multi-hop pattern suffices — chains are slower due to per-segment execution

## Current Settings
{filters}"""


# =============================================================================
# MODULAR PROMPT SECTIONS — used by PromptBuilder for strategy-specific prompts
# The monolithic INVENTORY_CHAT_SYSTEM_PROMPT above is kept as-is for backward
# compatibility and is used directly by the hybrid (all-tools) strategy.
# =============================================================================

# Base role description — always included in every strategy prompt
PROMPT_BASE = (
    "You are an assistant that explores a KNOWLEDGE GRAPH containing "
    "pre-extracted code entities and their relationships."
)

# Named rules — PromptBuilder picks only the rules relevant to each strategy
PROMPT_RULES = {
    "always_relate": (
        "ALWAYS use get_related_entities after search_knowledge_graph\n"
        "When you find an entity, you MUST call get_related_entities on it to understand how it works.\n"
        "The relationships (CALLS, CONTAINS, IMPLEMENTS, EXTENDS) reveal the actual behavior."
    ),
    "graph_before_code": (
        "Graph tools BEFORE code search\n"
        "Priority order:\n"
        "1. search_knowledge_graph → find entities\n"
        "2. get_related_entities → explore connections (REQUIRED STEP)\n"
        "3. query_graph → filter by type/layer\n"
        "4. Source toolkit tools (search_code, search_index) → ONLY if graph tools fail"
    ),
    "code_last_resort": (
        "Code search is a LAST RESORT\n"
        "Use source toolkit code search tools ONLY when:\n"
        "- Graph search returns nothing relevant\n"
        "- You need exact syntax not in the graph\n"
        "Do NOT use code search as your primary tool."
    ),
    "use_patterns": (
        "Use query_pattern for multi-hop tracing\n"
        "When tracing call chains, dependency paths, or inheritance hierarchies across multiple steps:\n"
        "1. If you don't know the graph's entity types or relation names, call `get_pattern_vocabulary` first\n"
        "2. Use query_pattern with Cypher-like syntax: `(Source)-[:calls*1..3]->(?)`\n"
        "3. Supports typed wildcards: `(?:class)`, named entities: `(UserService)`, both directions: `->` and `<-`\n"
        "4. Prefer query_pattern over repeated get_related_entities calls for multi-hop exploration\n"
        "5. Use CHAIN patterns when different relation types are needed per hop:\n"
        "   `(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)` traces feature→requirement→class\n"
        "6. Chain patterns avoid multiple separate queries — combine when the path crosses different relation types"
    ),
}

# Per-strategy focus statements — sets the tone for a focused prompt
STRATEGY_INTROS = {
    "entity_lookup": (
        "Focus on identifying and understanding specific entities. "
        "Use search_knowledge_graph to find the entity, then get_related_entities "
        "to understand its connections and behavior."
    ),
    "search": (
        "Focus on finding entities matching the user's criteria. "
        "Use search tools to discover relevant entities, then explore their connections if needed."
    ),
    "traversal": (
        "Focus on exploring relationships, call chains, dependency paths, and impact analysis "
        "across the knowledge graph. Use pattern-based traversal for multi-hop exploration."
    ),
    "overview": (
        "Focus on providing high-level information about the graph's contents, "
        "entity types, counts, architecture, and community structure. "
        "When communities are available, use list_communities for an architectural breakdown "
        "into logical groups, then get_community_detail to drill into specific groups."
    ),
}

# Which rules apply to each strategy (order matters — they appear in this order)
STRATEGY_RULE_KEYS = {
    "entity_lookup": ["always_relate"],
    "search": ["graph_before_code", "code_last_resort"],
    "traversal": ["use_patterns", "always_relate"],
    "overview": [],
}

# Per-strategy example workflows
STRATEGY_WORKFLOWS = {
    "entity_lookup": (
        '## Example Workflow for "how does authentication work?"\n\n'
        "CORRECT:\n"
        '1. search_knowledge_graph("authentication") → finds AuthService, login, etc.\n'
        '2. get_related_entities("AuthService (class)") → shows login(), validate(), dependencies\n'
        '3. get_related_entities("login (method)") → shows what login calls, its implementation\n'
        "4. Answer from relationships\n"
        "5. Code search only if more detail needed"
    ),
    "search": (
        '## Example Workflow for "find all classes that handle payments"\n\n'
        "CORRECT:\n"
        '1. search_knowledge_graph("payment") → finds PaymentService, PaymentProcessor, etc.\n'
        '2. get_related_entities("PaymentService (class)") → shows related entities\n'
        "3. Use source toolkit tools only if graph results insufficient"
    ),
    "traversal": (
        '## Example Workflow for "trace the call chain from Controller to Database"\n\n'
        "CORRECT:\n"
        "1. get_pattern_vocabulary() → learn types: class, function... relations: calls, imports, contains...\n"
        '2. query_pattern("(Controller)-[:calls*1..4]->(?:class)") → get multi-hop call paths\n'
        "3. Answer from structured paths\n\n"
        '## Example Workflow for "which requirements trace through features to code?"\n\n'
        "CORRECT (use chain pattern — different relation per hop):\n"
        "1. get_pattern_vocabulary() → learn types and relations\n"
        '2. query_pattern("(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)") → chain: feature→requirement→code\n'
        "3. Answer from stitched paths\n\n"
        "## When to use CHAIN patterns vs SINGLE-segment:\n"
        "- **Single**: Same relation type across hops → `(A)-[:calls*1..4]->(B)` (one relation, multiple hops)\n"
        "- **Chain**: Different relation types per hop → `(A)-[:rel1]->(B)-[:rel2]->(C)` (each hop has its own relation)\n"
        "- **Chain**: Mixed entity types along the path → trace from docs to code via intermediaries\n"
        "- **Avoid chains** when a single multi-hop pattern suffices — chains are slower due to per-segment execution"
    ),
    "overview": (
        '## Example Workflow for "what types of entities are in this codebase?"\n\n'
        "CORRECT:\n"
        "1. list_entity_types() → see all entity types and counts\n"
        '2. query_graph("type:class") → explore specific types if needed\n'
        "3. Answer with summary of graph contents\n\n"
        '## Example Workflow for "give me an architectural breakdown"\n\n'
        "CORRECT:\n"
        "1. list_communities() → get all communities with labels, sizes, key entities\n"
        '2. get_community_detail("community_0") → drill into a specific group for members and stats\n'
        '3. find_entity_community("UserService") → check which group a specific entity belongs to\n'
        '4. search_within_community("community_0", "auth") → find auth-related entities in that group\n'
        "5. Answer with architectural summary based on community structure"
    ),
}


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
    "search_knowledge_graph": "FIND entities by semantic similarity. Use to discover starting points for exploration. Parameters: 'query' (required), 'top_k' (optional, default 20). Returns entities matching your query by name/description. After finding entities, use get_related_entities to explore their connections.",

    "get_related_entities": """TRAVERSE the graph from an entity - THE KEY TO UNDERSTANDING RELATIONSHIPS.

Shows:
- INCOMING: What CALLS/USES/EXTENDS this entity (callers, users, subclasses)
- OUTGOING: What this entity CALLS/USES/EXTENDS (dependencies, base classes)

Use liberally after search_knowledge_graph to understand:
- What types/subclasses exist (via EXTENDS/IMPLEMENTS incoming)
- What depends on this (via CALLS/IMPORTS incoming)
- What this depends on (via CALLS/IMPORTS outgoing)

Supports: 'Name', 'Name (type)', or full 'Name (type) @ source - path' format.""",

    "get_entity_details": "GET full details about a specific entity: all properties, citations, and relationship summary. Use when you need complete information about one entity. Supports 'Name', 'Name (type)', or full format from search results.",

    "query_graph": """FILTER entities or traverse with precision. Two modes:

1. FILTER MODE - Find entities by criteria:
   type:class,function    - Filter by entity types
   layer:code,service     - Filter by layers
   file:*.py              - Filter by file patterns
   name:User              - Filter by name substring

2. TRAVERSE MODE - Find related entities with filters:
   related:"Entity (type)"           - All entities related to this
   related:"Entity" type:class       - Only classes related to this
   related:"Entity" dir:in           - Only incoming relationships

EXAMPLES:
  type:class layer:code              - All code classes
  related:"WeaponBase (class)"       - Everything related to WeaponBase
  related:"WeaponBase" type:class    - Classes that extend/use WeaponBase""",

    "list_entity_types": "LIST all entity types and counts. Use first to understand what's in the graph (classes, functions, facts, etc.).",

    "semantic_search": "FIND entities by CONCEPT, not just name. Use when looking for functionality by meaning "
    "(e.g., 'authentication logic', 'payment processing', 'error handling patterns'). "
    "Uses embedding similarity — finds related entities even without shared keywords. "
    "Parameters: 'query' (required), 'top_k' (optional, default 10). "
    "Use search_knowledge_graph for exact name/type lookups; use semantic_search for concept-level exploration.",

    "query_pattern": """MULTI-HOP graph traversal with Cypher-like pattern syntax. Supports single-segment and multi-segment CHAIN patterns.

SINGLE-SEGMENT SYNTAX: (source)-[:relation*min..max]->(target)
CHAIN SYNTAX: (A)-[:rel1]->(B)-[:rel2]->(C)  (up to 4 segments)

NODES:
  (?)           - Any entity (wildcard)
  (?:class)     - Any entity of type 'class'
  (UserService) - Entity named 'UserService'
  (User:class)  - Entity 'User' of type 'class'

RELATIONS:
  [:calls]       - Exactly 1 hop of type 'calls'
  [:calls*1..3]  - 1 to 3 hops of type 'calls'
  [:*1..3]       - 1 to 3 hops of any relation type
  [:]            - 1 hop of any type

DIRECTION (per segment):
  ->  Forward (outgoing edges)
  <-  Backward (incoming edges)

SINGLE-SEGMENT EXAMPLES:
  (UserService)-[:calls*1..3]->(?)             - What does UserService call within 3 hops?
  (?:class)-[:extends]->(BaseModel)            - What classes extend BaseModel?
  (Controller)<-[:calls*1..3]-(?)              - What calls Controller (up to 3 levels)?

CHAIN EXAMPLES (different relation per hop):
  (?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)  - Trace feature → requirement → code
  (?:user_story)-[:related_to]->(?:feature)-[:related_to]->(?:class)   - Trace user story → feature → code
  (Controller)-[:calls]->(?:class)-[:extends]->(BaseService)           - Controller calls what extends BaseService?

WHEN TO USE CHAINS vs SINGLE:
  Single: same relation type across hops → (A)-[:calls*1..4]->(B)
  Chain:  different relation types per hop → (A)-[:rel1]->(B)-[:rel2]->(C)
  Chain:  tracing across mixed entity domains (docs → code)""",

    "get_pattern_vocabulary": "List all entity types and relation types in the graph with counts. Call BEFORE query_pattern when you don't know exact type or relation names. Returns vocabulary and example patterns you can use.",

    "impact_analysis": "ANALYZE what would break or be affected if an entity changes. "
    "Shows direct dependents, transitive impact chains, and affected layers. "
    "Parameters: 'entity_name' (required) — the entity to analyze. "
    "Returns impact summary with affected entities grouped by impact depth (distance from the starting entity). "
    "Use for: 'what calls this?', 'what depends on this?', 'what would break if I change X?'",

    "list_communities": "LIST detected communities — logical groupings of related entities discovered by graph clustering. "
    "Shows each community's label, size, dominant types, and top centroids (key entities). "
    "Use first to get a high-level architectural breakdown, then drill into specific communities with get_community_detail.",

    "get_community_detail": "GET full details about a specific community: statistics, centroids (key entities), "
    "architectural members, and micro-clusters. "
    "Parameters: 'community_id' (required, e.g. 'community_0'). "
    "Use after list_communities to explore a group in depth.",

    "find_entity_community": "FIND which community a given entity belongs to. "
    "Parameters: 'entity_name' (required). "
    "Use to understand the architectural neighbourhood of a specific entity.",

    "search_within_community": "SEARCH for entities matching a query within a specific community. "
    "Parameters: 'community_id' (required), 'query' (required). "
    "Use to explore members of a community by keyword, scoped to that group only.",
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


# =============================================================================
# ENTITY LOOKUP CONSTANTS
# Used by smart_find_entity for flexible entity matching
# =============================================================================

# Cross-type compatibility for entity lookup
# Maps canonical types to other canonical types they should match
# This is used TOGETHER with TYPE_NORMALIZATION_MAP (which handles synonyms)
# Example: when searching for "class", also accept "concept" (semantic extraction)
CROSS_TYPE_COMPATIBILITY = {
    'class': ['concept', 'entity', 'component', 'module', 'interface'],
    'function': ['method', 'action', 'process'],
    'method': ['function', 'action'],
    'struct': ['entity', 'data_model', 'schema'],
    'enum': ['constant'],
    'variable': ['property', 'constant', 'parameter'],
    'interface': ['class', 'concept', 'component'],
    'component': ['class', 'concept', 'module'],
    'module': ['class', 'concept', 'component'],
    'concept': ['class', 'entity'],
    'entity': ['class', 'concept', 'struct'],
}


def get_compatible_types(requested_type: str) -> set:
    """
    Get all types compatible with the requested type.

    Combines:
    1. The type itself
    2. Types from CROSS_TYPE_COMPATIBILITY
    3. All types that normalize to the same canonical type (from TYPE_NORMALIZATION_MAP)

    Returns a set of compatible type strings (lowercase).
    """
    if not requested_type:
        return set()

    req = requested_type.lower()
    compatible = {req}

    # Add cross-type compatible types
    compatible.update(CROSS_TYPE_COMPATIBILITY.get(req, []))

    # Add all types that normalize to the same canonical type
    # (reverse lookup in TYPE_NORMALIZATION_MAP)
    canonical = TYPE_NORMALIZATION_MAP.get(req, req)
    for input_type, output_type in TYPE_NORMALIZATION_MAP.items():
        if output_type == canonical or output_type == req:
            compatible.add(input_type)
        if input_type == req:
            compatible.add(output_type)

    return compatible

# Comprehensive source file extensions for entity name matching
# Covers all major programming languages and document formats
SOURCE_EXTENSIONS = [
    # C/C++/Objective-C
    '.h', '.hpp', '.hxx', '.h++', '.hh',
    '.c', '.cpp', '.cc', '.cxx', '.c++',
    '.m', '.mm',  # Objective-C
    # Python
    '.py', '.pyi', '.pyw', '.pyx',
    # JavaScript/TypeScript
    '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',
    # Java/Kotlin/Scala
    '.java', '.kt', '.kts', '.scala', '.sc',
    # C#/F#/VB.NET
    '.cs', '.fs', '.fsx', '.vb',
    # Go
    '.go',
    # Rust
    '.rs',
    # Ruby
    '.rb', '.rake', '.gemspec',
    # PHP
    '.php', '.phtml', '.php3', '.php4', '.php5',
    # Swift
    '.swift',
    # COBOL
    '.cob', '.cbl', '.cpy', '.cobol',
    # Fortran
    '.f', '.for', '.f90', '.f95', '.f03', '.f08',
    # Ada
    '.adb', '.ads', '.ada',
    # Pascal/Delphi
    '.pas', '.pp', '.dpr', '.dpk',
    # BASIC/VBA
    '.bas', '.vbs', '.cls', '.frm',
    # PL/SQL, SQL
    '.sql', '.pls', '.plb', '.pck', '.pkb', '.pks',
    # Shell/Scripting
    '.sh', '.bash', '.zsh', '.ksh', '.csh',
    '.ps1', '.psm1', '.psd1',  # PowerShell
    '.bat', '.cmd',  # Windows batch
    # Perl
    '.pl', '.pm', '.pod', '.t',
    # Lua
    '.lua',
    # R
    '.r', '.R', '.rmd',
    # Julia
    '.jl',
    # Haskell
    '.hs', '.lhs',
    # Erlang/Elixir
    '.erl', '.hrl', '.ex', '.exs',
    # Clojure
    '.clj', '.cljs', '.cljc', '.edn',
    # Lisp/Scheme
    '.lisp', '.lsp', '.cl', '.scm', '.ss', '.rkt',
    # Groovy
    '.groovy', '.gvy', '.gy', '.gsh',
    # D
    '.d',
    # Nim
    '.nim',
    # OCaml
    '.ml', '.mli',
    # Prolog
    '.pro', '.P',
    # Assembly
    '.asm', '.s', '.S',
    # ABAP (SAP)
    '.abap',
    # RPG (IBM)
    '.rpg', '.rpgle', '.sqlrpgle',
    # JCL (Mainframe)
    '.jcl',
    # REXX
    '.rexx', '.rex',
    # Terraform/IaC
    '.tf', '.tfvars',
    # YAML/Config
    '.yaml', '.yml',
    # Web
    '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.vue', '.svelte',
    # Markup/Docs
    '.md', '.rst', '.txt', '.adoc', '.tex', '.latex',
    # Data
    '.json', '.xml', '.toml', '.ini', '.cfg', '.conf',
    # Office Documents
    '.pdf',
    '.doc', '.docx', '.docm',  # Word
    '.xls', '.xlsx', '.xlsm', '.xlsb',  # Excel
    '.ppt', '.pptx', '.pptm',  # PowerPoint
    '.odt', '.ods', '.odp', '.odg',  # OpenDocument
    '.rtf',
    # Other document formats
    '.epub', '.mobi',  # eBooks
    '.pages', '.numbers', '.key',  # Apple iWork
    '.csv', '.tsv',  # Tabular data
]


# =============================================================================
# TYPE NORMALIZATION CONSTANTS
# Used by KnowledgeGraph for entity type consolidation
# =============================================================================

# Comprehensive type consolidation map
# Maps many ad-hoc LLM types to a smaller set of canonical types
# NOTE: All keys should be lowercase - normalize_type() lowercases input first
TYPE_NORMALIZATION_MAP = {
    # ==========================================================================
    # IDENTITY MAPPINGS - Types that MUST be preserved as-is
    # ==========================================================================
    "fact": "fact", "source_file": "source_file", "feature": "feature",
    "module": "module", "constant": "constant", "rule": "rule",
    "parameter": "parameter", "error_handling": "error_handling",
    "todo": "todo", "property": "property", "configuration": "configuration",
    "process": "process", "integration": "integration", "interface": "interface",
    "user_story": "user_story", "test": "test", "variable": "variable",
    "function": "function", "class": "class", "method": "method",

    # ==========================================================================
    # CODE STRUCTURE FAMILY
    # ==========================================================================
    "named": "export", "default": "export", "business_rule": "rule",
    "domain_concept": "concept", "business_concept": "concept",
    "integration_point": "integration", "user_interface_element": "interface",
    "user_interface_component": "interface", "user_interaction": "interface",
    "user_action": "interface", "api_contract": "rest_api",
    "technical_debt": "todo", "test_scenario": "test", "test_case": "test",
    "acceptance_criteria": "test", "acceptance_criterion": "test",
    "tooltype": "tool",

    # ==========================================================================
    # TOOL & TOOLKIT FAMILY
    # ==========================================================================
    "tool": "tool", "tools": "tool", "tool_used": "tool",
    "tool_example": "tool", "tool_category": "tool", "internal_tool": "tool",
    "documentationtool": "tool", "toolkit": "toolkit", "toolkits": "toolkit",
    "toolkit_type": "toolkit",

    # ==========================================================================
    # FEATURE & CAPABILITY FAMILY
    # ==========================================================================
    "features": "feature", "functionality": "feature", "capability": "feature",
    "benefit": "feature", "characteristic": "feature",

    # ==========================================================================
    # PROCESS & WORKFLOW FAMILY
    # ==========================================================================
    "processes": "process", "procedure": "process", "workflow": "workflow",
    "flow": "process", "pipeline": "process",

    # ==========================================================================
    # CONCEPT & ENTITY FAMILY
    # ==========================================================================
    "concept": "concept", "concepts": "concept", "entity": "entity",
    "entities": "entity", "entity_type": "entity", "entitytype": "entity",
    "domain_entity": "entity", "domain": "concept", "topic": "concept",
    "term": "concept", "glossary_term": "concept", "key_concept": "concept",

    # ==========================================================================
    # CONFIGURATION FAMILY
    # ==========================================================================
    "config": "configuration", "configuration_section": "configuration",
    "configuration_field": "configuration", "configuration_option": "configuration",
    "configuration_file": "configuration", "configurationfile": "configuration",
    "configurationchange": "configuration", "configuration_command": "configuration",
    "setting": "configuration", "environment": "configuration",

    # ==========================================================================
    # DOCUMENTATION & GUIDE FAMILY
    # ==========================================================================
    "documentation": "documentation", "documentation_section": "documentation",
    "documentation_template": "documentation", "guide": "documentation",
    "guideline": "documentation", "instruction": "documentation",
    "tip": "documentation", "note": "documentation", "faq": "documentation",
    "overview": "documentation", "summary": "documentation",
    "best_practice": "documentation",

    # ==========================================================================
    # SECTION & STRUCTURE FAMILY
    # ==========================================================================
    "section": "section", "sections": "section", "interface_section": "section",
    "navigation_structure": "section", "navigation_group": "section",
    "navigation": "section",

    # ==========================================================================
    # COMPONENT & UI FAMILY
    # ==========================================================================
    "component": "component", "components": "component",
    "ui_component": "component", "ui_element": "component",
    "ui_layout": "component", "interface_element": "component",
    "button": "component", "menu": "component", "tab": "component",
    "panel": "component", "editor": "component", "view": "component",

    # ==========================================================================
    # ISSUE & PROBLEM FAMILY
    # ==========================================================================
    "issue": "issue", "issues": "issue", "issue_type": "issue",
    "issuetype": "issue", "known_issue": "issue", "fixed_issue": "issue",
    "limitation": "issue", "challenge": "issue", "problem": "issue",
    "error_message": "issue", "troubleshooting": "issue",
    "compatibilityissue": "issue",

    # ==========================================================================
    # ACTION & COMMAND FAMILY
    # ==========================================================================
    "action": "action", "actions": "action", "command": "action",
    "operation": "action", "task": "action", "trigger": "action",
    "automation_rule": "action",

    # ==========================================================================
    # PARAMETER & FIELD FAMILY
    # ==========================================================================
    "parameters": "parameter", "field": "parameter",
    "field_identifier": "parameter", "placeholder": "parameter",
    "value": "parameter", "label": "parameter", "tag": "parameter",

    # ==========================================================================
    # CREDENTIAL & AUTH FAMILY
    # ==========================================================================
    "credential": "credential", "credential_type": "credential",
    "secret": "credential", "token": "credential", "api_key": "credential",
    "api_token": "credential", "key": "credential",
    "authentication": "credential", "authentication_method": "credential",
    "permission": "credential", "access_control": "credential",
    "access_requirement": "credential",

    # ==========================================================================
    # RESOURCE & FILE FAMILY
    # ==========================================================================
    "resource": "resource", "resources": "resource", "file": "resource",
    "file_type": "resource", "file_format": "resource", "file_path": "resource",
    "folder": "resource", "artifact": "resource", "artifact_type": "resource",
    "document": "resource", "template": "resource", "script": "resource",

    # ==========================================================================
    # PLATFORM & SOFTWARE FAMILY
    # ==========================================================================
    "platform": "platform", "platforms": "platform", "software": "platform",
    "softwareversion": "platform", "application": "platform", "app": "platform",
    "system": "platform", "framework": "platform", "library": "platform",
    "technology": "platform", "product": "platform",

    # ==========================================================================
    # SERVICE & API FAMILY - Keep distinct types for different patterns
    # ==========================================================================
    "service": "service", "services": "service", "microservice": "service",
    "web_service": "service", "server": "service", "client": "service",
    "hostingservice": "service",

    # REST API
    "rest api": "rest_api", "rest_api": "rest_api", "restapi": "rest_api",
    "rest": "rest_api", "api": "rest_api", "openapi": "rest_api",
    "swagger": "rest_api", "apis": "rest_api",
    "rest endpoint": "rest_endpoint", "rest_endpoint": "rest_endpoint",
    "endpoint": "rest_endpoint", "api_endpoint": "rest_endpoint",
    "http_endpoint": "rest_endpoint", "endpoints": "rest_endpoint",
    "rest_resource": "rest_resource",

    # GraphQL
    "graphql api": "graphql_api", "graphql_api": "graphql_api",
    "graphql": "graphql_api", "graphql_schema": "graphql_api",
    "graphql query": "graphql_query", "graphql_query": "graphql_query",
    "query": "graphql_query",
    "graphql mutation": "graphql_mutation", "graphql_mutation": "graphql_mutation",
    "mutation": "graphql_mutation",
    "graphql subscription": "graphql_subscription",
    "graphql_subscription": "graphql_subscription", "subscription": "graphql_subscription",
    "graphql type": "graphql_type", "graphql_type": "graphql_type",

    # gRPC
    "grpc service": "grpc_service", "grpc_service": "grpc_service",
    "grpc": "grpc_service",
    "grpc method": "grpc_method", "grpc_method": "grpc_method",
    "rpc_method": "grpc_method",
    "protobuf_message": "protobuf_message", "protobuf": "protobuf_message",
    "proto_message": "protobuf_message", "protocol buffer": "protobuf_message",

    # Event-Driven Architecture
    "event bus": "event_bus", "event_bus": "event_bus",
    "message_broker": "event_bus", "message_queue": "event_bus",
    "kafka": "event_bus", "rabbitmq": "event_bus",
    "event type": "event_type", "event_type": "event_type",
    "event": "event_type", "message_type": "event_type",
    "event producer": "event_producer", "event_producer": "event_producer",
    "publisher": "event_producer",
    "event consumer": "event_consumer", "event_consumer": "event_consumer",
    "subscriber": "event_consumer", "listener": "event_consumer",
    "event handler": "event_handler", "event_handler": "event_handler",
    "message_handler": "event_handler", "handler": "event_handler",

    # ==========================================================================
    # INTEGRATION & CONNECTION FAMILY
    # ==========================================================================
    "integrations": "integration", "connection": "integration",
    "connection_type": "integration", "connector": "integration",
    "adapter": "integration", "datasource": "integration",
    "database": "database",

    # ==========================================================================
    # EXAMPLE & USE CASE FAMILY
    # ==========================================================================
    "example": "example", "examples": "example", "example_type": "example",
    "example_request": "example", "use_case": "example",
    "use_case_category": "example", "code_sample": "example",
    "sample_prompt": "example",

    # ==========================================================================
    # NODE & GRAPH FAMILY
    # ==========================================================================
    "node": "node", "nodetype": "node", "node_type": "node",
    "execution_node": "node", "iteration_node": "node",
    "interaction_node": "node", "utilitynode": "node",

    # ==========================================================================
    # STEP & PROCEDURE FAMILY
    # ==========================================================================
    "step": "step", "steps": "step", "number_of_step": "step",
    "prerequisite": "step",

    # ==========================================================================
    # STATUS & STATE FAMILY
    # ==========================================================================
    "status": "status", "state": "status", "state_type": "status",
    "mode": "status", "session_mode": "status",

    # ==========================================================================
    # PROJECT & WORKSPACE FAMILY
    # ==========================================================================
    "project": "project", "workspace": "project", "project_scope": "project",
    "repository": "project", "space": "project",

    # ==========================================================================
    # ROLE & USER FAMILY
    # ==========================================================================
    "role": "role", "user_role": "role", "team": "role", "person": "role",
    "audience": "role", "stakeholder": "role", "owner": "role",

    # ==========================================================================
    # AGENT FAMILY
    # ==========================================================================
    "agent": "agent", "agents": "agent", "agent_type": "agent",
    "agent_configuration": "agent", "ai_agent": "agent", "public_agent": "agent",

    # ==========================================================================
    # DATA & TYPE FAMILY
    # ==========================================================================
    "data_type": "data_type", "datatype": "data_type",
    "data_structure": "data_type", "schema": "schema", "format": "data_type",
    "content_type": "data_type", "collection": "data_type",
    "collectiontype": "data_type", "list": "data_type", "table": "table",
    "tables": "table", "schemas": "schema",

    # ==========================================================================
    # RELEASE & VERSION FAMILY
    # ==========================================================================
    "release": "release", "version": "release", "change": "release",
    "feature_change": "release", "migration": "release",
    "deployment": "release", "fix": "release",

    # ==========================================================================
    # REFERENCE & LINK FAMILY
    # ==========================================================================
    "reference": "reference", "related_page": "reference", "url": "reference",
    "webpage": "reference", "website": "reference", "page": "reference",
    "link": "reference",

    # ==========================================================================
    # RULE & POLICY FAMILY
    # ==========================================================================
    "rules": "rule", "policy": "rule", "formatting_rule": "rule",
    "directive": "rule", "requirement": "requirement",
    "requirements": "requirement", "specification": "rule",

    # ==========================================================================
    # MCP FAMILY
    # ==========================================================================
    "mcp server": "mcp_server", "mcp_server": "mcp_server",
    "mcp tool": "mcp_tool", "mcp_tool": "mcp_tool",
    "mcp resource": "mcp_resource", "mcp_resource": "mcp_resource",
    "mcp_type": "mcp_server", "transport": "mcp_server",

    # ==========================================================================
    # MISCELLANEOUS
    # ==========================================================================
    "model": "concept", "category": "concept", "metric": "parameter",
    "identifier": "parameter", "port": "parameter", "protocol": "service",
    "security": "credential", "support": "documentation",
    "community": "documentation", "contact": "reference",
    "contactmethod": "reference", "contact_information": "reference",
    "contactinfo": "reference", "building_block": "component",
    "container": "component", "instance": "entity", "object": "entity",
    "sourcetype": "data_type", "input_mapping_type": "data_type",
    "control_flow_feature": "feature", "export_option": "action",
    "export_format": "data_type", "conversion": "action",
    "customization": "configuration", "viewing_option": "configuration",
    "review_outcome": "status", "goal": "feature", "engagement": "action",
    "output": "data_type", "effect": "action", "solution": "documentation",
    "cause": "issue", "indicator": "status", "date": "parameter",
    "screenshot": "resource", "open_question": "issue",
    "static_site_generator": "platform", "theme": "configuration",
    "theme_convention": "rule", "file_naming_convention": "rule",
    "metadata_guideline": "rule", "linking_guideline": "rule",
    "media_guideline": "rule", "accessibility_guideline": "rule",
    "page_type": "section", "document_category": "section",
    "prompt": "example", "chat": "feature", "ide": "platform",
    "tagging": "action", "account": "credential",
    "installation_command": "action", "usage": "documentation",
    "mechanism": "concept", "ai_component": "component",
    "communication_method": "integration", "dns_record": "configuration",
    "tone": "rule", "voice": "rule",

    # ==========================================================================
    # FACT & KNOWLEDGE FAMILY
    # ==========================================================================
    "facts": "fact", "algorithm": "fact", "behavior": "fact",
    "validation": "fact", "decision": "fact", "definition": "fact",

    # ==========================================================================
    # FILE & STRUCTURE FAMILY
    # ==========================================================================
    "document_file": "document_file", "config_file": "config_file",
    "web_file": "web_file", "directory": "directory", "package": "package",

    # ==========================================================================
    # CASE VARIATIONS (explicit mappings)
    # ==========================================================================
    "Tool": "tool", "Feature": "feature", "API": "rest_api",
    "Service": "service", "Endpoint": "rest_endpoint", "Class": "class",
    "Function": "function", "Module": "module", "Interface": "interface",
    "Component": "component", "Toolkit": "toolkit",
    "MCP Server": "mcp_server", "MCP Tool": "mcp_tool",
    "Test Case": "test", "User Story": "user_story",
    "UI Component": "component", "Pull Request": "pull_request",
    "Business Rule": "rule",
}

# ==========================================================================
# TYPE PRIORITY - Higher priority types are more specific
# Used when deciding which type to keep during merges
# ==========================================================================
TYPE_PRIORITY = {
    # Code layer - highest priority (most specific)
    "class": 100, "function": 99, "method": 98, "module": 97,
    "interface": 96, "constant": 95, "variable": 94, "configuration": 93,

    # Service layer - specific communication patterns
    "service": 90,
    "rest_api": 89, "rest_endpoint": 88, "rest_resource": 87,
    "graphql_api": 89, "graphql_mutation": 88, "graphql_query": 87,
    "graphql_subscription": 86, "graphql_type": 85,
    "grpc_service": 89, "grpc_method": 88, "protobuf_message": 87,
    "event_bus": 89, "event_type": 88, "event_producer": 87,
    "event_consumer": 87, "event_handler": 86,
    "integration": 84, "payload": 83,

    # Data layer
    "database": 85, "table": 84, "column": 83, "constraint": 82,
    "index": 81, "migration": 80, "enum": 79, "schema": 78,

    # Product layer
    "feature": 75, "epic": 74, "user_story": 73, "screen": 72,
    "ux_flow": 71, "ui_component": 70, "ui_field": 69,

    # Domain layer - specific subtypes get higher priority than generic categories
    "error_handling": 70, "requirement": 69,  # Specific subtypes
    "rule": 65, "business_rule": 64,           # Generic rule categories
    "domain_entity": 63, "attribute": 62,
    "business_event": 61, "glossary_term": 60, "workflow": 59,

    # Testing layer
    "test_suite": 55, "test_case": 54, "test_step": 53, "test": 52,
    "assertion": 52, "test_data": 51, "defect": 50, "incident": 49,

    # Delivery layer
    "release": 45, "sprint": 44, "commit": 43, "pull_request": 42,
    "ticket": 41, "deployment": 40,

    # Organization layer
    "team": 35, "owner": 34, "stakeholder": 33, "repository": 32,
    "documentation": 31,

    # Toolkits
    "toolkit": 28, "source_toolkit": 27, "tool": 25, "command": 24,

    # Generic types - lowest priority
    "concept": 15, "entity": 14, "component": 13, "object": 12,
    "item": 11, "element": 10, "fact": 8, "thing": 5, "unknown": 1,
}

# ==========================================================================
# NEVER_DEDUPLICATE_TYPES - Types that should NEVER be merged
# These are context-dependent - same name in different files means different things
# ==========================================================================
NEVER_DEDUPLICATE_TYPES = {
    "tool",           # Tools belong to specific toolkits (e.g., "Get Tests" in Xray ≠ Zephyr)
    "property",       # Properties belong to specific entities
    "parameter",      # Parameters belong to specific functions/methods
    "argument",       # Arguments belong to specific functions
    "field",          # Fields belong to specific tables/forms
    "column",         # Columns belong to specific tables
    "attribute",      # Attributes belong to specific entities
    "option",         # Options belong to specific settings
    "setting",        # Settings may have same name in different contexts
    "step",           # Steps belong to specific workflows/processes
    "test_step",      # Test steps belong to specific test cases
    "ui_field",       # UI fields belong to specific screens
    "method",         # Methods belong to specific classes

    # API types - same name can exist in different API contexts
    "rest_endpoint",       # /users in API A ≠ /users in API B
    "rest_resource",       # Same resource in different REST APIs
    "graphql_query",       # Same query in different GraphQL schemas
    "graphql_mutation",    # Same mutation in different GraphQL schemas
    "graphql_subscription",# Same subscription in different GraphQL schemas
    "graphql_type",        # Same type in different GraphQL schemas
    "grpc_method",         # Same method in different gRPC services
    "protobuf_message",    # Same message in different proto files
    "event_type",          # Same event in different event buses
    "event_handler",       # Same handler in different services
}

# Common word prefixes for splitting joined types like "businessrule" → "business_rule"
# These must be full word prefixes that commonly appear in compound type names
KNOWN_TYPE_PREFIXES = [
    "business", "domain", "api", "integration", "error", "test", "user",
    "workflow", "configuration", "config", "technical", "acceptance",
    "procedure", "glossary", "data", "service", "feature", "requirement",
    "validation", "security", "access", "root", "design", "best", "known",
    "resource", "permission", "state", "support", "release", "migration",
    "bug", "entry", "contact", "stakeholder", "guide",
]

# Known suffixes that indicate a valid word boundary
KNOWN_TYPE_SUFFIXES = [
    "rule", "concept", "entity", "point", "contract", "term", "case", "step",
    "scenario", "criteria", "debt", "note", "handling", "policy", "behavior",
    "requirement", "integration", "component", "element", "practice", "issue",
    "condition", "tool", "action", "field", "param", "spec", "ref", "fix",
]

# ==========================================================================
# SUFFIX-BASED TYPE NORMALIZATION
# Maps compound types like "validation_rule" → "rule" based on suffix
# ==========================================================================
TYPE_SUFFIX_NORMALIZATION = {
    # Core type suffixes - map to canonical types
    "_rule": "rule",
    "_rules": "rule",
    "_requirement": "requirement",
    "_requirements": "requirement",
    "_step": "step",
    "_steps": "step",
    "_parameter": "parameter",
    "_parameters": "parameter",
    "_param": "parameter",
    "_params": "parameter",
    "_field": "parameter",
    "_fields": "parameter",
    "_attribute": "parameter",
    "_attributes": "parameter",
    "_property": "property",
    "_properties": "property",

    # Behavior/fact suffixes
    "_behavior": "fact",
    "_behaviour": "fact",
    "_behaviors": "fact",
    "_behaviours": "fact",
    "_handling": "error_handling",

    # Documentation suffixes
    "_guideline": "documentation",
    "_guidelines": "documentation",
    "_guide": "documentation",
    "_guides": "documentation",
    "_note": "documentation",
    "_notes": "documentation",
    "_documentation": "documentation",
    "_doc": "documentation",
    "_docs": "documentation",
    "_reference": "reference",
    "_references": "reference",
    "_tip": "documentation",
    "_tips": "documentation",

    # Example/sample suffixes
    "_example": "example",
    "_examples": "example",
    "_sample": "example",
    "_samples": "example",
    "_template": "example",
    "_templates": "example",

    # Process/workflow suffixes
    "_procedure": "process",
    "_procedures": "process",
    "_process": "process",
    "_processes": "process",
    "_workflow": "workflow",
    "_workflows": "workflow",
    "_flow": "workflow",
    "_flows": "workflow",
    "_pipeline": "process",

    # Integration/API suffixes
    "_integration": "integration",
    "_integrations": "integration",
    "_contract": "rest_api",
    "_contracts": "rest_api",
    "_endpoint": "rest_endpoint",
    "_endpoints": "rest_endpoint",
    "_api": "rest_api",
    "_apis": "rest_api",

    # Configuration suffixes
    "_configuration": "configuration",
    "_configurations": "configuration",
    "_config": "configuration",
    "_configs": "configuration",
    "_setting": "configuration",
    "_settings": "configuration",
    "_option": "configuration",
    "_options": "configuration",

    # Feature/capability suffixes
    "_feature": "feature",
    "_features": "feature",
    "_capability": "feature",
    "_capabilities": "feature",

    # Action/operation suffixes
    "_action": "action",
    "_actions": "action",
    "_operation": "action",
    "_operations": "action",
    "_command": "action",
    "_commands": "action",

    # Resource suffixes
    "_resource": "resource",
    "_resources": "resource",
    "_artifact": "resource",
    "_artifacts": "resource",
    "_asset": "resource",
    "_assets": "resource",

    # Testing suffixes
    "_test": "test",
    "_tests": "test",
    "_scenario": "test",
    "_scenarios": "test",

    # Issue/problem suffixes
    "_issue": "issue",
    "_issues": "issue",
    "_problem": "issue",
    "_problems": "issue",
    "_error": "error_handling",
    "_errors": "error_handling",

    # Schema/model suffixes
    "_schema": "schema",
    "_schemas": "schema",
    "_model": "concept",
    "_models": "concept",
    "_data": "data_type",

    # Concept/entity suffixes
    "_concept": "concept",
    "_concepts": "concept",
    "_entity": "entity",
    "_entities": "entity",
    "_term": "concept",
    "_terms": "concept",

    # Policy/constraint suffixes
    "_policy": "rule",
    "_policies": "rule",
    "_constraint": "rule",
    "_constraints": "rule",
    "_specification": "rule",
    "_specifications": "rule",
    "_spec": "rule",
    "_specs": "rule",

    # Credential/security suffixes
    "_credential": "credential",
    "_credentials": "credential",
    "_permission": "credential",
    "_permissions": "credential",

    # Tool suffixes
    "_tool": "tool",
    "_tools": "tool",
    "_toolkit": "toolkit",
    "_toolkits": "toolkit",

    # Component/element suffixes
    "_component": "component",
    "_components": "component",
    "_element": "component",
    "_elements": "component",

    # Pattern/practice suffixes
    "_pattern": "concept",
    "_patterns": "concept",
    "_practice": "documentation",
    "_practices": "documentation",

    # Node/graph suffixes
    "_node": "node",
    "_nodes": "node",

    # Service suffixes
    "_service": "service",
    "_services": "service",

    # Status/state suffixes
    "_status": "status",
    "_state": "status",
    "_states": "status",

    # Metric/measurement suffixes
    "_metric": "parameter",
    "_metrics": "parameter",
    "_measurement": "parameter",

    # Message/notification suffixes
    "_message": "fact",
    "_messages": "fact",
    "_notification": "action",
    "_notifications": "action",

    # Detail/info suffixes (typically become facts)
    "_detail": "fact",
    "_details": "fact",
    "_info": "fact",
    "_information": "fact",
}


# ==========================================================================
# CANONICAL_TYPES - Target types for smart (LLM-based) normalization
# These are the ~50 core types that all entity types should map to
# ==========================================================================
CANONICAL_TYPES = [
    # Code layer
    "class", "function", "method", "module", "interface", "constant", "variable",
    "import", "export", "enum", "property",
    # Service layer
    "service", "rest_api", "rest_endpoint", "integration", "event_type",
    # Data layer
    "database", "table", "schema", "data_type",
    # Product layer
    "feature", "user_story", "epic", "component",
    # Domain layer
    "rule", "requirement", "workflow", "process", "concept", "entity",
    # Testing layer
    "test", "test_suite", "defect",
    # Documentation layer
    "documentation", "example", "reference",
    # Configuration layer
    "configuration", "credential",
    # Organization layer
    "role", "project",
    # Generic
    "fact", "action", "status", "resource", "tool", "toolkit",
    "todo", "issue", "error_handling", "step", "node", "parameter",
    "source_file", "document_file",
]


# ==========================================================================
# SIGNIFICANT_ENTITY_TYPES - Types used for cross-source mention detection
# These entity types are considered "significant" enough to search for
# mentions across different sources (UI, backend, docs, etc.)
# ==========================================================================
SIGNIFICANT_ENTITY_TYPES = {
    # Code layer
    'class', 'function', 'method', 'module', 'interface',
    # Service layer
    'service', 'api', 'endpoint', 'rest_api', 'rest_endpoint',
    # Data layer
    'schema', 'table', 'data_type',
    # Product layer
    'feature', 'epic', 'component',
    # Domain layer (expanded for docs cross-linking)
    'requirement', 'rule', 'process', 'workflow', 'concept', 'entity',
    # Generic (expanded for docs cross-linking)
    'fact', 'action', 'step', 'configuration',
}
