#!/usr/bin/python3
# coding=utf-8

"""
Inventory Chat Worker Implementation

Standalone module containing chat execution logic that runs inside worker processes.
This module is imported by worker processes and executes with its own event loop.
"""

import json
import os
import uuid
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

# Note: This module runs in worker processes - imports must be available there


def execute_inventory_chat(
    alita_client,
    project_id: int,
    toolkit_id: int,
    prompt: str,
    filters: Dict[str, Any] = None,
    conversation_id: str = None,
    history: List[Dict] = None,
    model: str = None,
    user_id: str = None,
    emit_fn: Callable = None,
) -> Dict[str, Any]:
    """
    Execute inventory chat in a worker process.

    This is a simplified version of the inventory_chat method that can run
    independently in a worker process with its own event loop.

    Args:
        alita_client: AlitaClient instance
        project_id: Project ID
        toolkit_id: Toolkit ID
        prompt: User prompt
        filters: Optional filters
        conversation_id: Optional conversation ID
        history: Optional chat history
        model: Optional model override
        user_id: Optional user ID
        emit_fn: Optional streaming callback

    Returns:
        Dict with answer, citations, tool_calls, touched_entities, error
    """
    import requests as http_requests
    from pylon.core.tools import log

    session_id = str(uuid.uuid4())
    filters = filters or {}
    history = history or []
    emit_fn = emit_fn or (lambda t, d: None)

    log.info(f"[chat_worker] Starting chat session {session_id}")
    log.info(f"[chat_worker] project_id={project_id}, toolkit_id={toolkit_id}, model={model}")

    # Track entities accessed during execution
    touched_entities = []

    try:
        # 1. Fetch inventory toolkit settings
        toolkit_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}"
        resp = http_requests.get(toolkit_url, headers=alita_client.headers, verify=False)

        if not resp.ok:
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "touched_entities": [],
                "error": f"Failed to fetch toolkit settings: {resp.status_code}",
            }

        toolkit_data = resp.json()
        settings = toolkit_data.get("settings", {})
        toolkit_name = toolkit_data.get("name", f"inventory-{toolkit_id}")

        # 2. Get LLM model
        llm_model = model or (
            settings.get("toolkit_configuration_llm_model") or
            settings.get("llm_model") or
            "gpt-4o-mini"
        )
        log.info(f"[chat_worker] Using LLM model: {llm_model}")

        # 3. Get graph path
        graph_path = f"/data/graphs/{project_id}/{toolkit_id}/graph.json"

        # 4. Build tools
        tools = _build_tools(
            project_id=project_id,
            toolkit_id=toolkit_id,
            graph_path=graph_path,
            filters=filters,
            settings=settings,
            alita_client=alita_client,
            touched_entities=touched_entities,
        )

        if not tools:
            return {
                "answer": "No tools available. Please ensure the knowledge graph has been ingested.",
                "citations": [],
                "tool_calls": [],
                "touched_entities": [],
                "error": None,
            }

        log.info(f"[chat_worker] Built {len(tools)} tools")

        # 5. Get LLM with extended thinking for supported models
        model_config = {
            "temperature": 0.0,
            "max_tokens": 4096,
        }

        llm_model_lower = llm_model.lower()
        if "claude" in llm_model_lower and ("sonnet" in llm_model_lower or "opus" in llm_model_lower):
            log.info(f"[chat_worker] Enabling extended thinking for {llm_model}")
            model_config["thinking"] = {
                "type": "enabled",
                "budget_tokens": 8000,
            }
            model_config["temperature"] = 1.0

        llm = alita_client.get_llm(
            model_name=llm_model,
            model_config=model_config,
        )

        # 6. Execute agent
        result = _execute_agent(
            llm=llm,
            tools=tools,
            prompt=prompt,
            history=history,
            filters=filters,
            emit_fn=emit_fn,
        )

        result["touched_entities"] = touched_entities
        log.info(f"[chat_worker] Touched {len(touched_entities)} entities")

        emit_fn("chat_complete", {
            "answer_length": len(result.get("answer", "")),
            "citations_count": len(result.get("citations", [])),
            "touched_entities_count": len(touched_entities),
        })

        return result

    except Exception as e:
        log.exception(f"[chat_worker] Error: {e}")
        error_msg = str(e)

        emit_fn("chat_error", {"error": error_msg})

        return {
            "answer": "",
            "citations": [],
            "tool_calls": [],
            "touched_entities": [],
            "error": error_msg,
        }


