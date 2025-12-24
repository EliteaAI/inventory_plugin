#!/usr/bin/python3
# coding=utf-8

"""
Inventory Chat Method

Provides a self-contained chat interface for the inventory knowledge graph.
- Uses inventory toolkit's LLM configuration
- Auto-compiles tools: graph search, entity query, + each source as hybrid search
- Returns structured responses with citations
- Streams intermediate steps via callbacks
"""

import json
import uuid
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from pylon.core.tools import log, web

from ..constants import (
    INVENTORY_CHAT_SYSTEM_PROMPT,
    TOOL_DESCRIPTIONS,
    READ_ONLY_PREFIXES,
    WRITE_OPERATION_PATTERNS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_TOOL_LLM_MAX_TOKENS,
)
from ..utils.langfuse_callback import (
    fetch_langfuse_config,
    create_langfuse_callback,
    flush_langfuse_callback,
    langfuse_trace_context,
)


class InventoryChatCallback:
    """
    Callback handler for inventory chat that emits events for streaming.

    This class implements LangChain callback methods and forwards events
    to a provided emit function (typically socket.io emit).
    """

    def __init__(
        self,
        emit_fn: Callable[[str, Dict[str, Any]], None],
        session_id: str,
        conversation_id: Optional[str] = None,
        file_tracking_fn: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Initialize callback handler.

        Args:
            emit_fn: Function to emit events (receives event_type, data)
            session_id: Unique session ID for this chat
            conversation_id: Optional conversation ID for history
            file_tracking_fn: Optional function to track file accesses (tool_name, input_str)
        """
        self.emit_fn = emit_fn
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.file_tracking_fn = file_tracking_fn
        self.tool_runs = {}  # Track active tool runs
        self.start_time = time.time()

    def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event with session metadata."""
        payload = {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **data,
        }
        try:
            self.emit_fn(event_type, payload)
        except Exception as e:
            log.warning(f"Failed to emit event {event_type}: {e}")

    # LangChain callback methods
    def on_llm_start(self, serialized: Dict, prompts: List[str], **kwargs):
        """Called when LLM starts processing."""
        run_id = str(kwargs.get("run_id", uuid.uuid4()))

        # Extract model name from various possible locations
        model_name = None
        # Try serialized dict first
        if serialized:
            model_name = serialized.get("model_name") or serialized.get("model") or serialized.get("name")
            # Check nested kwargs in serialized
            if not model_name and "kwargs" in serialized:
                model_name = serialized["kwargs"].get("model_name") or serialized["kwargs"].get("model")
            # Check id field (often contains model info)
            if not model_name and "id" in serialized:
                id_parts = serialized["id"]
                if isinstance(id_parts, list) and len(id_parts) > 0:
                    model_name = id_parts[-1]  # Last part often has model name

        # Try invocation_params from kwargs
        invocation_params = kwargs.get("invocation_params", {})
        if not model_name and invocation_params:
            model_name = invocation_params.get("model_name") or invocation_params.get("model")

        # Store LLM run info for later
        self.tool_runs[run_id] = {
            "name": model_name or "LLM",
            "type": "llm",
            "start_time": time.time(),
            "input": prompts[0][:500] if prompts else "",  # First prompt, truncated
        }

        self.emit("llm_start", {
            "run_id": run_id,
            "model": model_name or "LLM",
            "input": prompts[0][:500] if prompts else "",  # Include input preview
        })

    def on_llm_new_token(self, token: str, **kwargs):
        """Called for each new token (streaming)."""
        run_id = str(kwargs.get("run_id", ""))
        self.emit("llm_token", {
            "run_id": run_id,
            "token": token,
        })

    def on_llm_end(self, response, **kwargs):
        """Called when LLM finishes."""
        run_id = str(kwargs.get("run_id", ""))
        llm_info = self.tool_runs.pop(run_id, {})

        duration = time.time() - llm_info.get("start_time", time.time())

        # Extract output content and thinking/reasoning from response
        output_content = ""
        thinking_content = ""

        if response:
            # LLMResult has generations attribute
            if hasattr(response, "generations") and response.generations:
                for gen_list in response.generations:
                    for gen in gen_list:
                        if hasattr(gen, "text"):
                            output_content += gen.text

                        if hasattr(gen, "message"):
                            msg = gen.message

                            if hasattr(msg, "content"):
                                content = msg.content
                                if isinstance(content, str):
                                    output_content += content
                                elif isinstance(content, list):
                                    # Handle list of content blocks (including thinking blocks)
                                    for block in content:
                                        if isinstance(block, dict):
                                            block_type = block.get("type", "")
                                            if block_type == "thinking":
                                                # Extended thinking block from Claude
                                                thinking_content += block.get("thinking", "")
                                            elif block_type == "text":
                                                output_content += block.get("text", "")
                                        elif isinstance(block, str):
                                            output_content += block
                                        # Check if block is an object with type attribute
                                        elif hasattr(block, "type"):
                                            if block.type == "thinking" and hasattr(block, "thinking"):
                                                thinking_content += block.thinking
                                            elif block.type == "text" and hasattr(block, "text"):
                                                output_content += block.text

                            # Check response_metadata for thinking
                            if hasattr(msg, "response_metadata"):
                                metadata = msg.response_metadata or {}
                                if "thinking" in metadata:
                                    thinking_content += metadata.get("thinking", "")
                            # Check additional_kwargs
                            if hasattr(msg, "additional_kwargs"):
                                additional = msg.additional_kwargs or {}
                                if "thinking" in additional:
                                    thinking_content += additional.get("thinking", "")

        # Emit thinking/reasoning tokens if present
        if thinking_content:
            self.emit("thinking_step", {
                "message": thinking_content,
                "tool_name": "reasoning",
                "is_reasoning_token": True,
            })

        self.emit("llm_end", {
            "run_id": run_id,
            "output": output_content[:1000] if output_content else "",  # Truncate output
            "duration_ms": int(duration * 1000),
            "has_thinking": bool(thinking_content),
        })

    def on_llm_error(self, error: Exception, **kwargs):
        """Called on LLM error."""
        run_id = str(kwargs.get("run_id", ""))
        llm_info = self.tool_runs.pop(run_id, {})
        self.emit("llm_error", {
            "run_id": run_id,
            "error": str(error),
        })

    def on_tool_start(self, serialized: Dict, input_str: str, **kwargs):
        """Called when a tool starts execution."""
        run_id = str(kwargs.get("run_id", uuid.uuid4()))
        tool_name = serialized.get("name", "unknown")

        self.tool_runs[run_id] = {
            "name": tool_name,
            "start_time": time.time(),
        }

        # Track file accesses for file-reading tools
        if self.file_tracking_fn:
            file_reading_patterns = ('read_file', 'get_file', 'fetch_file', 'get_content', 'read_content')
            tool_name_lower = tool_name.lower()
            if any(pattern in tool_name_lower for pattern in file_reading_patterns):
                try:
                    self.file_tracking_fn(tool_name, input_str)
                except Exception as e:
                    log.warning(f"File tracking failed for {tool_name}: {e}")

        self.emit("tool_start", {
            "run_id": run_id,
            "tool_name": tool_name,
            "input": input_str[:500] if input_str else "",  # Truncate large inputs
        })

    def on_tool_end(self, output: str, **kwargs):
        """Called when a tool finishes."""
        run_id = str(kwargs.get("run_id", ""))
        tool_info = self.tool_runs.pop(run_id, {})

        duration = time.time() - tool_info.get("start_time", time.time())

        self.emit("tool_end", {
            "run_id": run_id,
            "tool_name": tool_info.get("name", "unknown"),
            "output_preview": output[:1000] if output else "",  # Preview only
            "duration_ms": int(duration * 1000),
        })

    def on_tool_error(self, error: Exception, **kwargs):
        """Called on tool error."""
        run_id = str(kwargs.get("run_id", ""))
        tool_info = self.tool_runs.pop(run_id, {})

        self.emit("tool_error", {
            "run_id": run_id,
            "tool_name": tool_info.get("name", "unknown"),
            "error": str(error),
        })

    def on_chain_start(self, serialized: Dict, inputs: Dict, **kwargs):
        """Called when a chain starts."""
        pass  # Usually too noisy, skip

    def on_chain_end(self, outputs: Dict, **kwargs):
        """Called when a chain ends."""
        pass

    def on_agent_action(self, action, **kwargs):
        """Called when agent takes an action."""
        self.emit("agent_action", {
            "tool": action.tool if hasattr(action, "tool") else str(action),
            "tool_input": str(action.tool_input)[:500] if hasattr(action, "tool_input") else "",
        })

    def on_agent_finish(self, finish, **kwargs):
        """Called when agent finishes."""
        duration = time.time() - self.start_time
        self.emit("agent_finish", {
            "duration_ms": int(duration * 1000),
        })

    def on_custom_event(self, name: str, data: Dict, **kwargs):
        """Called when a custom event is dispatched (e.g., thinking_step)."""
        if name == "thinking_step":
            self.emit("thinking_step", {
                "message": data.get("message", ""),
                "tool_name": data.get("tool_name", ""),
                "toolkit": data.get("toolkit", ""),
            })


class Method:
    """
    Method Resource for inventory chat

    self is pointing to current Module instance
    """

    @web.method()
    def inventory_chat(
        self,
        project_id: int,
        toolkit_id: int,
        prompt: str,
        filters: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        emit_fn: Optional[Callable] = None,
        model: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a chat query against the inventory knowledge graph.

        This method:
        1. Loads LLM from inventory toolkit configuration (or uses specified model)
        2. Auto-compiles tools (graph search + each source as hybrid search)
        3. Executes the agent with streaming callbacks
        4. Returns structured response with citations

        Args:
            project_id: Project ID
            toolkit_id: Inventory toolkit ID (for LLM config)
            prompt: User's question/prompt
            filters: Optional filters (entity_types, sources, layers, etc.)
            conversation_id: Optional conversation ID for history persistence
            history: Optional chat history [(role, content), ...]
            emit_fn: Optional callback emit function for streaming
            model: Optional model name to use for chat (overrides toolkit config)
            user_id: Optional user ID for Langfuse tracing attribution

        Returns:
            Dict with:
            - answer: The LLM's response
            - citations: List of source citations
            - tool_calls: List of tools that were called
            - error: Error message if failed
        """
        session_id = str(uuid.uuid4())
        filters = filters or {}
        history = history or []

        log.info(f"[inventory_chat] Starting chat session {session_id}")
        log.info(f"[inventory_chat] project_id={project_id}, toolkit_id={toolkit_id}")
        log.info(f"[inventory_chat] prompt: {prompt[:100]}...")
        log.info(f"[inventory_chat] filters: {filters}")

        # Track entities accessed during execution (shared across all tracking)
        touched_entities = []

        # Create file tracking function for callback
        def track_file_from_tool(tool_name: str, tool_input: str):
            """Extract file path from tool input and add to touched_entities."""
            import re

            FILE_PATH_PATTERNS = [
                r'"(?:file_?path|path|file)"\s*:\s*"([^"]+)"',
                r"'(?:file_?path|path|file)'\s*:\s*'([^']+)'",
                r'(?:file_?path|path|file)\s*=\s*["\']?([^\s"\',}]+)',
            ]

            file_path = None
            input_str = str(tool_input) if tool_input else ""

            for pattern in FILE_PATH_PATTERNS:
                match = re.search(pattern, input_str, re.IGNORECASE)
                if match:
                    file_path = match.group(1)
                    break

            # If input looks like a plain file path
            if not file_path and '/' in input_str and not input_str.strip().startswith('{'):
                path = input_str.strip().strip('"\'')
                if path and ' ' not in path[:20]:
                    file_path = path

            if file_path:
                # Extract toolkit name from tool_name (e.g., "github_read_file" -> "github")
                toolkit_name = tool_name.split('_')[0] if '_' in tool_name else "source"
                file_id = f"file:{toolkit_name}:{file_path}"

                if not any(e.get('id') == file_id for e in touched_entities):
                    file_name = file_path.split('/')[-1] if '/' in file_path else file_path
                    touched_entities.append({
                        'id': file_id,
                        'name': file_name,
                        'type': 'file',
                        'layer': 'source',
                        'file_path': file_path,
                        'source_toolkit': toolkit_name,
                        'is_file_access': True,
                    })
                    log.info(f"[inventory_chat] Tracked file access: {file_path} from {toolkit_name}")

        # Create callback handler with file tracking
        callback = InventoryChatCallback(
            emit_fn=emit_fn or (lambda t, d: None),  # No-op if no emit function
            session_id=session_id,
            conversation_id=conversation_id,
            file_tracking_fn=track_file_from_tool,
        )

        # Initialize Langfuse variables for finally block
        langfuse_client = None
        langfuse_callback = None

        try:
            # 1. Get AlitaClient for platform API
            alita_client = self._get_alita_client(project_id)
            if not alita_client:
                return {
                    "answer": "",
                    "citations": [],
                    "tool_calls": [],
                    "error": "Platform API not configured",
                }

            # 2. Fetch inventory toolkit settings
            import requests as http_requests
            toolkit_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}"
            resp = http_requests.get(toolkit_url, headers=alita_client.headers, verify=False)

            if not resp.ok:
                return {
                    "answer": "",
                    "citations": [],
                    "tool_calls": [],
                    "error": f"Failed to fetch toolkit settings: {resp.status_code}",
                }

            toolkit_data = resp.json()
            settings = toolkit_data.get("settings", {})
            toolkit_name = toolkit_data.get("name", f"inventory-{toolkit_id}")

            # 3. Fetch Langfuse config for tracing (optional)
            langfuse_config = fetch_langfuse_config(alita_client)
            langfuse_trace_attrs = None

            if langfuse_config:
                langfuse_metadata = {
                    "project_id": str(project_id),
                    "toolkit_id": str(toolkit_id),
                    "toolkit_name": toolkit_name,
                    "conversation_id": conversation_id or "",
                }
                langfuse_client, langfuse_callback, langfuse_trace_attrs = create_langfuse_callback(
                    langfuse_config,
                    trace_name=f"inventory-chat:{toolkit_name}",
                    session_id=conversation_id or session_id,
                    user_id=user_id,
                    metadata=langfuse_metadata,
                )

            # 4. Get LLM model - prefer passed model, fallback to toolkit configuration
            llm_model = model or (
                settings.get("toolkit_configuration_llm_model") or
                settings.get("llm_model") or
                "gpt-4o-mini"
            )
            log.info(f"[inventory_chat] Using LLM model: {llm_model} (requested: {model})")

            # 4. Get graph path
            graph_path = f"/data/graphs/{project_id}/{toolkit_id}/graph.json"

            # 5. Build tools for the agent (touched_entities was declared earlier)
            tools = self._build_chat_tools(
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

            log.info(f"[inventory_chat] Built {len(tools)} tools")

            # 7. Get LLM instance with extended thinking for supported models
            model_config = {
                "temperature": DEFAULT_LLM_TEMPERATURE,
                "max_tokens": DEFAULT_LLM_MAX_TOKENS,
            }

            # Enable extended thinking for Claude models that support it
            # Claude 3.5 Sonnet and Claude 3 Opus support extended thinking
            llm_model_lower = llm_model.lower()
            if "claude" in llm_model_lower and ("sonnet" in llm_model_lower or "opus" in llm_model_lower):
                log.info(f"[inventory_chat] Enabling extended thinking for {llm_model}")
                model_config["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": 8000,  # Allow up to 8k tokens for reasoning
                }
                # Extended thinking requires temperature=1
                model_config["temperature"] = 1.0

            llm = alita_client.get_llm(
                model_name=llm_model,
                model_config=model_config,
            )

            # 8. Build the agent and execute with Langfuse tracing
            with langfuse_trace_context(langfuse_trace_attrs):
                result = self._execute_chat_agent(
                    llm=llm,
                    tools=tools,
                    prompt=prompt,
                    history=history,
                    filters=filters,
                    callback=callback,
                    langfuse_callback=langfuse_callback,
                )

            # Add touched entities to result
            result["touched_entities"] = touched_entities
            log.info(f"[inventory_chat] Touched {len(touched_entities)} entities")

            callback.emit("chat_complete", {
                "answer_length": len(result.get("answer", "")),
                "citations_count": len(result.get("citations", [])),
                "touched_entities_count": len(touched_entities),
            })

            return result

        except Exception as e:
            log.exception(f"[inventory_chat] Error: {e}")
            error_msg = str(e)

            callback.emit("chat_error", {"error": error_msg})

            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "touched_entities": [],
                "error": error_msg,
            }
        finally:
            # Flush Langfuse traces
            flush_langfuse_callback(langfuse_client, langfuse_callback)

    @web.method()
    def _build_chat_tools(
        self,
        project_id: int,
        toolkit_id: int,
        graph_path: str,
        filters: Dict[str, Any],
        settings: Dict[str, Any],
        alita_client,
        touched_entities: List[Dict[str, Any]],
    ) -> List:
        """
        Build tools for the chat agent.

        Creates:
        1. Graph search tool (with filters applied)
        2. Entity details tool
        3. Related entities tool
        4. List entity types tool
        5. Source toolkit tools (read-only operations from GitHub, etc.)

        Args:
            touched_entities: Shared list to collect entities accessed during execution
        """
        from langchain.tools import Tool
        import os

        tools = []

        # Check if graph exists
        if not os.path.exists(graph_path):
            log.warning(f"[_build_chat_tools] Graph not found at {graph_path}")
            return tools

        # Add source toolkit tools (read-only operations) with tracking wrapper
        source_tools = self._get_source_toolkit_tools(
            project_id=project_id,
            toolkit_id=toolkit_id,
            alita_client=alita_client,
            settings=settings,
            touched_entities=touched_entities,
        )
        if source_tools:
            tools.extend(source_tools)
            log.info(f"[_build_chat_tools] Added {len(source_tools)} source toolkit tools")

        # Get the retrieval wrapper
        wrapper = self._get_or_create_wrapper(graph_path, {
            "configuration": {
                "project_id": project_id,
                "application_id": toolkit_id,
                "settings": settings,
            }
        })

        # Extract filter values
        filter_entity_types = filters.get("entity_types", [])
        filter_sources = filters.get("sources", [])
        filter_layers = filters.get("layers", [])
        # Default depth and max_nodes from filters
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
            """Search the knowledge graph for entities matching the query. Returns tree structure."""
            try:
                # Parse input - can be JSON object or plain string query
                query = tool_input
                top_k = min(default_max_nodes, 20)  # Cap at 20 for tree display
                max_depth = default_depth

                # Try to parse as JSON for structured input
                if tool_input.strip().startswith('{'):
                    try:
                        parsed = json.loads(tool_input)
                        query = parsed.get('query', tool_input)
                        top_k = min(parsed.get('top_k', top_k), 50)  # Cap at 50
                        max_depth = min(parsed.get('max_depth', max_depth), 3)  # Cap depth at 3
                    except json.JSONDecodeError:
                        pass  # Use as plain string query

                log.info(f"[search_graph] query='{query}', top_k={top_k}, max_depth={max_depth}")

                results = wrapper._knowledge_graph.search(
                    query,
                    top_k=top_k,
                    entity_type=filter_entity_types[0] if len(filter_entity_types) == 1 else None,
                    layer=filter_layers[0] if len(filter_layers) == 1 else None,
                )

                # Filter by source if specified
                if filter_sources:
                    filtered = []
                    for r in results:
                        entity = r['entity']
                        citations = entity.get('citations', [])
                        if not citations and 'citation' in entity:
                            citations = [entity['citation']]
                        for c in citations:
                            if isinstance(c, dict) and c.get('source_toolkit') in filter_sources:
                                filtered.append(r)
                                break
                    results = filtered

                # Filter by entity type if multiple specified
                if len(filter_entity_types) > 1:
                    results = [r for r in results if r['entity'].get('type', '').lower() in
                              [t.lower() for t in filter_entity_types]]

                if not results:
                    return "No matching entities found."

                # Build tree structure with relationships
                def get_source_info(entity):
                    """Extract source info from entity."""
                    citations = entity.get('citations', [])
                    if not citations and 'citation' in entity:
                        citations = [entity['citation']]
                    if citations and isinstance(citations[0], dict):
                        c = citations[0]
                        source = c.get('source_toolkit', 'unknown')
                        path = c.get('file_path', '')
                        return f"{source} - {path}" if path else source
                    return None

                def build_entity_node(entity, depth_remaining, visited):
                    """Build a tree node for an entity with its relationships."""
                    entity_id = entity.get('id')
                    if not entity_id or entity_id in visited:
                        return None

                    visited.add(entity_id)
                    track_entity(entity)

                    node = {
                        'name': entity.get('name', 'unknown'),
                        'type': entity.get('type', 'unknown'),
                    }

                    # Add description if available (truncated)
                    desc = entity.get('description', '')
                    if desc:
                        node['description'] = desc[:100] + ('...' if len(desc) > 100 else '')

                    # Add source
                    source = get_source_info(entity)
                    if source:
                        node['source'] = source

                    # Get relationships if depth allows
                    if depth_remaining > 0:
                        try:
                            relations = wrapper._knowledge_graph.get_relations(entity_id, direction="both")
                            if relations:
                                # Group by relation type
                                outgoing = {}  # entity -> others
                                incoming = {}  # others -> entity

                                for rel in relations[:20]:  # Limit relations
                                    rel_type = rel.get('relation_type', 'RELATED')
                                    if rel['source'] == entity_id:
                                        # Outgoing relation
                                        if rel_type not in outgoing:
                                            outgoing[rel_type] = []
                                        target_name = rel.get('target_name', rel['target'])
                                        outgoing[rel_type].append(target_name)
                                    else:
                                        # Incoming relation
                                        if rel_type not in incoming:
                                            incoming[rel_type] = []
                                        source_name = rel.get('source_name', rel['source'])
                                        incoming[rel_type].append(source_name)

                                if outgoing:
                                    node['relations'] = {}
                                    for rel_type, targets in outgoing.items():
                                        node['relations'][rel_type] = targets[:5]  # Limit to 5 per type
                                        if len(targets) > 5:
                                            node['relations'][rel_type].append(f"...+{len(targets)-5} more")

                                if incoming:
                                    node['referenced_by'] = {}
                                    for rel_type, sources in incoming.items():
                                        node['referenced_by'][rel_type] = sources[:5]
                                        if len(sources) > 5:
                                            node['referenced_by'][rel_type].append(f"...+{len(sources)-5} more")

                        except Exception as e:
                            log.warning(f"[search_graph] Error getting relations for {entity_id}: {e}")

                    return node

                # Build compact tree output
                output = f"# {query} | {len(results)} results | depth={max_depth}\n"

                visited = set()
                for i, r in enumerate(results[:top_k], 1):
                    entity = r['entity']
                    score = r.get('score', 0.0)
                    rel = f"{score:.0%}" if isinstance(score, float) else str(score)

                    node = build_entity_node(entity, max_depth, visited)
                    if node:
                        # Compact header: name (type) [relevancy] @ source
                        header = f"{node['name']} ({node['type']}) [{rel}]"
                        if 'source' in node:
                            header += f" @ {node['source']}"
                        output += f"{i}. {header}\n"

                        # Description on next line if present
                        if 'description' in node:
                            output += f"   {node['description']}\n"

                        # Compact relations: rel_type: target1, target2, ...
                        if 'relations' in node:
                            for rel_type, targets in node['relations'].items():
                                output += f"   -> {rel_type}: {', '.join(targets)}\n"

                        # Compact referenced_by
                        if 'referenced_by' in node:
                            for rel_type, sources in node['referenced_by'].items():
                                output += f"   <- {rel_type}: {', '.join(sources)}\n"

                return output

            except Exception as e:
                log.exception(f"[search_graph] Error: {e}")
                return f"Error searching graph: {e}"

        tools.append(Tool(
            name="search_knowledge_graph",
            func=search_graph,
            description=TOOL_DESCRIPTIONS["search_knowledge_graph"],
        ))

        # 2. Get Entity Details Tool
        def get_entity_details(entity_name: str) -> str:
            """Get detailed information about a specific entity."""
            try:
                entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
                if not entity:
                    return f"Entity '{entity_name}' not found."

                # Track this entity as touched
                track_entity(entity)

                output = f"# {entity.get('name')}\n\n"
                output += f"**Type:** {entity.get('type', 'unknown')}\n"
                output += f"**Layer:** {entity.get('layer', 'unknown')}\n\n"

                if entity.get('description'):
                    output += f"**Description:**\n{entity.get('description')}\n\n"

                if entity.get('content'):
                    content = entity.get('content', '')
                    if len(content) > 2000:
                        content = content[:2000] + "...[truncated]"
                    output += f"**Content:**\n```\n{content}\n```\n\n"

                # Add citations
                citations = entity.get('citations', [])
                if not citations and 'citation' in entity:
                    citations = [entity['citation']]

                if citations:
                    output += "**Sources:**\n"
                    for c in citations:
                        if isinstance(c, dict):
                            output += f"- {c.get('source_toolkit', 'unknown')}"
                            if c.get('file_path'):
                                output += f": {c.get('file_path')}"
                            if c.get('line_start'):
                                output += f" (lines {c.get('line_start')}-{c.get('line_end', '?')})"
                            output += "\n"

                return output

            except Exception as e:
                log.exception(f"[get_entity_details] Error: {e}")
                return f"Error getting entity: {e}"

        tools.append(Tool(
            name="get_entity_details",
            func=get_entity_details,
            description=TOOL_DESCRIPTIONS["get_entity_details"],
        ))

        # 3. Get Related Entities Tool
        def get_related_entities(entity_name: str) -> str:
            """Get entities related to the specified entity."""
            try:
                entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
                if not entity:
                    return f"Entity '{entity_name}' not found."

                # Track the main entity
                track_entity(entity)

                entity_id = entity.get('id')
                if not entity_id:
                    return "Entity has no ID for relation lookup."

                relations = wrapper._knowledge_graph.get_relations(entity_id, direction="both")

                if not relations:
                    return f"No relations found for '{entity_name}'."

                output = f"# Relations for {entity_name}\n\n"

                # Group by relation type
                by_type = {}
                for rel in relations:
                    rel_type = rel.get('relation_type', 'RELATED')
                    if rel_type not in by_type:
                        by_type[rel_type] = []
                    by_type[rel_type].append(rel)

                for rel_type, rels in by_type.items():
                    output += f"## {rel_type}\n"
                    for rel in rels[:10]:
                        # Track related entities
                        if rel['source'] == entity_id:
                            related_id = rel['target']
                            related_name = rel.get('target_name', rel['target'])
                            output += f"- → {related_name}\n"
                        else:
                            related_id = rel['source']
                            related_name = rel.get('source_name', rel['source'])
                            output += f"- ← {related_name}\n"
                        # Track the related entity (minimal info from relation)
                        if related_id and not any(e.get('id') == related_id for e in touched_entities):
                            touched_entities.append({
                                'id': related_id,
                                'name': related_name,
                                'type': None,  # Not available in relation data
                                'layer': None,
                            })
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
            description=TOOL_DESCRIPTIONS["get_related_entities"],
        ))

        # 4. List Entity Types Tool
        def list_entity_types(tool_input: str = "") -> str:
            """List all entity types in the knowledge graph."""
            # Note: tool_input is ignored, this tool takes no parameters
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
            description=TOOL_DESCRIPTIONS["list_entity_types"],
        ))

        return tools

    @web.method()
    def _execute_chat_agent(
        self,
        llm,
        tools: List,
        prompt: str,
        history: List[Dict[str, str]],
        filters: Dict[str, Any],
        callback: InventoryChatCallback,
        langfuse_callback=None,
    ) -> Dict[str, Any]:
        """
        Execute the chat agent with the given tools and prompt.

        Uses LangGraph agent pattern (same as alita-sdk) for better tool calling support.

        Args:
            langfuse_callback: Optional Langfuse CallbackHandler for tracing

        Returns structured response with answer and citations.
        """
        import yaml
        from langchain_core.messages import HumanMessage, AIMessage
        from langchain_core.callbacks import BaseCallbackHandler
        from langgraph.checkpoint.memory import MemorySaver
        from alita_sdk.runtime.langchain.langraph_agent import create_graph

        # Format filters for prompt
        filter_desc = []
        # Search scope settings
        depth = filters.get("depth", 2)
        max_nodes = filters.get("max_nodes", 500)
        filter_desc.append(f"Depth: {depth} (relationship hops to traverse)")
        filter_desc.append(f"Max nodes: {max_nodes} (maximum results to return)")
        # Type/source filters
        if filters.get("entity_types"):
            filter_desc.append(f"Entity types: {', '.join(filters['entity_types'])}")
        if filters.get("sources"):
            filter_desc.append(f"Sources: {', '.join(filters['sources'])}")
        if filters.get("layers"):
            filter_desc.append(f"Layers: {', '.join(filters['layers'])}")

        filter_text = "\n".join(filter_desc)

        # Build system prompt
        system_prompt = INVENTORY_CHAT_SYSTEM_PROMPT.format(filters=filter_text)

        # Create callback adapter for LangChain/LangGraph
        class LangChainCallbackAdapter(BaseCallbackHandler):
            """Adapter to convert LangChain callbacks to our format."""
            def __init__(self, inventory_callback):
                self.cb = inventory_callback

            def on_llm_start(self, serialized, prompts, **kwargs):
                self.cb.on_llm_start(serialized, prompts, **kwargs)

            def on_llm_new_token(self, token, **kwargs):
                self.cb.on_llm_new_token(token, **kwargs)

            def on_llm_end(self, response, **kwargs):
                self.cb.on_llm_end(response, **kwargs)

            def on_llm_error(self, error, **kwargs):
                self.cb.on_llm_error(error, **kwargs)

            def on_tool_start(self, serialized, input_str, **kwargs):
                self.cb.on_tool_start(serialized, input_str, **kwargs)

            def on_tool_end(self, output, **kwargs):
                self.cb.on_tool_end(output, **kwargs)

            def on_tool_error(self, error, **kwargs):
                self.cb.on_tool_error(error, **kwargs)

            def on_agent_action(self, action, **kwargs):
                self.cb.on_agent_action(action, **kwargs)

            def on_agent_finish(self, finish, **kwargs):
                self.cb.on_agent_finish(finish, **kwargs)

            def on_custom_event(self, name, data, **kwargs):
                self.cb.on_custom_event(name, data, **kwargs)

        lc_callback = LangChainCallbackAdapter(callback)

        # Build LangGraph YAML schema (same pattern as alita-sdk Assistant)
        tool_names = [tool.name for tool in tools] if tools else []
        log.info(f"[_execute_chat_agent] Tool names: {tool_names}")

        # Build chat history messages
        chat_history_messages = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                chat_history_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                chat_history_messages.append(AIMessage(content=content))

        if chat_history_messages:
            log.info(f"[_execute_chat_agent] Chat history has {len(chat_history_messages)} messages")

        # Create YAML schema for LangGraph
        # Note: Don't include chat_history_messages in the schema - they can't be YAML serialized
        # Instead, pass them when invoking the agent
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
                    'system': {
                        'type': 'fixed',
                        'value': system_prompt
                    },
                    'task': {
                        'type': 'variable',
                        'value': 'input'
                    },
                    'chat_history': {
                        'type': 'variable',
                        'value': 'messages'
                    }
                },
                'step_limit': DEFAULT_MAX_ITERATIONS,
                'input': ['messages'],
                'output': ['messages'],
                'transition': 'END'
            }],
            'entry_point': 'agent'
        }

        # Add tools if present
        if tools:
            schema_dict['nodes'][0]['tool_names'] = tool_names

        yaml_schema = yaml.dump(schema_dict, default_flow_style=False, allow_unicode=True)
        log.debug(f"[_execute_chat_agent] YAML schema:\n{yaml_schema}")

        # Create memory checkpointer
        checkpointer = MemorySaver()

        # Execute
        try:
            # Create LangGraph agent using alita-sdk's create_graph
            agent = create_graph(
                client=llm,
                yaml_schema=yaml_schema,
                tools=tools,
                memory=checkpointer,
                store=None,
                debug=False,
                for_subgraph=False,
                steps_limit=DEFAULT_MAX_ITERATIONS
            )

            log.info("[_execute_chat_agent] LangGraph agent created, invoking...")

            # Invoke the agent with callbacks
            thread_id = str(uuid.uuid4())
            callbacks = [lc_callback]
            if langfuse_callback:
                callbacks.append(langfuse_callback)
                log.info("[_execute_chat_agent] Langfuse callback added for tracing")

            config = {
                "configurable": {"thread_id": thread_id},
                "callbacks": callbacks,
            }

            result = agent.invoke(
                {"input": prompt, "messages": chat_history_messages},
                config=config
            )

            # Extract answer from result messages
            answer = ""
            tool_calls = []

            if "messages" in result:
                messages = result["messages"]
                # Get the last AI message as the answer
                for msg in reversed(messages):
                    if hasattr(msg, 'content') and isinstance(msg, AIMessage):
                        content = msg.content
                        # Handle content that may be a list of content blocks (LangGraph format)
                        if isinstance(content, list):
                            # Extract text from content blocks
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

                # Extract tool calls and their results from messages
                # First, collect all tool results by tool_call_id
                from langchain_core.messages import ToolMessage
                tool_results = {}
                for msg in messages:
                    if isinstance(msg, ToolMessage):
                        tool_call_id = getattr(msg, 'tool_call_id', None)
                        if tool_call_id:
                            content = msg.content
                            # Handle content that may be a list
                            if isinstance(content, list):
                                content_parts = []
                                for block in content:
                                    if isinstance(block, dict) and block.get('type') == 'text':
                                        content_parts.append(block.get('text', ''))
                                    elif isinstance(block, str):
                                        content_parts.append(block)
                                content = ''.join(content_parts)
                            tool_results[tool_call_id] = str(content)[:500] if content else ""

                # Now extract tool calls and match with results
                for msg in messages:
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_call_id = tc.get("id", "")
                            tool_args = tc.get("args", {})
                            # Format args nicely
                            if isinstance(tool_args, dict):
                                # Filter out empty/default args
                                filtered_args = {k: v for k, v in tool_args.items()
                                               if v and k != "__arg1"}
                                input_str = json.dumps(filtered_args, indent=2) if filtered_args else "{}"
                            else:
                                input_str = str(tool_args)

                            tool_calls.append({
                                "tool": tc.get("name", "unknown"),
                                "input": input_str[:500],
                                "output_preview": tool_results.get(tool_call_id, "")[:500],
                            })

            # Fallback to output key if no messages
            if not answer and "output" in result:
                answer = result.get("output", "")

            # Extract citations from the answer
            citations = self._extract_citations_from_answer(answer)

            return {
                "answer": answer,
                "citations": citations,
                "tool_calls": tool_calls,
                "error": None,
            }

        except Exception as e:
            log.exception(f"[_execute_chat_agent] Error: {e}")
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": str(e),
            }

    @web.method()
    def _extract_citations_from_answer(self, answer: str) -> List[Dict[str, Any]]:
        """
        Extract citation references from the answer text.

        This is a basic implementation that looks for patterns like:
        - "in ClassName"
        - "from file.py"
        - "Source: xyz"
        """
        import re

        citations = []

        # Pattern: Source: toolkit - path
        source_pattern = r"Source:\s*(\w+)(?:\s*[-:]\s*([^\n]+))?"
        for match in re.finditer(source_pattern, answer):
            citations.append({
                "source_toolkit": match.group(1),
                "file_path": match.group(2).strip() if match.group(2) else None,
            })

        # Pattern: in `ClassName` or `function_name`
        entity_pattern = r"`([A-Z][a-zA-Z0-9_]+)`|`([a-z_][a-zA-Z0-9_]+)`"
        for match in re.finditer(entity_pattern, answer):
            entity_name = match.group(1) or match.group(2)
            if entity_name and len(entity_name) > 2:
                citations.append({
                    "entity_name": entity_name,
                })

        # Deduplicate
        seen = set()
        unique_citations = []
        for c in citations:
            key = json.dumps(c, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_citations.append(c)

        return unique_citations[:20]  # Limit to 20 citations

    @web.method()
    def _get_source_toolkit_tools(
        self,
        project_id: int,
        toolkit_id: int,
        alita_client,
        settings: Dict[str, Any],
        touched_entities: List[Dict[str, Any]] = None,
    ) -> List:
        """
        Get read-only tools from all source toolkits.

        Reads sources_status.json to find configured sources, fetches their
        toolkit configurations, instantiates them using alita-sdk, and
        filters to only include read-only operations.

        Args:
            project_id: Project ID
            toolkit_id: Inventory toolkit ID
            alita_client: AlitaClient instance for API calls
            settings: Inventory toolkit settings (for LLM configuration)
            touched_entities: Shared list to track accessed files/entities

        Returns:
            List of LangChain tools (read-only operations only)
        """
        touched_entities = touched_entities or []
        import os
        import requests as http_requests

        tools = []

        # 1. Read sources_status.json to get source toolkit info
        sources_status_path = f"/data/graphs/{project_id}/{toolkit_id}/sources_status.json"
        if not os.path.exists(sources_status_path):
            log.info(f"[_get_source_toolkit_tools] No sources_status.json found at {sources_status_path}")
            return tools

        try:
            with open(sources_status_path, 'r') as f:
                sources_status = json.load(f)
        except Exception as e:
            log.warning(f"[_get_source_toolkit_tools] Failed to read sources_status.json: {e}")
            return tools

        if not sources_status:
            log.info("[_get_source_toolkit_tools] No sources configured")
            return tools

        # sources_status.json has nested structure: {"sources": {...}, "last_modified": ...}
        sources = sources_status.get("sources", {})
        if not sources:
            log.info("[_get_source_toolkit_tools] No sources in sources_status")
            return tools

        log.info(f"[_get_source_toolkit_tools] Found {len(sources)} sources")

        # 2. For each source, fetch toolkit config and instantiate tools
        for source_key, source_info in sources.items():
            source_toolkit_id = source_info.get("toolkit_id")
            source_toolkit_type = source_info.get("toolkit_type")
            source_toolkit_name = source_info.get("toolkit_name", source_toolkit_type)

            if not source_toolkit_id:
                log.warning(f"[_get_source_toolkit_tools] Source {source_key} has no toolkit_id")
                continue

            try:
                # Fetch toolkit configuration from platform
                toolkit_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{source_toolkit_id}"
                resp = http_requests.get(toolkit_url, headers=alita_client.headers, verify=False)

                if not resp.ok:
                    log.warning(f"[_get_source_toolkit_tools] Failed to fetch toolkit {source_toolkit_id}: {resp.status_code}")
                    continue

                toolkit_data = resp.json()
                toolkit_type = toolkit_data.get("type", source_toolkit_type)
                toolkit_settings = toolkit_data.get("settings", {})

                log.info(f"[_get_source_toolkit_tools] Instantiating toolkit {source_toolkit_name} (type={toolkit_type}, id={source_toolkit_id})")

                # Build toolkit config for alita-sdk
                toolkit_config = {
                    "id": int(source_toolkit_id),
                    "type": toolkit_type,
                    "toolkit_name": source_toolkit_name,
                    "name": source_toolkit_name,
                    "settings": toolkit_settings,
                }

                # Get LLM model from inventory toolkit settings
                llm_model = (
                    settings.get("toolkit_configuration_llm_model") or
                    settings.get("llm_model") or
                    "gpt-4o-mini"  # Fallback only if not configured
                )

                # Get LLM for tools that need it
                llm = alita_client.get_llm(
                    model_name=llm_model,
                    model_config={"temperature": DEFAULT_LLM_TEMPERATURE, "max_tokens": DEFAULT_TOOL_LLM_MAX_TOKENS},
                )

                # Instantiate toolkit tools using alita-sdk
                source_tools = self._instantiate_toolkit_tools(
                    toolkit_config=toolkit_config,
                    llm=llm,
                    alita_client=alita_client,
                )

                if source_tools:
                    # Filter to only include read-only tools
                    read_only_tools = self._filter_read_only_tools(source_tools, source_toolkit_name)
                    # Wrap tools to track file accesses
                    wrapped_tools = self._wrap_tools_with_tracking(
                        read_only_tools,
                        source_toolkit_name,
                        touched_entities,
                    )
                    tools.extend(wrapped_tools)
                    log.info(f"[_get_source_toolkit_tools] Added {len(wrapped_tools)} read-only tools from {source_toolkit_name}")

            except Exception as e:
                log.exception(f"[_get_source_toolkit_tools] Error processing source {source_key}: {e}")
                continue

        return tools

    @web.method()
    def _instantiate_toolkit_tools(
        self,
        toolkit_config: Dict[str, Any],
        llm,
        alita_client,
    ) -> List:
        """
        Instantiate tools from a toolkit configuration using alita-sdk.

        Args:
            toolkit_config: Toolkit configuration dict
            llm: LLM instance
            alita_client: AlitaClient instance

        Returns:
            List of instantiated LangChain tools
        """
        try:
            from alita_sdk.runtime.toolkits.tools import get_tools

            # Build tools_list format expected by get_tools()
            tools_list = [toolkit_config]

            # Instantiate tools
            tools = get_tools(
                tools_list,
                alita_client=alita_client,
                llm=llm,
            )

            return tools

        except ImportError as e:
            log.warning(f"[_instantiate_toolkit_tools] alita-sdk not available: {e}")
            return []
        except Exception as e:
            log.exception(f"[_instantiate_toolkit_tools] Error instantiating toolkit: {e}")
            return []

    @web.method()
    def _filter_read_only_tools(
        self,
        tools: List,
        toolkit_name: str,
    ) -> List:
        """
        Filter tools to only include read-only operations.

        Uses name patterns to identify read vs write operations.
        Patterns are defined in constants.py.

        Args:
            tools: List of LangChain tools
            toolkit_name: Name of the source toolkit (for logging)

        Returns:
            Filtered list of read-only tools
        """
        read_only_tools = []

        for tool in tools:
            tool_name = tool.name.lower() if hasattr(tool, 'name') else ''

            # Check if it's a read operation
            is_read = any(tool_name.startswith(prefix) for prefix in READ_ONLY_PREFIXES)

            # Check if it contains write patterns
            has_write_pattern = any(pattern in tool_name for pattern in WRITE_OPERATION_PATTERNS)

            if is_read and not has_write_pattern:
                read_only_tools.append(tool)
                log.debug(f"[_filter_read_only_tools] Including: {tool_name}")
            else:
                log.debug(f"[_filter_read_only_tools] Excluding: {tool_name} (is_read={is_read}, has_write={has_write_pattern})")

        log.info(f"[_filter_read_only_tools] {toolkit_name}: {len(read_only_tools)}/{len(tools)} tools are read-only")

        return read_only_tools

    @web.method()
    def _wrap_tools_with_tracking(
        self,
        tools: List,
        toolkit_name: str,
        touched_entities: List[Dict[str, Any]],
    ) -> List:
        """
        Pass-through for tools - file tracking now happens via LangChain callbacks.

        Previously this method wrapped tools to intercept file accesses, but that
        approach broke toolkit initialization (especially GitHub toolkit). Now
        tracking happens in InventoryChatCallback.on_tool_start instead.

        Args:
            tools: List of LangChain tools
            toolkit_name: Name of the source toolkit (unused, kept for compatibility)
            touched_entities: Shared list (unused, tracking via callback now)

        Returns:
            Original tools list unchanged
        """
        return tools
