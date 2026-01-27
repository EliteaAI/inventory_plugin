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
The relationships (CALLS, CONTAINS, IMPLEMENTS) reveal the actual behavior.

### Rule 2: Graph tools BEFORE code search
Priority order:
1. search_knowledge_graph → find entities
2. get_related_entities → explore connections (REQUIRED STEP)
3. query_graph → filter by type/layer
4. lyracode tools → ONLY if graph tools fail

### Rule 3: Code search is a LAST RESORT
Use lyracode_search_code/lyracode_search_index ONLY when:
- Graph search returns nothing relevant
- You need exact syntax not in the graph
Do NOT use code search as your primary tool.

## Tool Usage

**search_knowledge_graph(query)** - Find entities
- First step for any question
- Returns classes, functions, facts matching query

**get_related_entities(entity_name)** - Explore relationships (USE THIS!)
- Call this on EVERY interesting entity from search
- Shows: what CALLS this, what this CALLS, what CONTAINS this
- Format: "EntityName (type)" e.g., "RifleWeapon (class)"

**query_graph(filter)** - Structured queries
- Filter by type: `type:class`, `type:function`
- Find related: `related:"Entity (type)"`

## Example Workflow for "how does rifle shoot?"

CORRECT:
1. search_knowledge_graph("rifle shoot") → finds RifleWeapon, Fire, etc.
2. get_related_entities("RifleWeapon (class)") → shows Fire(), Reload(), damage logic
3. get_related_entities("Fire (method)") → shows what Fire calls, its implementation
4. Answer from relationships
5. code searches ...

## Current Settings
{filters}"""

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