def _build_tools(
    project_id: int,
    toolkit_id: int,
    graph_path: str,
    filters: Dict[str, Any],
    settings: Dict[str, Any],
    alita_client,
    touched_entities: List[Dict[str, Any]],
) -> List:
    """Build tools for chat agent."""
    import re
    import requests as http_requests
    from langchain.tools import Tool
    from pylon.core.tools import log

    # Import knowledge graph wrapper
    try:
        import sys
        plugin_dir = Path(__file__).parent.parent
        if str(plugin_dir) not in sys.path:
            sys.path.insert(0, str(plugin_dir))

        from inventory.retrieval import KnowledgeGraphRetrievalWrapper
    except ImportError as e:
        log.warning(f"[chat_worker] Could not import KnowledgeGraphRetrievalWrapper: {e}")
        return []

    tools = []

    # Check if graph exists
    if not os.path.exists(graph_path):
        log.warning(f"[chat_worker] Graph not found at {graph_path}")
        return tools

    # Load graph wrapper
    try:
        wrapper = KnowledgeGraphRetrievalWrapper(graph_path, {
            "configuration": {
                "project_id": project_id,
                "application_id": toolkit_id,
                "settings": settings,
            }
        })
    except Exception as e:
        log.exception(f"[chat_worker] Failed to create wrapper: {e}")
        return tools

    # Extract filter values
    filter_entity_types = filters.get("entity_types", [])
    filter_sources = filters.get("sources", [])
    filter_layers = filters.get("layers", [])
    default_depth = filters.get("depth", 2)
    default_max_nodes = filters.get("max_nodes", 500)

    def track_entity(entity: Dict[str, Any]):
        """Add entity to touched_entities if not already present."""
        entity_id = entity.get('id')
        if entity_id and not any(e.get('id') == entity_id for e in touched_entities):
            touched_entities.append({
                'id': entity_id,
                'name': entity.get('name'),
                'type': entity.get('type'),
                'layer': entity.get('layer'),
            })

    # 1. Graph Search Tool
    def search_graph(tool_input: str) -> str:
        """Search the knowledge graph."""
        try:
            query = tool_input
            top_k = min(default_max_nodes, 20)
            max_depth = default_depth

            if tool_input.strip().startswith('{'):
                try:
                    parsed = json.loads(tool_input)
                    query = parsed.get('query', tool_input)
                    top_k = min(parsed.get('top_k', top_k), 50)
                    max_depth = min(parsed.get('max_depth', max_depth), 3)
                except json.JSONDecodeError:
                    pass

            log.info(f"[search_graph] query='{query}', top_k={top_k}")

            results = wrapper._knowledge_graph.search(
                query,
                top_k=top_k,
                entity_type=filter_entity_types[0] if len(filter_entity_types) == 1 else None,
                layer=filter_layers[0] if len(filter_layers) == 1 else None,
            )

            if not results:
                return "No matching entities found."

            output = f"# {query} | {len(results)} results\n"

            for i, r in enumerate(results[:top_k], 1):
                entity = r['entity']
                track_entity(entity)
                score = r.get('score', 0.0)
                rel = f"{score:.0%}" if isinstance(score, float) else str(score)

                name = entity.get('name', 'unknown')
                etype = entity.get('type', 'unknown')
                output += f"{i}. {name} ({etype}) [{rel}]\n"

                desc = entity.get('description', '')
                if desc:
                    output += f"   {desc[:100]}{'...' if len(desc) > 100 else ''}\n"

            return output

        except Exception as e:
            log.exception(f"[search_graph] Error: {e}")
            return f"Error searching graph: {e}"

    tools.append(Tool(
        name="search_knowledge_graph",
        func=search_graph,
        description="Search the knowledge graph for entities matching a query. Returns entity names, types, and descriptions.",
    ))

    # 2. Get Entity Details Tool
    def get_entity_details(entity_name: str) -> str:
        """Get detailed information about a specific entity."""
        try:
            entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
            if not entity:
                return f"Entity '{entity_name}' not found."

            track_entity(entity)

            output = f"# {entity.get('name')}\n\n"
            output += f"**Type:** {entity.get('type', 'unknown')}\n"
            output += f"**Layer:** {entity.get('layer', 'unknown')}\n\n"

            if entity.get('description'):
                output += f"**Description:**\n{entity.get('description')}\n\n"

            if entity.get('content'):
                content = entity.get('content', '')[:2000]
                output += f"**Content:**\n```\n{content}\n```\n\n"

            return output

        except Exception as e:
            log.exception(f"[get_entity_details] Error: {e}")
            return f"Error getting entity: {e}"

    tools.append(Tool(
        name="get_entity_details",
        func=get_entity_details,
        description="Get detailed information about a specific entity by name.",
    ))

    # 3. Get Related Entities Tool
    def get_related_entities(entity_name: str) -> str:
        """Get entities related to the specified entity."""
        try:
            entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
            if not entity:
                return f"Entity '{entity_name}' not found."

            track_entity(entity)
            entity_id = entity.get('id')
            if not entity_id:
                return "Entity has no ID for relation lookup."

            relations = wrapper._knowledge_graph.get_relations(entity_id, direction="both")

            if not relations:
                return f"No relations found for '{entity_name}'."

            output = f"# Relations for {entity_name}\n\n"

            by_type = {}
            for rel in relations:
                rel_type = rel.get('relation_type', 'RELATED')
                if rel_type not in by_type:
                    by_type[rel_type] = []
                by_type[rel_type].append(rel)

            for rel_type, rels in by_type.items():
                output += f"## {rel_type}\n"
                for rel in rels[:10]:
                    if rel['source'] == entity_id:
                        output += f"- -> {rel.get('target_name', rel['target'])}\n"
                    else:
                        output += f"- <- {rel.get('source_name', rel['source'])}\n"
                if len(rels) > 10:
                    output += f"  ...and {len(rels) - 10} more\n"
                output += "\n"

            return output

        except Exception as e:
            log.exception(f"[get_related_entities] Error: {e}")
            return f"Error getting relations: {e}"

    tools.append(Tool(
        name="get_related_entities",
        func=get_related_entities,
        description="Get entities related to a specific entity.",
    ))

    # 4. List Entity Types Tool
    def list_entity_types(tool_input: str = "") -> str:
        """List all entity types in the knowledge graph."""
        try:
            stats = wrapper._knowledge_graph.get_stats()
            entity_types = stats.get('entity_types', {})

            if not entity_types:
                return "No entity types found in the graph."

            output = "# Entity Types in Knowledge Graph\n\n"
            for etype, count in sorted(entity_types.items(), key=lambda x: -x[1]):
                output += f"- **{etype}**: {count} entities\n"

            return output

        except Exception as e:
            return f"Error listing entity types: {e}"

    tools.append(Tool(
        name="list_entity_types",
        func=list_entity_types,
        description="List all entity types in the knowledge graph with counts.",
    ))

    # 5. Add source toolkit tools (read-only operations)
    source_tools = _get_source_toolkit_tools(
        project_id=project_id,
        toolkit_id=toolkit_id,
        alita_client=alita_client,
        settings=settings,
        touched_entities=touched_entities,
    )
    if source_tools:
        tools.extend(source_tools)
        log.info(f"[chat_worker] Added {len(source_tools)} source toolkit tools")

    return tools


