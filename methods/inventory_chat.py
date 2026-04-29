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

# Allow nested event loops - required when running async code in environments
# that already have an event loop (like pylon/Flask with threading)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not installed, will fail if nested loops are used

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
    SOURCE_EXTENSIONS,
    get_compatible_types,
)
from ..utils.langfuse_callback import (
    fetch_langfuse_config,
    create_langfuse_callback,
    flush_langfuse_callback,
    langfuse_trace_context,
)


class ChatCancelledException(Exception):
    """Raised when a chat session is cancelled by the user."""
    pass


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
        is_cancelled: Optional[Callable[[], bool]] = None,
    ):
        """
        Initialize callback handler.

        Args:
            emit_fn: Function to emit events (receives event_type, data)
            session_id: Unique session ID for this chat
            conversation_id: Optional conversation ID for history
            file_tracking_fn: Optional function to track file accesses (tool_name, input_str)
            is_cancelled: Optional function to check if chat has been cancelled
        """
        self.emit_fn = emit_fn
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.file_tracking_fn = file_tracking_fn
        self.is_cancelled = is_cancelled or (lambda: False)
        self.tool_runs = {}  # Track active tool runs
        self.start_time = time.time()
        # Token tracking - accumulate across all LLM calls
        self.total_tokens_in = 0
        self.total_tokens_out = 0

    def check_cancelled(self):
        """Check if cancelled and raise exception if so."""
        if self.is_cancelled():
            raise ChatCancelledException("Chat cancelled by user")

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
        # Check for cancellation before starting LLM
        self.check_cancelled()

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

        # Extract token usage from response
        tokens_in = 0
        tokens_out = 0
        if response:
            # Method 1: Check llm_output (common format)
            llm_output = getattr(response, 'llm_output', {}) or {}
            token_usage = llm_output.get('token_usage', {}) or {}
            if token_usage:
                tokens_in = token_usage.get('prompt_tokens', 0) or token_usage.get('input_tokens', 0) or 0
                tokens_out = token_usage.get('completion_tokens', 0) or token_usage.get('output_tokens', 0) or 0

            # Method 2: Check response_metadata in generations
            if tokens_in == 0 and tokens_out == 0:
                if hasattr(response, 'generations') and response.generations:
                    for gen_list in response.generations:
                        for gen in gen_list:
                            if hasattr(gen, 'message'):
                                msg = gen.message
                                # Check usage_metadata (LangChain standard)
                                # Note: usage_metadata can be a dict or an object with attributes
                                if hasattr(msg, 'usage_metadata') and msg.usage_metadata:
                                    usage = msg.usage_metadata
                                    # Handle dict format (common in LangChain)
                                    if isinstance(usage, dict):
                                        tokens_in += usage.get('input_tokens', 0) or 0
                                        tokens_out += usage.get('output_tokens', 0) or 0
                                    else:
                                        # Handle object format
                                        tokens_in += getattr(usage, 'input_tokens', 0) or 0
                                        tokens_out += getattr(usage, 'output_tokens', 0) or 0
                                # Check response_metadata
                                if hasattr(msg, 'response_metadata') and msg.response_metadata:
                                    metadata = msg.response_metadata
                                    usage = metadata.get('usage', {}) or metadata.get('token_usage', {}) or {}
                                    if usage:
                                        tokens_in += usage.get('input_tokens', 0) or usage.get('prompt_tokens', 0) or 0
                                        tokens_out += usage.get('output_tokens', 0) or usage.get('completion_tokens', 0) or 0

        # Debug log token extraction
        if tokens_in > 0 or tokens_out > 0:
            log.debug(f"[on_llm_end] Extracted tokens: in={tokens_in}, out={tokens_out}")

        # Accumulate tokens
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out

        log.debug(f"[on_llm_end] Total accumulated: in={self.total_tokens_in}, out={self.total_tokens_out}")

        self.emit("llm_end", {
            "run_id": run_id,
            "output": output_content[:1000] if output_content else "",  # Truncate output
            "duration_ms": int(duration * 1000),
            "has_thinking": bool(thinking_content),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
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
        # Check for cancellation before starting tool
        self.check_cancelled()

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

    def get_token_usage(self) -> Dict[str, int]:
        """Get accumulated token usage across all LLM calls."""
        return {
            "tokens_in": self.total_tokens_in,
            "tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
        }


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
        is_cancelled: Optional[Callable[[], bool]] = None,
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
            is_cancelled: Optional callback to check if chat should be cancelled

        Returns:
            Dict with:
            - answer: The LLM's response
            - citations: List of source citations
            - tool_calls: List of tools that were called
            - error: Error message if failed
            - cancelled: True if chat was cancelled
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

        # Create callback handler with file tracking and cancellation support
        callback = InventoryChatCallback(
            emit_fn=emit_fn or (lambda t, d: None),  # No-op if no emit function
            session_id=session_id,
            conversation_id=conversation_id,
            file_tracking_fn=track_file_from_tool,
            is_cancelled=is_cancelled,
        )

        # Initialize Langfuse variables for finally block
        langfuse_client = None
        langfuse_callback = None

        try:
            # 1. Get EliteAClient for platform API
            elitea_client = self._get_elitea_client(project_id)
            if not elitea_client:
                return {
                    "answer": "",
                    "citations": [],
                    "tool_calls": [],
                    "error": "Platform API not configured",
                }

            # 2. Fetch inventory toolkit settings
            import requests as http_requests
            toolkit_url = f"{elitea_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}"
            resp = http_requests.get(toolkit_url, headers=elitea_client.headers, verify=False)

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
            langfuse_config = fetch_langfuse_config(elitea_client)
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
                elitea_client=elitea_client,
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

            log.info(f"[inventory_chat] Built {len(tools)} tools (all)")

            # 6. Route query — select focused tool subset + compose prompt
            from ..routing import QueryRouter, ToolSelector, PromptBuilder, GraphProfile

            wrapper = self._get_or_create_wrapper(graph_path, {
                "configuration": {
                    "project_id": project_id,
                    "application_id": toolkit_id,
                    "settings": settings,
                }
            })
            graph_stats = wrapper._knowledge_graph.get_stats()
            has_source = any(
                name not in {
                    "search_knowledge_graph", "semantic_search", "get_entity_details",
                    "get_related_entities", "query_graph", "query_pattern",
                    "get_pattern_vocabulary", "list_entity_types", "impact_analysis",
                }
                for name in tools
            )
            profile = GraphProfile.from_stats(graph_stats, has_source_tools=has_source)

            # Create a cheap LLM instance for intent classification fallback.
            # Only used when regex + embeddings can't decide (~10-15% of queries).
            try:
                routing_llm = elitea_client.get_llm(
                    model_name=llm_model,
                    model_config={"temperature": 0, "max_tokens": 20},
                )
            except Exception:
                routing_llm = None

            strategy = QueryRouter.classify(prompt, llm=routing_llm)

            focused_tools = ToolSelector.select(strategy, tools, profile)

            # Format filter text for prompt
            filter_desc_route = []
            depth = filters.get("depth", 2)
            max_nodes = filters.get("max_nodes", 500)
            filter_desc_route.append(f"Depth: {depth} (relationship hops to traverse)")
            filter_desc_route.append(f"Max nodes: {max_nodes} (maximum results to return)")
            if filters.get("entity_types"):
                filter_desc_route.append(f"Entity types: {', '.join(filters['entity_types'])}")
            if filters.get("sources"):
                filter_desc_route.append(f"Sources: {', '.join(filters['sources'])}")
            if filters.get("layers"):
                filter_desc_route.append(f"Layers: {', '.join(filters['layers'])}")
            filter_text_route = "\n".join(filter_desc_route)

            focused_tool_names = [t.name for t in focused_tools]
            composed_prompt = PromptBuilder.compose(strategy, focused_tool_names, filter_text_route)

            log.info(
                f"[inventory_chat] Strategy: {strategy} | "
                f"tools: {len(focused_tools)}/{len(tools)} | "
                f"prompt chars: {len(composed_prompt)}"
            )

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

            llm = elitea_client.get_llm(
                model_name=llm_model,
                model_config=model_config,
            )

            # 8. Build the agent and execute with Langfuse tracing
            with langfuse_trace_context(langfuse_trace_attrs):
                result = self._execute_chat_agent(
                    llm=llm,
                    tools=focused_tools,
                    prompt=prompt,
                    history=history,
                    filters=filters,
                    callback=callback,
                    langfuse_callback=langfuse_callback,
                    system_prompt=composed_prompt,
                )

            # Add touched entities and routing info to result
            result["touched_entities"] = touched_entities
            result["strategy"] = strategy
            log.info(f"[inventory_chat] Touched {len(touched_entities)} entities")
            log.info(f"[inventory_chat] Token usage: in={result.get('tokens_in', 0)}, out={result.get('tokens_out', 0)}")

            callback.emit("chat_complete", {
                "answer_length": len(result.get("answer", "")),
                "citations_count": len(result.get("citations", [])),
                "touched_entities_count": len(touched_entities),
                "tokens_in": result.get("tokens_in", 0),
                "tokens_out": result.get("tokens_out", 0),
                "strategy": strategy,
                "tools_count": len(focused_tools),
            })

            return result

        except ChatCancelledException:
            log.info(f"[inventory_chat] Chat cancelled: session_id={session_id}")
            token_usage = callback.get_token_usage()

            callback.emit("chat_cancelled", {"message": "Chat cancelled by user"})

            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "touched_entities": touched_entities,
                "error": None,
                "cancelled": True,
                "tokens_in": token_usage["tokens_in"],
                "tokens_out": token_usage["tokens_out"],
            }

        except Exception as e:
            log.exception(f"[inventory_chat] Error: {e}")
            error_msg = str(e)
            token_usage = callback.get_token_usage()

            callback.emit("chat_error", {"error": error_msg})

            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "touched_entities": [],
                "error": error_msg,
                "tokens_in": token_usage["tokens_in"],
                "tokens_out": token_usage["tokens_out"],
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
        elitea_client,
        touched_entities: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build tools for the chat agent.

        Creates:
        1. search_knowledge_graph — graph search with filters
        2. semantic_search — embedding-based similarity (when embeddings exist)
        3. get_entity_details — full entity info
        4. get_related_entities — neighbours / relationships
        5. query_graph — natural-language to Cypher-like graph traversal
        6. query_pattern — structural pattern matching
        7. get_pattern_vocabulary — available node/edge types for patterns
        8. list_entity_types — type summary statistics
        9. impact_analysis — what-if change impact analysis
        10. Source toolkit tools (read-only operations from GitHub, etc.)

        Args:
            touched_entities: Shared list to collect entities accessed during execution

        Returns:
            Dict mapping tool name → Tool object
        """
        from langchain_core.tools.structured import StructuredTool
        from langchain_core.tools.simple import Tool
        from pydantic import BaseModel, Field
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
            elitea_client=elitea_client,
            settings=settings,
            touched_entities=touched_entities,
        )
        if source_tools:
            tools.extend(source_tools)
            log.info(f"[_build_chat_tools] Added {len(source_tools)} source toolkit tools")

        # Get the retrieval wrapper (embedding model is local, initialized lazily)
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

        # 1. Graph Search Tool - Args schema for StructuredTool
        class SearchGraphInput(BaseModel):
            """Input for search_knowledge_graph tool."""
            query: str = Field(description="The search query to find entities in the knowledge graph")
            top_k: int = Field(default=20, description="Maximum number of results to return (default: 20, max: 50)")

        def search_graph(query: str, top_k: int = 20) -> str:
            """Search the knowledge graph for entities matching the query. Returns tree structure."""
            try:
                # Apply limits
                top_k = min(top_k, 50)  # Cap at 50
                top_k = min(top_k, default_max_nodes)  # Also respect default_max_nodes
                max_depth = default_depth

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

                # Add graph traversal hint
                if results:
                    output += f"\n💡 Use get_related_entities(\"Name (type)\") to explore relationships for any result above."

                return output

            except Exception as e:
                log.exception(f"[search_graph] Error: {e}")
                return f"Error searching graph: {e}"

        tools.append(StructuredTool(
            name="search_knowledge_graph",
            func=search_graph,
            description=TOOL_DESCRIPTIONS["search_knowledge_graph"],
            args_schema=SearchGraphInput,
        ))

        # 1b. Semantic Search Tool — conditionally added when embeddings are available
        stats = wrapper._knowledge_graph.get_stats()
        if stats.get('has_embeddings'):
            class SemanticSearchInput(BaseModel):
                """Input for semantic_search tool."""
                query: str = Field(description="Natural language query for semantic similarity search (e.g., 'authentication logic', 'payment processing')")
                top_k: int = Field(default=10, description="Maximum number of results (default: 10)")

            def semantic_search_func(query: str, top_k: int = 10) -> str:
                """Search entities by semantic similarity using embeddings."""
                try:
                    top_k = min(top_k, 50)
                    result = wrapper.semantic_search(
                        query=query,
                        top_k=top_k,
                        entity_type=filter_entity_types[0] if len(filter_entity_types) == 1 else None,
                        layer=filter_layers[0] if len(filter_layers) == 1 else None,
                    )

                    # Track entities from results for graph context
                    if result and not result.startswith("No ") and not result.startswith("Semantic search"):
                        for line in result.split('\n'):
                            if line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
                                # Parse entity name from result line
                                if '**' in line:
                                    parts = line.split('**')
                                    if len(parts) >= 2:
                                        ename = parts[1].strip()
                                        if ename:
                                            track_entity({'name': ename, 'type': 'unknown'})

                    return result
                except Exception as e:
                    log.exception(f"[semantic_search] Error: {e}")
                    return f"Error in semantic search: {e}"

            tools.append(StructuredTool(
                name="semantic_search",
                func=semantic_search_func,
                description=TOOL_DESCRIPTIONS.get(
                    "semantic_search",
                    "Search entities by semantic similarity. Use for concept-level queries like 'authentication logic' or 'error handling patterns'."
                ),
                args_schema=SemanticSearchInput,
            ))
            log.info(f"[_build_chat_tools] Semantic search tool enabled ({stats.get('embeddings_count', 0)} entities with embeddings)")

        # Helper to parse "Name (type)" format from search results
        def parse_entity_reference(entity_ref: str):
            """
            Parse entity reference string.

            Supports formats from search results:
            - "Name" -> returns (name, None)
            - "Name (type)" -> returns (name, type)
            - "Name (type) @ source" -> returns (name, type)
            - "Name (type) @ source - file_path" -> returns (name, type)

            The parser strips everything after " @ " before parsing Name (type).
            """
            entity_ref = entity_ref.strip()

            # Strip source and file path info: "Name (type) @ source - path" -> "Name (type)"
            if ' @ ' in entity_ref:
                entity_ref = entity_ref.split(' @ ')[0].strip()

            # Check if ends with "(type)" pattern
            if entity_ref.endswith(')'):
                # Find the last opening parenthesis
                paren_start = entity_ref.rfind('(')
                if paren_start > 0:  # Must have something before the parenthesis
                    name = entity_ref[:paren_start].strip()
                    type_str = entity_ref[paren_start + 1:-1].strip()
                    if name and type_str:
                        return name, type_str

            # Plain name without type
            return entity_ref, None

        def smart_find_entity(name: str, entity_type: str = None):
            """
            Smart entity lookup with fallbacks for common naming variations.

            Generic approach that works across any codebase:
            1. Exact name match (case-sensitive)
            2. Case-insensitive name match
            3. Name as file with common extensions
            4. Semantic search fallback

            Type matching uses TYPE_COMPATIBILITY from constants since LLM-extracted
            entities may use semantic types (concept, fact) instead of code types.

            Returns: (best_match_entity, all_alternatives)
            """
            kg = wrapper._knowledge_graph

            def type_matches(requested: str, actual: str) -> bool:
                """Check if types match, considering compatibility."""
                if not requested:
                    return True
                act = actual.lower() if actual else ''
                if not act:
                    return False
                compatible = get_compatible_types(requested)
                return act in compatible

            def find_best_match(entities, requested_type):
                """Find best match from entities, preferring exact type match."""
                if not entities:
                    return None
                if not requested_type:
                    return entities[0]

                # Pass 1: exact type match
                for e in entities:
                    if e.get('type', '').lower() == requested_type.lower():
                        return e

                # Pass 2: compatible type match
                for e in entities:
                    if type_matches(requested_type, e.get('type', '')):
                        return e

                # Pass 3: prefer semantic entities over source files
                file_types = {'source_file', 'document_file', 'config_file', 'web_file', 'file'}
                for e in entities:
                    if e.get('type', '').lower() not in file_types:
                        return e

                return entities[0] if entities else None

            all_matches = []

            # 1. Try exact name match
            matches = kg.find_all_entities_by_name(name)
            all_matches.extend(matches)
            result = find_best_match(matches, entity_type)
            if result:
                return result, all_matches

            # 2. Try case-insensitive search via semantic search
            # This handles naming variations across languages
            search_results = kg.search(name, top_k=15)
            if search_results:
                # Filter to entities whose name closely matches (contains the search term)
                name_lower = name.lower()
                for sr in search_results:
                    entity = sr['entity']
                    entity_name = entity.get('name', '').lower()
                    # Check if names are similar (contains or close match)
                    if name_lower in entity_name or entity_name in name_lower:
                        if entity not in all_matches:
                            all_matches.append(entity)

                result = find_best_match(all_matches, entity_type)
                if result:
                    return result, all_matches

            # 3. Try as source file with common extensions
            # Strip existing extension first if present
            base_name = name
            for ext in SOURCE_EXTENSIONS:
                if name.lower().endswith(ext):
                    base_name = name[:-len(ext)]
                    break

            for ext in SOURCE_EXTENSIONS:
                try_name = f"{base_name}{ext}"
                if try_name.lower() != name.lower():  # Don't retry the same name
                    matches = kg.find_all_entities_by_name(try_name)
                    for m in matches:
                        if m not in all_matches:
                            all_matches.append(m)
                    if matches:
                        result = find_best_match(matches, entity_type)
                        if result:
                            return result, all_matches

            # 4. Broader semantic search as final fallback
            if not all_matches and search_results:
                all_matches = [sr['entity'] for sr in search_results]
                result = find_best_match(all_matches, entity_type)
                if result:
                    return result, all_matches

            # Deduplicate by entity ID
            seen_ids = set()
            unique_matches = []
            for e in all_matches:
                eid = e.get('id')
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    unique_matches.append(e)

            return None, unique_matches

        # 2. Get Entity Details Tool
        def get_entity_details(entity_name: str) -> str:
            """Get detailed information about a specific entity."""
            try:
                # Parse input: support both "Name" and "Name (type)" formats
                parsed_name, parsed_type = parse_entity_reference(entity_name)

                # Use smart lookup with fallbacks
                entity, alternatives = smart_find_entity(parsed_name, parsed_type)

                if not entity:
                    # Show helpful message with suggestions
                    if alternatives:
                        suggestions = [f"  - {e.get('name')} ({e.get('type')})" for e in alternatives[:5]]
                        return f"Entity '{parsed_name}' not found. Did you mean:\n" + "\n".join(suggestions)
                    return f"Entity '{parsed_name}' not found. Try search_knowledge_graph to find the correct name."

                # Track this entity as touched
                track_entity(entity)

                output = f"# {entity.get('name')}\n\n"

                # Note if type differs from requested
                actual_type = entity.get('type', 'unknown')
                if parsed_type and parsed_type.lower() != actual_type.lower():
                    output += f"**Note:** Requested type '{parsed_type}' not found. Showing '{actual_type}' instead.\n\n"

                output += f"**Type:** {actual_type}\n"
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
                # Parse input: support both "Name" and "Name (type)" formats
                parsed_name, parsed_type = parse_entity_reference(entity_name)

                # Use smart lookup with fallbacks
                entity, alternatives = smart_find_entity(parsed_name, parsed_type)

                if not entity:
                    # Show helpful message with suggestions
                    if alternatives:
                        suggestions = [f"  - {e.get('name')} ({e.get('type')})" for e in alternatives[:5]]
                        return f"Entity '{parsed_name}' not found. Did you mean:\n" + "\n".join(suggestions)
                    return f"Entity '{parsed_name}' not found. Try search_knowledge_graph to find the correct name."

                # Track the main entity
                track_entity(entity)

                entity_id = entity.get('id')
                if not entity_id:
                    return "Entity has no ID for relation lookup."

                relations = wrapper._knowledge_graph.get_relations(entity_id, direction="both")

                # Build header with actual entity info
                actual_name = entity.get('name', parsed_name)
                actual_type = entity.get('type', 'unknown')
                output = f"# Relations for {actual_name} ({actual_type})\n\n"

                # Note if we found a different entity than requested
                if parsed_type and parsed_type.lower() != actual_type.lower():
                    output += f"_Note: Found '{actual_type}' instead of requested '{parsed_type}'_\n\n"

                if not relations:
                    output += "No relations found."
                    return output

                # Group by relation type
                by_type = {}
                for rel in relations:
                    rel_type = rel.get('relation_type', 'RELATED')
                    if rel_type not in by_type:
                        by_type[rel_type] = []
                    by_type[rel_type].append(rel)

                for rel_type, rels in by_type.items():
                    output += f"## {rel_type} ({len(rels)})\n"
                    for rel in rels[:15]:
                        # Get the related entity's full info
                        if rel['source'] == entity_id:
                            related_id = rel['target']
                            direction = "→"
                        else:
                            related_id = rel['source']
                            direction = "←"

                        # Look up the related entity to get name and type
                        related_entity = wrapper._knowledge_graph.get_entity(related_id)
                        if related_entity:
                            related_name = related_entity.get('name', related_id)
                            related_type = related_entity.get('type', 'unknown')
                            file_path = related_entity.get('file_path', '')
                            description = related_entity.get('description', '')

                            # Format: → EntityName (type) - file_path
                            output += f"- {direction} **{related_name}** ({related_type})"
                            if file_path:
                                output += f" - `{file_path}`"
                            output += "\n"

                            # Add description/brief if available (truncate to keep output readable)
                            if description:
                                # Truncate long descriptions
                                brief = description[:150].strip()
                                if len(description) > 150:
                                    brief += "..."
                                # Indent description under the entity
                                output += f"  _{brief}_\n"

                            # Track the related entity
                            track_entity(related_entity)
                        else:
                            output += f"- {direction} {related_id} (not found)\n"

                    if len(rels) > 15:
                        output += f"  ...and {len(rels) - 15} more\n"
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

        # Helper to parse JQL-like query syntax
        def parse_graph_query(query_str: str) -> dict:
            """
            Parse JQL-like query string into parameters dict.

            Syntax:
                type:class,function    - Entity types (comma-separated)
                layer:code,service     - Layers to filter
                file:*.py,src/*.ts     - File patterns
                name:UserService       - Name substring filter
                name:"User Service"    - Quoted for spaces
                related:EntityName     - Find entities related to this
                related:"Name (type)"  - With type qualifier
                related:"Name (type) @ source - path"  - Full search result format
                rel:calls,imports      - Relation types filter
                dir:in|out|both        - Relation direction
                has_rel:true|false     - Has relations filter
                limit:50               - Max results

            Examples:
                type:class layer:code
                related:UserService type:function dir:out
                related:"read_file (method) @ sdk - artifact.py" type:class
                file:*.py name:test limit:100

            Plain text without operators is treated as name filter.
            Copy-paste from search results is supported for related: operator.
            """
            import shlex

            params = {}
            query_str = query_str.strip()

            if not query_str:
                return params

            # Known operators
            operators = {
                'type': 'types',
                'types': 'types',
                'layer': 'layers',
                'layers': 'layers',
                'file': 'files',
                'files': 'files',
                'name': 'name',
                'text': 'name',
                'query': 'name',
                'related': 'related_to',
                'related_to': 'related_to',
                'rel': 'relation_types',
                'relation': 'relation_types',
                'relation_types': 'relation_types',
                'dir': 'direction',
                'direction': 'direction',
                'has_rel': 'has_relations',
                'has_relations': 'has_relations',
                'limit': 'limit',
            }

            # List-type parameters (comma-separated values)
            list_params = {'types', 'layers', 'files', 'relation_types'}

            # Try to parse with shlex for proper quote handling
            try:
                tokens = shlex.split(query_str)
            except ValueError:
                # Fallback to simple split if shlex fails
                tokens = query_str.split()

            unmatched_tokens = []

            for token in tokens:
                if ':' in token:
                    # Split on first colon only
                    key, value = token.split(':', 1)
                    key = key.lower().strip()

                    if key in operators:
                        param_name = operators[key]

                        if param_name in list_params:
                            # Parse comma-separated values
                            values = [v.strip() for v in value.split(',') if v.strip()]
                            if param_name in params:
                                params[param_name].extend(values)
                            else:
                                params[param_name] = values
                        elif param_name == 'limit':
                            try:
                                params[param_name] = int(value)
                            except ValueError:
                                pass
                        elif param_name == 'has_relations':
                            params[param_name] = value.lower() in ('true', 'yes', '1')
                        else:
                            params[param_name] = value
                    else:
                        # Unknown operator - treat whole token as part of name
                        unmatched_tokens.append(token)
                else:
                    # No operator - collect for name filter
                    unmatched_tokens.append(token)

            # If there are unmatched tokens and no name set, use them as name filter
            if unmatched_tokens and 'name' not in params:
                params['name'] = ' '.join(unmatched_tokens)

            return params

        # 4. Query Graph Tool - structured queries without similarity search
        def query_graph(query_input: str) -> str:
            """Query the knowledge graph with structured filters (no similarity search)."""
            import json as json_module

            try:
                # Parse input - accept JSON, JQL-like syntax, or simple text
                params = {}
                query_input = query_input.strip()

                if query_input.startswith('{'):
                    # JSON input
                    try:
                        params = json_module.loads(query_input)
                    except json_module.JSONDecodeError:
                        return "Invalid JSON input. Use JQL syntax instead: type:class layer:code"
                else:
                    # JQL-like syntax
                    params = parse_graph_query(query_input)

                # Extract parameters
                entity_types = params.get("types", params.get("entity_types", []))
                layers = params.get("layers", [])
                file_patterns = params.get("files", params.get("file_patterns", []))
                text_filter = params.get("name", params.get("text", params.get("query", "")))
                has_relations = params.get("has_relations")
                limit = min(params.get("limit", 30), 100)

                # Relationship-based query: find entities related to a specific entity
                related_to = params.get("related_to")
                relation_types = params.get("relation_types", [])
                relation_direction = params.get("direction", "both")  # in, out, both

                # Handle relationship-based query first
                if related_to:
                    # Parse entity reference (supports "Name (type)" format)
                    parsed_name, parsed_type = parse_entity_reference(related_to)

                    # Find all entities with this name to provide helpful feedback
                    all_matches = wrapper._knowledge_graph.find_all_entities_by_name(parsed_name)

                    if parsed_type:
                        # Filter by type
                        base_entity = None
                        for e in all_matches:
                            if e.get('type', '').lower() == parsed_type.lower():
                                base_entity = e
                                break
                        if not base_entity and all_matches:
                            available = [f"{e.get('name')} ({e.get('type', 'unknown')})" for e in all_matches[:10]]
                            return f"Entity '{parsed_name}' not found with type '{parsed_type}'.\n\nAvailable entities with this name:\n" + "\n".join(f"  - {a}" for a in available)
                    else:
                        # No type specified - check if multiple matches exist
                        if len(all_matches) > 1:
                            # Multiple matches - ask user to specify type
                            options = [f"{e.get('name')} ({e.get('type', 'unknown')}) @ {e.get('source_toolkit', '?')} - {e.get('file_path', '?')}" for e in all_matches[:10]]
                            return f"Multiple entities named '{parsed_name}' found. Please specify the type:\n\n" + "\n".join(f"  - {o}" for o in options) + "\n\nUse format: related:\"Name (type)\" or copy full reference from above."
                        elif all_matches:
                            base_entity = all_matches[0]
                        else:
                            base_entity = None

                    if not base_entity:
                        # Try a fuzzy search to suggest similar names
                        return f"Entity '{parsed_name}' not found. Try search_knowledge_graph to find the correct entity name."

                    entity_id = base_entity.get('id')
                    if not entity_id:
                        # This shouldn't happen, but provide helpful info if it does
                        return f"Entity '{base_entity.get('name')}' ({base_entity.get('type')}) has no ID. This may be a graph integrity issue."

                    # Get relations
                    relations = wrapper._knowledge_graph.get_relations(entity_id, direction=relation_direction)

                    # Filter by relation types if specified
                    if relation_types:
                        rel_types_lower = [rt.lower() for rt in relation_types]
                        relations = [r for r in relations if r.get('relation_type', '').lower() in rel_types_lower]

                    # Collect related entities
                    results = []
                    seen_ids = set()

                    for rel in relations:
                        # Get the related entity ID
                        if rel['source'] == entity_id:
                            related_id = rel['target']
                            rel_dir = "outgoing"
                        else:
                            related_id = rel['source']
                            rel_dir = "incoming"

                        if related_id in seen_ids:
                            continue
                        seen_ids.add(related_id)

                        related_entity = wrapper._knowledge_graph.get_entity(related_id)
                        if not related_entity:
                            continue

                        # Apply additional filters
                        etype = related_entity.get('type', '').lower()
                        elayer = related_entity.get('layer', '') or wrapper._knowledge_graph.TYPE_TO_LAYER.get(etype, '')

                        if entity_types:
                            types_lower = [t.lower() for t in entity_types]
                            # Also expand layer names to types
                            expanded_types = set(types_lower)
                            for t in types_lower:
                                if t in wrapper._knowledge_graph.LAYER_TYPE_MAPPING:
                                    expanded_types.update(wrapper._knowledge_graph.LAYER_TYPE_MAPPING[t])
                            if etype not in expanded_types:
                                continue

                        if layers:
                            layers_lower = [l.lower() for l in layers]
                            if elayer.lower() not in layers_lower:
                                continue

                        if text_filter:
                            name = related_entity.get('name', '').lower()
                            if text_filter.lower() not in name:
                                continue

                        results.append({
                            'entity': related_entity,
                            'relation_type': rel.get('relation_type', 'RELATED'),
                            'direction': rel_dir,
                        })

                        if len(results) >= limit:
                            break

                    # Format output
                    if not results:
                        return f"No related entities found for '{related_to}' matching filters."

                    base_name = base_entity.get('name', 'Unknown')
                    base_type = base_entity.get('type', '')
                    output = f"# Entities related to {base_name} ({base_type})\n"
                    output += f"Found {len(results)} results\n\n"

                    for r in results:
                        e = r['entity']
                        name = e.get('name', 'Unknown')
                        etype = e.get('type', '')
                        rel_type = r['relation_type']
                        direction = r['direction']
                        arrow = "→" if direction == "outgoing" else "←"

                        file_path = e.get('file_path', '')
                        source = e.get('source_toolkit', '')

                        output += f"- {arrow} [{rel_type}] **{name}** ({etype})"
                        if source:
                            output += f" @ {source}"
                        if file_path:
                            output += f" - {file_path}"
                        output += "\n"

                    return output

                # Standard structured query (no relationship base)
                results = wrapper._knowledge_graph.search_advanced(
                    query=text_filter if text_filter else None,
                    entity_types=entity_types if entity_types else None,
                    layers=layers if layers else None,
                    file_patterns=file_patterns if file_patterns else None,
                    has_relations=has_relations,
                    top_k=limit,
                )

                if not results:
                    filters_desc = []
                    if entity_types:
                        filters_desc.append(f"types={entity_types}")
                    if layers:
                        filters_desc.append(f"layers={layers}")
                    if file_patterns:
                        filters_desc.append(f"files={file_patterns}")
                    if text_filter:
                        filters_desc.append(f"text='{text_filter}'")
                    return f"No entities found matching filters: {', '.join(filters_desc) or 'none'}"

                # Format output - group by layer for readability
                by_layer = {}
                for r in results:
                    e = r['entity']
                    etype = e.get('type', '').lower()
                    layer = e.get('layer', '') or wrapper._knowledge_graph.TYPE_TO_LAYER.get(etype, 'other')
                    if layer not in by_layer:
                        by_layer[layer] = []
                    by_layer[layer].append(r)

                output = f"# Query Results | {len(results)} entities\n"

                # Show active filters
                filters = []
                if entity_types:
                    filters.append(f"types: {', '.join(entity_types)}")
                if layers:
                    filters.append(f"layers: {', '.join(layers)}")
                if file_patterns:
                    filters.append(f"files: {', '.join(file_patterns)}")
                if text_filter:
                    filters.append(f"text: '{text_filter}'")
                if filters:
                    output += f"Filters: {' | '.join(filters)}\n"
                output += "\n"

                # Output by layer
                for layer in ['code', 'service', 'data', 'testing', 'configuration', 'documentation', 'domain', 'product', 'knowledge', 'structure', 'tooling', 'other']:
                    if layer not in by_layer:
                        continue
                    entities = by_layer[layer]
                    output += f"## {layer.title()} ({len(entities)})\n"

                    for r in entities:
                        e = r['entity']
                        name = e.get('name', 'Unknown')
                        etype = e.get('type', '')
                        file_path = e.get('file_path', '')
                        source = e.get('source_toolkit', '')

                        output += f"- **{name}** ({etype})"
                        if source:
                            output += f" @ {source}"
                        if file_path:
                            output += f" - {file_path}"
                        output += "\n"

                        # Track entity
                        track_entity(e)

                    output += "\n"

                return output

            except Exception as ex:
                log.exception(f"[query_graph] Error: {ex}")
                return f"Error querying graph: {ex}"

        tools.append(Tool(
            name="query_graph",
            func=query_graph,
            description=TOOL_DESCRIPTIONS.get("query_graph", "Query the knowledge graph with structured filters. No similarity search - exact type/layer/file filtering. Input JSON: {\"types\": [\"class\", \"function\"], \"layers\": [\"code\"], \"files\": [\"*.py\"], \"name\": \"User\", \"related_to\": \"EntityName (type)\", \"relation_types\": [\"calls\", \"imports\"], \"direction\": \"out\", \"limit\": 30}"),
        ))

        # 4b. Pattern Query Tool - Cypher-like multi-hop traversal
        def query_pattern(pattern_input: str) -> str:
            """Execute a Cypher-like pattern query for multi-hop graph traversal."""
            try:
                pattern = pattern_input.strip()
                if not pattern:
                    return wrapper._knowledge_graph.PATTERN_SYNTAX_HELP
                
                results = wrapper._knowledge_graph.query_pattern(pattern, max_results=50)
                
                if not results:
                    return f"No paths found matching pattern: {pattern}"
                
                output = f"# Pattern: {pattern}\nFound {len(results)} path{'s' if len(results) != 1 else ''}\n\n"
                
                for i, result in enumerate(results, 1):
                    path = result['path']
                    edges = result['edges']
                    length = result['length']
                    
                    parts = []
                    for j, node in enumerate(path):
                        parts.append(f"**{node['name']}** ({node['type']})")
                        if j < len(edges):
                            parts.append(f"=[{edges[j]}]=")
                    
                    output += f"{i:2}. {' '.join(parts)} ({length} hop{'s' if length != 1 else ''})\n"
                
                if len(results) >= 50:
                    output += "\n_Showing first 50 results. Narrow your pattern for more specific results._\n"
                
                return output
            
            except ValueError as e:
                return str(e)
            except Exception as ex:
                log.exception(f"[query_pattern] Error: {ex}")
                return f"Error executing pattern query: {ex}"

        tools.append(Tool(
            name="query_pattern",
            func=query_pattern,
            description=TOOL_DESCRIPTIONS.get("query_pattern", (
                "Execute Cypher-like graph pattern query for MULTI-HOP traversal. "
                "Supports single-segment and multi-segment CHAIN patterns. "
                "Single: (source)-[:relation*min..max]->(target). "
                "Chain: (A)-[:rel1]->(B)-[:rel2]->(C) (up to 4 segments). "
                "Examples: (UserService)-[:calls*1..3]->(?), "
                "(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
            )),
        ))

        # 4c. Pattern Vocabulary Tool - discover graph schema for composing patterns
        def get_pattern_vocabulary(tool_input: str = "") -> str:
            """List entity types and relation types so you can compose valid query_pattern calls."""
            try:
                vocab = wrapper._knowledge_graph.get_pattern_vocabulary()
                etypes = vocab['entity_types']
                rtypes = vocab['relation_types']
                examples = vocab['example_patterns']
                
                output = "# Graph Vocabulary for Pattern Queries\n\n"
                output += "## Entity Types\n"
                for etype, count in etypes.items():
                    output += f"- **{etype}**: {count}\n"
                output += "\n## Relation Types\n"
                for rtype, count in rtypes.items():
                    output += f"- **{rtype}**: {count}\n"
                if examples:
                    output += "\n## Example Patterns\n"
                    for ex in examples:
                        output += f"- `{ex}`\n"
                return output
            except Exception as ex:
                log.exception(f"[get_pattern_vocabulary] Error: {ex}")
                return f"Error getting vocabulary: {ex}"

        tools.append(Tool(
            name="get_pattern_vocabulary",
            func=get_pattern_vocabulary,
            description=TOOL_DESCRIPTIONS.get("get_pattern_vocabulary", (
                "List all entity types and relation types in the graph with counts. "
                "Call BEFORE query_pattern when you don't know exact type or relation names."
            )),
        ))

        # 4d. Impact Analysis Tool - analyze change dependencies
        def impact_analysis(entity_name: str) -> str:
            """Analyze what entities would be impacted by changes to the specified entity."""
            try:
                parsed_name, parsed_type = parse_entity_reference(entity_name)

                entity, alternatives = smart_find_entity(parsed_name, parsed_type)

                if not entity:
                    if alternatives:
                        suggestions = [f"  - {e.get('name')} ({e.get('type')})" for e in alternatives[:5]]
                        return f"Entity '{parsed_name}' not found. Did you mean:\n" + "\n".join(suggestions)
                    return f"Entity '{parsed_name}' not found. Try search_knowledge_graph to find the correct name."

                track_entity(entity)

                entity_id = entity.get('id')
                if not entity_id:
                    return "Entity has no ID for impact analysis."

                # Downstream = what depends on this entity (affected by changes)
                impact = wrapper._knowledge_graph.impact_analysis(
                    entity_id, direction='downstream', max_depth=3
                )

                impacted = impact.get('impacted', [])

                actual_name = entity.get('name', parsed_name)
                actual_type = entity.get('type', 'unknown')

                if not impacted:
                    return f"No downstream dependencies found for '{actual_name}' ({actual_type}). This entity has no dependents that would be affected by changes."

                output = f"# Impact Analysis: {actual_name} ({actual_type})\n\n"
                output += f"**Direction:** downstream (what would break if this changes)\n"
                output += f"**Total impacted:** {len(impacted)} entities\n\n"

                # Group by depth
                by_depth = {}
                for item in impacted:
                    depth = item['depth']
                    if depth not in by_depth:
                        by_depth[depth] = []
                    by_depth[depth].append(item)

                for depth in sorted(by_depth.keys()):
                    items = by_depth[depth]
                    output += f"## Level {depth} — {len(items)} {'directly' if depth == 1 else 'indirectly'} affected\n\n"

                    for item in items[:15]:
                        ent = item['entity']
                        if ent:
                            track_entity(ent)
                            citation = ent.get('citation', {})
                            location = citation.get('file_path', '') if isinstance(citation, dict) else ''
                            output += f"- **{ent.get('name', '?')}** ({ent.get('type', '?')})"
                            if location:
                                output += f" - `{location}`"
                            output += "\n"

                    if len(items) > 15:
                        output += f"- ... and {len(items) - 15} more\n"

                    output += "\n"

                output += f"\n💡 Use get_related_entities(\"{actual_name} ({actual_type})\") for detailed relationship breakdown."

                return output

            except Exception as e:
                log.exception(f"[impact_analysis] Error: {e}")
                return f"Error in impact analysis: {e}"

        tools.append(Tool(
            name="impact_analysis",
            func=impact_analysis,
            description=TOOL_DESCRIPTIONS.get("impact_analysis", (
                "Analyze what would break or be affected if an entity changes. "
                "Shows direct dependents and transitive impact chains."
            )),
        ))

        # 5. List Entity Types Tool
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

        # 6. Community tools — conditionally added when community data exists
        if wrapper._has_communities():
            class ListCommunitiesInput(BaseModel):
                """Input for list_communities tool."""
                top_n: int = Field(default=0, description="Max communities to show (0 = all)")

            def list_communities_func(top_n: int = 0) -> str:
                """List detected communities with labels and key members."""
                try:
                    return wrapper.list_communities(top_n=top_n if top_n > 0 else None)
                except Exception as e:
                    log.exception(f"[list_communities] Error: {e}")
                    return f"Error listing communities: {e}"

            tools.append(StructuredTool(
                name="list_communities",
                func=list_communities_func,
                description="List all detected communities with their labels, sizes, and key members.",
                args_schema=ListCommunitiesInput,
            ))

            class GetCommunityDetailInput(BaseModel):
                """Input for get_community_detail tool."""
                community_id: str = Field(description="Community identifier (e.g., 'community_0')")

            def get_community_detail_func(community_id: str) -> str:
                """Get detailed info about a specific community."""
                try:
                    return wrapper.get_community_detail(community_id=community_id)
                except Exception as e:
                    log.exception(f"[get_community_detail] Error: {e}")
                    return f"Error getting community detail: {e}"

            tools.append(StructuredTool(
                name="get_community_detail",
                func=get_community_detail_func,
                description="Get detailed information about a specific community including members, centroids, and statistics.",
                args_schema=GetCommunityDetailInput,
            ))

            class FindEntityCommunityInput(BaseModel):
                """Input for find_entity_community tool."""
                entity_name: str = Field(description="Name of the entity to look up")

            def find_entity_community_func(entity_name: str) -> str:
                """Find which community an entity belongs to."""
                try:
                    return wrapper.find_entity_community(entity_name=entity_name)
                except Exception as e:
                    log.exception(f"[find_entity_community] Error: {e}")
                    return f"Error finding entity community: {e}"

            tools.append(StructuredTool(
                name="find_entity_community",
                func=find_entity_community_func,
                description="Find which community a given entity belongs to.",
                args_schema=FindEntityCommunityInput,
            ))

            class SearchWithinCommunityInput(BaseModel):
                """Input for search_within_community tool."""
                community_id: str = Field(description="Community identifier to search within")
                query: str = Field(description="Search query for finding entities within the community")

            def search_within_community_func(community_id: str, query: str) -> str:
                """Search entities within a specific community."""
                try:
                    return wrapper.search_within_community(
                        community_id=community_id, query=query
                    )
                except Exception as e:
                    log.exception(f"[search_within_community] Error: {e}")
                    return f"Error searching within community: {e}"

            tools.append(StructuredTool(
                name="search_within_community",
                func=search_within_community_func,
                description="Search for entities matching a query within a specific community.",
                args_schema=SearchWithinCommunityInput,
            ))

            log.info(f"[_build_chat_tools] Community tools enabled ({wrapper._knowledge_graph._metadata.get('community_data', {}).get('num_communities', 0)} communities)")

        return {tool.name: tool for tool in tools}

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
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute the chat agent with the given tools and prompt.

        Uses LangGraph agent pattern (same as elitea-sdk) for better tool calling support.

        Args:
            langfuse_callback: Optional Langfuse CallbackHandler for tracing
            system_prompt: Pre-composed system prompt from routing. If provided,
                           skips internal prompt formatting.

        Returns structured response with answer and citations.
        """
        import yaml
        from langchain_core.messages import HumanMessage, AIMessage
        from langchain_core.callbacks import BaseCallbackHandler
        from langgraph.checkpoint.memory import MemorySaver
        from elitea_sdk.runtime.langchain.langraph_agent import create_graph

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

        # Build system prompt — use pre-composed from routing if provided
        if system_prompt is None:
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

        # Build LangGraph YAML schema (same pattern as elitea-sdk Assistant)
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
            # Create LangGraph agent using elitea-sdk's create_graph
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

            # Get token usage from callback
            token_usage = callback.get_token_usage()

            return {
                "answer": answer,
                "citations": citations,
                "tool_calls": tool_calls,
                "error": None,
                "tokens_in": token_usage["tokens_in"],
                "tokens_out": token_usage["tokens_out"],
            }

        except Exception as e:
            log.exception(f"[_execute_chat_agent] Error: {e}")
            # Get token usage even on error (partial execution may have used tokens)
            token_usage = callback.get_token_usage()
            return {
                "answer": "",
                "citations": [],
                "tool_calls": [],
                "error": str(e),
                "tokens_in": token_usage["tokens_in"],
                "tokens_out": token_usage["tokens_out"],
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
        elitea_client,
        settings: Dict[str, Any],
        touched_entities: List[Dict[str, Any]] = None,
    ) -> List:
        """
        Get read-only tools from all source toolkits.

        Reads sources_status.json to find configured sources, fetches their
        toolkit configurations, instantiates them using elitea-sdk, and
        filters to only include read-only operations.

        Args:
            project_id: Project ID
            toolkit_id: Inventory toolkit ID
            elitea_client: EliteAClient instance for API calls
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
                # Fetch toolkit configuration from platform with expand=true to get expanded credentials
                toolkit_url = f"{elitea_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{source_toolkit_id}?expand=true"
                resp = http_requests.get(toolkit_url, headers=elitea_client.headers, verify=False)

                if not resp.ok:
                    log.warning(f"[_get_source_toolkit_tools] Failed to fetch toolkit {source_toolkit_id}: {resp.status_code}")
                    continue

                toolkit_data = resp.json()
                toolkit_type = toolkit_data.get("type", source_toolkit_type)
                toolkit_settings = toolkit_data.get("settings", {})

                log.info(f"[_get_source_toolkit_tools] Instantiating toolkit {source_toolkit_name} (type={toolkit_type}, id={source_toolkit_id})")

                # Build toolkit config for elitea-sdk
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
                llm = elitea_client.get_llm(
                    model_name=llm_model,
                    model_config={"temperature": DEFAULT_LLM_TEMPERATURE, "max_tokens": DEFAULT_TOOL_LLM_MAX_TOKENS},
                )

                # Instantiate toolkit tools using elitea-sdk
                source_tools = self._instantiate_toolkit_tools(
                    toolkit_config=toolkit_config,
                    llm=llm,
                    elitea_client=elitea_client,
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
        elitea_client,
    ) -> List:
        """
        Instantiate tools from a toolkit configuration using elitea-sdk.

        Args:
            toolkit_config: Toolkit configuration dict
            llm: LLM instance
            elitea_client: EliteAClient instance

        Returns:
            List of instantiated LangChain tools
        """
        try:
            from elitea_sdk.runtime.toolkits.tools import get_tools

            # Build tools_list format expected by get_tools()
            tools_list = [toolkit_config]

            # Instantiate tools
            tools = get_tools(
                tools_list,
                elitea_client=elitea_client,
                llm=llm,
            )

            return tools

        except ImportError as e:
            log.warning(f"[_instantiate_toolkit_tools] elitea-sdk not available: {e}")
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
    def _prefix_tool_names(
        self,
        tools: List,
        toolkit_name: str,
    ) -> List:
        """
        Add toolkit name prefix to tool names to ensure uniqueness.

        When multiple source toolkits of the same type are configured (e.g., 2 GitHub repos),
        tools like 'read_file' would collide. This method prefixes each tool's name
        with the toolkit name to make them unique (e.g., 'myrepo_read_file').

        Args:
            tools: List of LangChain tools
            toolkit_name: Name of the source toolkit (used as prefix)

        Returns:
            List of tools with prefixed names
        """
        import re
        from langchain_core.tools.simple import Tool
        from langchain_core.tools.structured import StructuredTool

        prefixed_tools = []

        # Sanitize toolkit name to be a valid tool name prefix
        # Replace spaces, hyphens, dots with underscores, keep only alphanumeric + underscore
        safe_prefix = re.sub(r'[^a-zA-Z0-9_]', '_', toolkit_name.lower())
        # Remove leading underscores and digits
        safe_prefix = re.sub(r'^[_0-9]+', '', safe_prefix)
        # Limit length to avoid overly long names
        safe_prefix = safe_prefix[:20]

        if not safe_prefix:
            safe_prefix = "source"

        for tool in tools:
            original_name = tool.name if hasattr(tool, 'name') else 'unknown'
            prefixed_name = f"{safe_prefix}_{original_name}"

            # Create a new tool with the prefixed name
            # Update description to mention which source this is from
            original_desc = tool.description if hasattr(tool, 'description') else ''
            prefixed_desc = f"[Source: {toolkit_name}] {original_desc}"

            # Get the function to call
            func = tool.func if hasattr(tool, 'func') else tool._run
            args_schema = tool.args_schema if hasattr(tool, 'args_schema') else None

            # Preserve StructuredTool type if original tool has args_schema
            # This is critical - using simple Tool for multi-arg tools causes
            # "Too many arguments to single-input tool" errors
            if args_schema is not None:
                prefixed_tool = StructuredTool(
                    name=prefixed_name,
                    func=func,
                    description=prefixed_desc,
                    args_schema=args_schema,
                )
            else:
                prefixed_tool = Tool(
                    name=prefixed_name,
                    func=func,
                    description=prefixed_desc,
                )

            prefixed_tools.append(prefixed_tool)
            log.debug(f"[_prefix_tool_names] Renamed '{original_name}' -> '{prefixed_name}' (structured={args_schema is not None})")

        log.info(f"[_prefix_tool_names] Prefixed {len(prefixed_tools)} tools from {toolkit_name} with '{safe_prefix}_'")
        return prefixed_tools

    @web.method()
    def _wrap_tools_with_tracking(
        self,
        tools: List,
        toolkit_name: str,
        touched_entities: List[Dict[str, Any]],
    ) -> List:
        """
        Prefix tool names and prepare for tracking.

        Adds toolkit name prefix to ensure unique tool names when multiple
        toolkits of the same type are configured (e.g., 2 GitHub repos).
        File tracking happens via LangChain callbacks in InventoryChatCallback.on_tool_start.

        Args:
            tools: List of LangChain tools
            toolkit_name: Name of the source toolkit (used as prefix)
            touched_entities: Shared list (tracking via callback)

        Returns:
            List of tools with prefixed names
        """
        return self._prefix_tool_names(tools, toolkit_name)