def _get_source_toolkit_tools(
    project_id: int,
    toolkit_id: int,
    alita_client,
    settings: Dict[str, Any],
    touched_entities: List[Dict[str, Any]],
) -> List:
    """Get read-only tools from source toolkits."""
    import re
    import requests as http_requests
    from langchain.tools import Tool
    from pylon.core.tools import log
    from alita_sdk.runtime.toolkits.tools import get_tools

    tools = []

    # Read sources_status.json
    sources_status_path = f"/data/graphs/{project_id}/{toolkit_id}/sources_status.json"
    if not os.path.exists(sources_status_path):
        return tools

    try:
        with open(sources_status_path, 'r') as f:
            sources_status = json.load(f)
    except Exception as e:
        log.warning(f"[chat_worker] Failed to read sources_status.json: {e}")
        return tools

    sources = sources_status.get("sources", {})
    if not sources:
        return tools

    log.info(f"[chat_worker] Found {len(sources)} sources")

    # Read-only prefixes
    READ_ONLY_PREFIXES = (
        'get_', 'read_', 'list_', 'search_', 'find_', 'fetch_',
        'query_', 'describe_', 'show_', 'view_', 'browse_',
        'check_', 'verify_', 'validate_', 'lookup_', 'retrieve_',
    )
    WRITE_OPERATION_PATTERNS = (
        'create', 'update', 'delete', 'remove', 'add', 'modify',
        'edit', 'change', 'set', 'put', 'post', 'patch', 'write',
        'insert', 'upload', 'commit', 'push', 'merge', 'approve',
        'close', 'open', 'assign', 'unassign', 'comment', 'review',
        'fork', 'clone', 'archive', 'publish', 'deploy', 'execute',
        'run', 'start', 'stop', 'restart', 'enable', 'disable',
        'grant', 'revoke', 'invite', 'reject', 'accept', 'send',
    )

    # For each source, fetch toolkit config and instantiate tools
    for source_key, source_info in sources.items():
        source_toolkit_id = source_info.get("toolkit_id")
        source_toolkit_type = source_info.get("toolkit_type")
        source_toolkit_name = source_info.get("toolkit_name", source_toolkit_type)

        if not source_toolkit_id:
            continue

        try:
            # Fetch toolkit configuration
            toolkit_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{source_toolkit_id}"
            resp = http_requests.get(toolkit_url, headers=alita_client.headers, verify=False)

            if not resp.ok:
                continue

            toolkit_data = resp.json()
            toolkit_type = toolkit_data.get("type", source_toolkit_type)
            toolkit_settings = toolkit_data.get("settings", {})

            log.info(f"[chat_worker] Instantiating toolkit {source_toolkit_name} (type={toolkit_type})")

            # Build toolkit config
            toolkit_config = {
                "id": int(source_toolkit_id),
                "type": toolkit_type,
                "toolkit_name": source_toolkit_name,
                "name": source_toolkit_name,
                "settings": toolkit_settings,
            }

            # Get LLM for tools
            llm_model = settings.get("toolkit_configuration_llm_model") or settings.get("llm_model") or "gpt-4o-mini"
            llm = alita_client.get_llm(
                model_name=llm_model,
                model_config={"temperature": 0.0, "max_tokens": 2048},
            )

            # Instantiate tools
            source_tools = get_tools(
                [toolkit_config],
                alita_client=alita_client,
                llm=llm,
            )

            if source_tools:
                # Filter to read-only and prefix names
                safe_prefix = re.sub(r'[^a-zA-Z0-9_]', '_', source_toolkit_name.lower())
                safe_prefix = re.sub(r'^[_0-9]+', '', safe_prefix)[:20] or "source"

                for tool in source_tools:
                    tool_name = tool.name.lower() if hasattr(tool, 'name') else ''

                    # Check if read-only
                    is_read = any(tool_name.startswith(prefix) for prefix in READ_ONLY_PREFIXES)
                    has_write = any(pattern in tool_name for pattern in WRITE_OPERATION_PATTERNS)

                    if is_read and not has_write:
                        # Create prefixed tool
                        prefixed_name = f"{safe_prefix}_{tool.name}"
                        prefixed_desc = f"[Source: {source_toolkit_name}] {tool.description}"

                        prefixed_tool = Tool(
                            name=prefixed_name,
                            func=tool.func if hasattr(tool, 'func') else tool._run,
                            description=prefixed_desc,
                            args_schema=tool.args_schema if hasattr(tool, 'args_schema') else None,
                        )
                        tools.append(prefixed_tool)
                        log.debug(f"[chat_worker] Added tool: {prefixed_name}")

                log.info(f"[chat_worker] Added read-only tools from {source_toolkit_name}")

        except Exception as e:
            log.exception(f"[chat_worker] Error processing source {source_key}: {e}")
            continue

    return tools


def _execute_agent(
    llm,
    tools: List,
    prompt: str,
    history: List[Dict[str, str]],
    filters: Dict[str, Any],
    emit_fn: Callable,
) -> Dict[str, Any]:
    """Execute the chat agent."""
    import yaml
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from langchain_core.callbacks import BaseCallbackHandler
    from langgraph.checkpoint.memory import MemorySaver
    from alita_sdk.runtime.langchain.langraph_agent import create_graph
    from pylon.core.tools import log

    # System prompt
    SYSTEM_PROMPT = """You are an AI assistant helping users explore and understand a knowledge graph.

You have access to tools that allow you to:
- Search the knowledge graph for entities
- Get detailed information about specific entities
- Find relationships between entities
- List all entity types
- Read files from source repositories (prefixed with source name)

When answering questions:
1. Use the search_knowledge_graph tool to find relevant entities
2. Use get_entity_details for more information about specific entities
3. Use get_related_entities to understand connections
4. Use source toolkit tools (prefixed with source name) to read actual file contents

Always provide clear, accurate responses based on the knowledge graph data.
Cite your sources when providing information.

Current filters: {filters}
"""

    # Format filters
    filter_desc = []
    if filters.get("entity_types"):
        filter_desc.append(f"Entity types: {', '.join(filters['entity_types'])}")
    if filters.get("sources"):
        filter_desc.append(f"Sources: {', '.join(filters['sources'])}")
    if filters.get("layers"):
        filter_desc.append(f"Layers: {', '.join(filters['layers'])}")

    filter_text = "\n".join(filter_desc) if filter_desc else "None"
    system_prompt = SYSTEM_PROMPT.format(filters=filter_text)

    # Callback adapter
    class CallbackAdapter(BaseCallbackHandler):
        def __init__(self, emit):
            self.emit = emit

        def on_tool_start(self, serialized, input_str, **kwargs):
            tool_name = serialized.get("name", "unknown")
            self.emit("tool_start", {
                "tool_name": tool_name,
                "input": input_str[:500] if input_str else "",
            })

        def on_tool_end(self, output, **kwargs):
            self.emit("tool_end", {
                "output_preview": output[:500] if output else "",
            })

        def on_llm_start(self, serialized, prompts, **kwargs):
            self.emit("llm_start", {"model": serialized.get("name", "LLM")})

        def on_llm_end(self, response, **kwargs):
            self.emit("llm_end", {})

    callback = CallbackAdapter(emit_fn)

    # Build LangGraph schema
    tool_names = [tool.name for tool in tools] if tools else []

    # Build chat history
    chat_history_messages = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            chat_history_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            chat_history_messages.append(AIMessage(content=content))

    schema_dict = {
        'name': 'inventory_chat_agent',
        'state': {
            'input': {'type': 'str'},
            'messages': {'type': 'list'}
        },
        'nodes': [{
            'id': 'agent',
            'type': 'llm',
            'prompt': {'template': system_prompt},
            'input_mapping': {
                'system': {'type': 'fixed', 'value': system_prompt},
                'task': {'type': 'variable', 'value': 'input'},
                'chat_history': {'type': 'variable', 'value': 'messages'}
            },
            'step_limit': 25,
            'input': ['messages'],
            'output': ['messages'],
            'transition': 'END'
        }],
        'entry_point': 'agent'
    }

    if tools:
        schema_dict['nodes'][0]['tool_names'] = tool_names

    yaml_schema = yaml.dump(schema_dict, default_flow_style=False, allow_unicode=True)

    # Create and execute agent
    try:
        checkpointer = MemorySaver()

        agent = create_graph(
            client=llm,
            yaml_schema=yaml_schema,
            tools=tools,
            memory=checkpointer,
            store=None,
            debug=False,
            for_subgraph=False,
            steps_limit=25
        )

        log.info("[chat_worker] LangGraph agent created, invoking...")

        thread_id = str(uuid.uuid4())
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [callback],
        }

        result = agent.invoke(
            {"input": prompt, "messages": chat_history_messages},
            config=config
        )

        # Extract answer
        answer = ""
        tool_calls = []

        if "messages" in result:
            messages = result["messages"]

            # Get last AI message as answer
            for msg in reversed(messages):
                if hasattr(msg, 'content') and isinstance(msg, AIMessage):
                    content = msg.content
                    if isinstance(content, list):
                        answer_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                answer_parts.append(block.get('text', ''))
                            elif isinstance(block, str):
                                answer_parts.append(block)
                        answer = ''.join(answer_parts)
                    else:
                        answer = content if isinstance(content, str) else str(content)
                    break

            # Extract tool calls
            tool_results = {}
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    tool_call_id = getattr(msg, 'tool_call_id', None)
                    if tool_call_id:
                        content = msg.content
                        if isinstance(content, list):
                            content = ''.join(
                                b.get('text', '') if isinstance(b, dict) else str(b)
                                for b in content
                            )
                        tool_results[tool_call_id] = str(content)[:500]

            for msg in messages:
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_call_id = tc.get("id", "")
                        tool_args = tc.get("args", {})
                        if isinstance(tool_args, dict):
                            filtered_args = {k: v for k, v in tool_args.items() if v and k != "__arg1"}
                            input_str = json.dumps(filtered_args, indent=2) if filtered_args else "{}"
                        else:
                            input_str = str(tool_args)

                        tool_calls.append({
                            "tool": tc.get("name", "unknown"),
                            "input": input_str[:500],
                            "output_preview": tool_results.get(tool_call_id, "")[:500],
                        })

        if not answer and "output" in result:
            answer = result.get("output", "")

        # Extract citations
        citations = _extract_citations(answer)

        return {
            "answer": answer,
            "citations": citations,
            "tool_calls": tool_calls,
            "error": None,
        }

    except Exception as e:
        log.exception(f"[chat_worker] Agent error: {e}")
        return {
            "answer": "",
            "citations": [],
            "tool_calls": [],
            "error": str(e),
        }


def _extract_citations(answer: str) -> List[Dict[str, Any]]:
    """Extract citation references from answer text."""
    import re

    citations = []

    # Pattern: Source: toolkit - path
    source_pattern = r"Source:\s*(\w+)(?:\s*[-:]\s*([^\n]+))?"
    for match in re.finditer(source_pattern, answer):
        citations.append({
            "source_toolkit": match.group(1),
            "file_path": match.group(2).strip() if match.group(2) else None,
        })

    # Pattern: `ClassName` or `function_name`
    entity_pattern = r"`([A-Z][a-zA-Z0-9_]+)`|`([a-z_][a-zA-Z0-9_]+)`"
    for match in re.finditer(entity_pattern, answer):
        entity_name = match.group(1) or match.group(2)
        if entity_name and len(entity_name) > 2:
            citations.append({"entity_name": entity_name})

    # Deduplicate
    seen = set()
    unique = []
    for c in citations:
        key = json.dumps(c, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:20]
