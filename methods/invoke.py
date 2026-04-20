#!/usr/bin/python3
# coding=utf-8

"""Tool Invocation Method"""

import json
import traceback
import sys
from pathlib import Path

from pylon.core.tools import log
from pylon.core.tools import web

# Add plugin directory to Python path for local inventory module
plugin_dir = Path(__file__).parent.parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

# Import CANONICAL_TYPES for smart normalization
try:
    from ..constants import CANONICAL_TYPES
except ImportError:
    from plugins.inventory_plugin.constants import CANONICAL_TYPES


class Method:
    """
    Method Resource for tool invocation

    self is pointing to current Module instance

    web.method decorator takes zero or one argument: method name
    Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def _create_error_response(self, invocation_id, operation, exception, include_traceback=True):
        """Create a structured error response"""
        error_type = type(exception).__name__
        error_category = "unknown_error"
        exception_str = str(exception)

        try:
            lower = exception_str.lower()
        except Exception:
            lower = ""

        # Categorize errors
        if "not found" in lower or isinstance(exception, FileNotFoundError):
            error_category = "resource_not_found"
        elif "memory" in lower or isinstance(exception, MemoryError):
            error_category = "out_of_memory"
        elif "timeout" in lower:
            error_category = "timeout_error"
        elif isinstance(exception, RuntimeError):
            error_category = "runtime_error"
        elif isinstance(exception, ValueError):
            error_category = "invalid_input"

        error_message = f"{str(operation).capitalize()} failed\n\n"
        error_message += f"Error: {exception_str}\n"
        error_message += f"Type: {error_type}\n"
        error_message += f"Category: {error_category}"

        if include_traceback:
            tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
            stack_trace = "".join(tb_lines)
            error_message += f"\n\nStack Trace:\n{stack_trace}"

        result_objects = [
            {
                "object_type": "message",
                "result_target": "response",
                "result_encoding": "plain",
                "data": error_message,
            }
        ]

        return {
            "invocation_id": invocation_id,
            "status": "Error",
            "result": json.dumps(result_objects),
            "result_type": "String",
            "error_category": error_category,
            "error_type": error_type,
        }

    @web.method()
    def _create_success_response(self, invocation_id, result, artifacts=None):
        """Create a structured success response"""
        result_objects = [
            {
                "object_type": "message",
                "result_target": "response",
                "result_encoding": "plain",
                "data": result,
            }
        ]

        # Add any artifacts
        if artifacts:
            for artifact in artifacts:
                result_objects.append(artifact)

        return {
            "invocation_id": invocation_id,
            "status": "Completed",
            "result": json.dumps(result_objects),
            "result_type": "String",
        }

    @web.method()
    def _get_elitea_client(self, project_id: int):
        """Create EliteAClient instance for platform API calls.

        Args:
            project_id: The project ID to use for the client

        Returns:
            EliteAClient instance or None if platform config is missing
        """
        from elitea_sdk.runtime.clients.client import EliteAClient

        platform_api_url = self.descriptor.config.get("platform_api_url", "")
        platform_token = self.descriptor.config.get("ai_run_platform_token", "")

        # Fallback: derive platform URL from app_url if platform_api_url not set
        if not platform_api_url:
            app_url = self.descriptor.config.get("app_url", "")
            if app_url:
                from urllib.parse import urlparse
                parsed = urlparse(app_url)
                platform_api_url = f"{parsed.scheme}://{parsed.hostname}"

        if not platform_api_url or not platform_token:
            log.warning("Platform API URL or token not configured")
            return None

        return EliteAClient(
            base_url=platform_api_url.rstrip("/"),
            project_id=int(project_id),
            auth_token=platform_token,
        )

    @web.method()
    def perform_invoke_request(self, toolkit_name, tool_name, request_data):
        """Main entry point for tool invocation"""
        import tasknode_task
        invocation_id = tasknode_task.id

        log.info(f"Invoking tool: {toolkit_name}:{tool_name}")

        # Validate toolkit - supports "inventory" and "inventory_search"
        if toolkit_name not in ("inventory", "inventory_search"):
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError(f"Unknown toolkit: {toolkit_name}. Expected: inventory or inventory_search"),
                include_traceback=False,
            )

        try:
            # Check for stop request
            self.invocation_stop_checkpoint()

            # Extract parameters
            toolkit_params = request_data.get("configuration", {}).get("parameters", {})
            tool_params = request_data.get("parameters", {})

            log.info(f"Toolkit params: {toolkit_params}")
            log.info(f"Tool params: {tool_params}")

            # Merge parameters (tool params override toolkit params)
            params = toolkit_params.copy()
            for key, value in tool_params.items():
                if key not in params or value:
                    params[key] = value

            # Route to appropriate handler based on toolkit type
            if toolkit_name == "inventory_search":
                return self._handle_inventory_search_tool(invocation_id, tool_name, params, request_data)
            else:
                return self._handle_inventory_tool(invocation_id, tool_name, params, request_data)

        except Exception as e:
            log.exception(f"Tool invocation failed: {toolkit_name}:{tool_name}")
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=e,
                include_traceback=True,
            )

    @web.method()
    def _handle_inventory_tool(self, invocation_id, tool_name, params, request_data):
        """Handle all inventory toolkit tools"""
        log.info(f"[DEBUG] _handle_inventory_tool called: tool_name={tool_name}")
        log.info(f"[DEBUG] params: {params}")
        log.info(f"[DEBUG] config keys: {request_data.get('configuration', {}).keys()}")
        
        # Tool routing map
        tools = {
            # Ingestion tools
            "run_ingestion": self._tool_run_ingestion,
            "delta_update": self._tool_delta_update,
            "remove_source_entities": self._tool_remove_source_entities,
            # Graph management tools
            "list_ingested_sources": self._tool_list_ingested_sources,
            "list_graphs": self._tool_list_graphs,
            "load_graph": self._tool_load_graph,
            "get_graph_info": self._tool_get_graph_info,
            # Retrieval tools
            "search_graph": self._tool_search_graph,
            "get_entity": self._tool_get_entity,
            "get_entity_content": self._tool_get_entity_content,
            "impact_analysis": self._tool_impact_analysis,
            "get_related_entities": self._tool_get_related_entities,
            "get_cross_source_relations": self._tool_get_cross_source_relations,
            "get_stats": self._tool_get_stats,
            "list_entities_by_type": self._tool_list_entities_by_type,
            "list_entities_by_layer": self._tool_list_entities_by_layer,
            "list_entities_by_source": self._tool_list_entities_by_source,
            # Preset tools
            "list_presets": self._tool_list_presets,
            "get_preset_info": self._tool_get_preset_info,
            # Cache management tools
            "get_cache_stats": self._tool_get_cache_stats,
            "cleanup_cache": self._tool_cleanup_cache,
            # Ingestion status tools
            "get_ingestion_status": self._tool_get_ingestion_status,
            # Source status tools (for UI)
            "get_sources_status": self._tool_get_sources_status,
            # Entity batch retrieval (for chat highlighting)
            "get_entities_by_ids": self._tool_get_entities_by_ids,
            # Entity neighbor expansion (for graph UI context menu)
            "get_entity_neighbors": self._tool_get_entity_neighbors,
            # Maintenance tools
            "normalize_types": self._tool_normalize_types,
            "rebuild_indices": self._tool_rebuild_indices,
            "smart_normalize_types": self._tool_smart_normalize_types,
        }

        if tool_name not in tools:
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError(f"Unknown tool: {tool_name}"),
                include_traceback=False,
            )

        # Get graph path from project and application IDs
        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        application_id = config.get("application_id") or params.get("application_id")

        log.info(f"[DEBUG] Extracted project_id={project_id}, application_id={application_id}")

        # Construct graph path: /data/graphs/<project_id>/<application_id>/graph.json
        graph_path = None
        if project_id and application_id:
            graph_path = f"/data/graphs/{project_id}/{application_id}/graph.json"

        log.info(f"[DEBUG] Constructed graph_path: {graph_path}")

        # Track cache access for this graph (using project_id and application_id as keys)
        if project_id and application_id:
            self.cache_manager.touch(str(project_id), str(application_id))

        # Execute tool
        result = tools[tool_name](params, graph_path, request_data)

        # Handle tuple returns (result, artifacts)
        if isinstance(result, tuple):
            return self._create_success_response(invocation_id, result[0], result[1])
        return self._create_success_response(invocation_id, result)

    @web.method()
    def _handle_inventory_search_tool(self, invocation_id, tool_name, params, request_data):
        """Handle inventory_search toolkit tools - read-only access to referenced inventory graph.

        This toolkit references an existing inventory application and exposes only
        read-only search/query tools for use by other agents.
        """
        log.info(f"[inventory_search] Handling tool: {tool_name}")
        log.info(f"[inventory_search] params: {params}")
        log.info(f"[inventory_search] request_data keys: {request_data.keys() if request_data else 'None'}")
        log.info(f"[inventory_search] configuration: {request_data.get('configuration', {})}")

        # Tool name mapping: inventory_search tool names -> internal handler names
        tool_mapping = {
            "search_knowledge_graph": "search_graph",
            "get_entity_details": "get_entity",
            "get_related_entities": "get_related_entities",
            "query_graph": "query_graph",
            "list_entity_types": "list_entity_types",
            "investigate": "investigate",
        }

        if tool_name not in tool_mapping:
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError(f"Unknown inventory_search tool: {tool_name}. Available: {list(tool_mapping.keys())}"),
                include_traceback=False,
            )

        # Get the referenced inventory toolkit - can be an ID or a full toolkit object
        inventory_toolkit_ref = params.get("inventory_toolkit")
        if not inventory_toolkit_ref:
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError("inventory_toolkit parameter is required - specify which inventory toolkit to use"),
                include_traceback=False,
            )

        # Extract toolkit ID - handle both int and dict (full toolkit object)
        if isinstance(inventory_toolkit_ref, dict):
            inventory_toolkit_id = inventory_toolkit_ref.get("id")
            log.info(f"[inventory_search] Extracted toolkit ID from object: {inventory_toolkit_id}")
        else:
            inventory_toolkit_id = inventory_toolkit_ref
            log.info(f"[inventory_search] Using toolkit ID directly: {inventory_toolkit_id}")

        if not inventory_toolkit_id:
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError("Could not extract toolkit ID from inventory_toolkit parameter"),
                include_traceback=False,
            )

        # Get project_id - it's at the root level of request_data (not in configuration)
        project_id = request_data.get("project_id")
        log.info(f"[inventory_search] project_id={project_id}, type={type(project_id)}, raw_value={repr(request_data.get('project_id'))}")

        # Debug: dump all request_data keys and values for project_id
        for key in request_data.keys():
            if 'project' in key.lower() or 'id' in key.lower():
                log.info(f"[inventory_search] request_data[{key}]={request_data.get(key)}")

        if not project_id:
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError("project_id not found in request configuration"),
                include_traceback=False,
            )

        # Construct graph path from the referenced inventory toolkit
        # The inventory toolkit stores its graph at /data/graphs/<project_id>/<toolkit_id>/graph.json
        graph_path = f"/data/graphs/{project_id}/{inventory_toolkit_id}/graph.json"
        log.info(f"[inventory_search] Using graph from inventory toolkit {inventory_toolkit_id}: {graph_path}")

        # Track cache access
        self.cache_manager.touch(str(project_id), str(inventory_toolkit_id))

        # Route to internal tool handlers
        internal_tool_name = tool_mapping[tool_name]

        # Map to internal tool handlers
        tools = {
            "search_graph": self._tool_search_graph,
            "get_entity": self._tool_get_entity,
            "get_related_entities": self._tool_get_related_entities,
            "query_graph": self._tool_query_graph,
            "list_entity_types": self._tool_list_entity_types_only,
            "investigate": self._tool_investigate,
        }

        # Execute tool - investigate needs project_id and toolkit_id
        if internal_tool_name == "investigate":
            result = tools[internal_tool_name](params, project_id, inventory_toolkit_id, request_data)
        else:
            result = tools[internal_tool_name](params, graph_path, request_data)

        # Handle tuple returns (result, artifacts)
        if isinstance(result, tuple):
            return self._create_success_response(invocation_id, result[0], result[1])
        return self._create_success_response(invocation_id, result)

    @web.method()
    def _tool_list_entity_types_only(self, params, graph_path, request_data):
        """List all entity types in the graph with counts - simplified version for inventory_search toolkit."""
        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)

        # Get entity type counts from the graph
        type_counts = {}
        for node_id in wrapper._knowledge_graph._graph.nodes():
            node_data = wrapper._knowledge_graph._graph.nodes[node_id]
            entity_type = node_data.get('type', 'unknown')
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

        # Sort by count descending
        sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)

        # Format output
        lines = ["Entity Types in Knowledge Graph:", "=" * 40]
        total = 0
        for entity_type, count in sorted_types:
            lines.append(f"  {entity_type}: {count}")
            total += count
        lines.append("-" * 40)
        lines.append(f"  Total: {total} entities")

        return "\n".join(lines)

    @web.method()
    def _tool_investigate(self, params, project_id, toolkit_id, request_data):
        """
        Investigate/ask questions about the knowledge graph using an AI agent.

        This tool provides a natural language interface to query the knowledge graph.
        It uses an LLM-powered agent that can:
        - Search the knowledge graph
        - Get entity details
        - Explore relationships
        - Analyze impact

        Args (via params):
            question: The question to investigate (required)
            entity_types: Filter to specific entity types (optional)
            sources: Filter to specific source toolkits (optional)
            layers: Filter to specific layers (optional)
            depth: Max relationship hops (default: 2)
            max_nodes: Max results per search (default: 500)

        Returns:
            Structured response with answer, citations, and token usage
        """
        import json as json_module

        # Extract question
        question = params.get("question") or params.get("query") or params.get("prompt")
        if not question:
            return json_module.dumps({
                "error": "Missing required parameter: question",
                "usage": "Provide a 'question' parameter with your investigation query"
            })

        # Build filters from params
        filters = {}
        if params.get("entity_types"):
            entity_types = params.get("entity_types")
            if isinstance(entity_types, str):
                entity_types = [t.strip() for t in entity_types.split(",")]
            filters["entity_types"] = entity_types

        if params.get("sources"):
            sources = params.get("sources")
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split(",")]
            filters["sources"] = sources

        if params.get("layers"):
            layers = params.get("layers")
            if isinstance(layers, str):
                layers = [l.strip() for l in layers.split(",")]
            filters["layers"] = layers

        # Search scope settings
        filters["depth"] = params.get("depth", 2)
        filters["max_nodes"] = params.get("max_nodes", 500)

        log.info(f"[investigate] Question: {question[:100]}...")
        log.info(f"[investigate] Filters: {filters}")
        log.info(f"[investigate] project_id={project_id}, toolkit_id={toolkit_id}")

        try:
            # Call inventory_chat method (no emit_fn for non-streaming)
            result = self.inventory_chat(
                project_id=project_id,
                toolkit_id=toolkit_id,
                prompt=question,
                filters=filters,
                conversation_id=None,  # No conversation tracking for tool invocations
                history=[],  # No history for single-shot queries
                emit_fn=None,  # No streaming
                model=None,  # Use toolkit's configured model
            )

            # Format response
            response = {
                "answer": result.get("answer", ""),
                "citations": result.get("citations", []),
                "tool_calls": result.get("tool_calls", []),
                "tokens_in": result.get("tokens_in", 0),
                "tokens_out": result.get("tokens_out", 0),
                "total_tokens": result.get("tokens_in", 0) + result.get("tokens_out", 0),
            }

            if result.get("error"):
                response["error"] = result["error"]

            # Also return as formatted text for better readability
            output_format = params.get("output_format", "text")
            if output_format == "json":
                return json_module.dumps(response, indent=2)

            # Text format
            lines = []
            lines.append("=" * 60)
            lines.append("INVESTIGATION RESULT")
            lines.append("=" * 60)
            lines.append("")
            lines.append(result.get("answer", "No answer generated"))
            lines.append("")

            if result.get("citations"):
                lines.append("-" * 40)
                lines.append("Citations:")
                for citation in result["citations"][:10]:  # Limit to 10
                    if citation.get("entity_name"):
                        lines.append(f"  - {citation['entity_name']}")
                    elif citation.get("source_toolkit"):
                        path = citation.get("file_path", "")
                        lines.append(f"  - {citation['source_toolkit']}: {path}")

            if result.get("tool_calls"):
                lines.append("-" * 40)
                lines.append(f"Tools used: {len(result['tool_calls'])}")
                for tc in result["tool_calls"][:5]:  # Show first 5
                    lines.append(f"  - {tc.get('tool', 'unknown')}")

            lines.append("-" * 40)
            lines.append(f"Tokens: {response['total_tokens']} (in: {response['tokens_in']}, out: {response['tokens_out']})")

            if result.get("error"):
                lines.append(f"Error: {result['error']}")

            return "\n".join(lines)

        except Exception as e:
            log.exception(f"[investigate] Error: {e}")
            return json_module.dumps({
                "error": str(e),
                "answer": "",
                "citations": [],
            })

    # ========== Graph/Wrapper Management ==========

    @web.method()
    def _parse_entity_reference(self, entity_ref: str):
        """
        Parse entity reference string.

        Supports formats from search results:
        - "Name" -> returns (name, None)
        - "Name (type)" -> returns (name, type)
        - "Name (type) @ source" -> returns (name, type)
        - "Name (type) @ source - file_path" -> returns (name, type)

        The parser strips everything after " @ " before parsing Name (type),
        allowing users to copy search results directly as input.
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

    @web.method()
    def _find_entity_by_reference(self, wrapper, entity_ref: str):
        """
        Find entity by reference string, supporting "Name", "Name (type)", and full search result formats.

        Returns:
            Tuple of (entity, error_message) - entity is None if not found
        """
        parsed_name, parsed_type = self._parse_entity_reference(entity_ref)

        # Find all entities with this name to provide helpful feedback
        all_matches = wrapper._knowledge_graph.find_all_entities_by_name(parsed_name)

        if parsed_type:
            # Input has type specified - filter by type
            for e in all_matches:
                if e.get('type', '').lower() == parsed_type.lower():
                    return e, None

            # Not found with specified type
            if all_matches:
                available = [f"{e.get('name')} ({e.get('type', 'unknown')})" for e in all_matches[:10]]
                return None, f"Entity '{parsed_name}' not found with type '{parsed_type}'.\n\nAvailable entities with this name:\n" + "\n".join(f"  - {a}" for a in available)
            return None, f"Entity '{parsed_name}' not found. Try search_knowledge_graph to find the correct name."
        else:
            # No type specified - check if multiple matches exist
            if len(all_matches) > 1:
                # Multiple matches - ask user to specify type
                options = [f"{e.get('name')} ({e.get('type', 'unknown')}) @ {e.get('source_toolkit', '?')} - {e.get('file_path', '?')}" for e in all_matches[:10]]
                return None, f"Multiple entities named '{parsed_name}' found. Please specify the type:\n\n" + "\n".join(f"  - {o}" for o in options) + "\n\nUse format: \"Name (type)\" or copy full reference from above."
            elif all_matches:
                return all_matches[0], None
            return None, f"Entity '{entity_ref}' not found. Try search_knowledge_graph to find the correct name."

    @web.method()
    def _parse_graph_query(self, query_str: str) -> dict:
        """
        Parse JQL-like query string into parameters dict.

        Syntax:
            type:class,function    - Entity types (comma-separated)
            layer:code,service     - Layers to filter
            file:*.py,src/*.ts     - File patterns
            name:UserService       - Name substring filter
            related:EntityName     - Find entities related to this
            related:"Name (type) @ source - path"  - Full search result format
            rel:calls,imports      - Relation types filter
            dir:in|out|both        - Relation direction
            limit:50               - Max results

        Examples:
            type:class layer:code
            related:UserService type:function dir:out
            related:"read_file (method) @ sdk - path" type:class

        Copy-paste from search results is supported for related: operator.
        """
        import shlex

        params = {}
        query_str = query_str.strip()

        if not query_str:
            return params

        operators = {
            'type': 'types', 'types': 'types',
            'layer': 'layers', 'layers': 'layers',
            'file': 'files', 'files': 'files',
            'name': 'name', 'text': 'name', 'query': 'name',
            'related': 'related_to', 'related_to': 'related_to',
            'rel': 'relation_types', 'relation': 'relation_types',
            'dir': 'direction', 'direction': 'direction',
            'has_rel': 'has_relations', 'has_relations': 'has_relations',
            'limit': 'limit',
        }

        list_params = {'types', 'layers', 'files', 'relation_types'}

        try:
            tokens = shlex.split(query_str)
        except ValueError:
            tokens = query_str.split()

        unmatched_tokens = []

        for token in tokens:
            if ':' in token:
                key, value = token.split(':', 1)
                key = key.lower().strip()

                if key in operators:
                    param_name = operators[key]

                    if param_name in list_params:
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
                    unmatched_tokens.append(token)
            else:
                unmatched_tokens.append(token)

        if unmatched_tokens and 'name' not in params:
            params['name'] = ' '.join(unmatched_tokens)

        return params

    @web.method()
    def _is_artifact_error(self, data):
        """Check if artifact response is an error (handles various error formats)"""
        if not data:
            return True
        # Check various error prefixes
        error_prefixes = [
            "Error:",
            '{"error"',
            "An error occurred",
        ]
        for prefix in error_prefixes:
            if data.startswith(prefix):
                return True
        # Also check if "error" appears in the response (for embedded JSON errors)
        if '"error"' in data[:100]:  # Check first 100 chars
            return True
        return False

    @web.method()
    def _download_graph_from_artifacts(self, graph_path, project_id, application_id, artifact_bucket):
        """Download graph and checkpoints from artifact bucket if not present locally"""
        import os
        from pathlib import Path

        # Create EliteAClient using helper method
        elitea_client = self._get_elitea_client(project_id)
        if not elitea_client:
            log.warning("Cannot download from artifacts: Platform API URL or token not configured")
            return False

        graph_dir = os.path.dirname(graph_path)

        try:
            # Download main graph file
            artifact_name = "graph.json"
            log.info(f"Downloading graph from artifacts: {artifact_bucket}/{artifact_name}")
            graph_data = elitea_client.artifact(artifact_bucket).get(artifact_name)

            if graph_data and not self._is_artifact_error(graph_data):
                # Create directory if needed
                Path(graph_dir).mkdir(parents=True, exist_ok=True)

                # Write graph file
                with open(graph_path, 'w', encoding='utf-8') as f:
                    f.write(graph_data)
                log.info(f"Downloaded graph to {graph_path}")

                # Try to download sources_status.json
                try:
                    status_data = elitea_client.artifact(artifact_bucket).get("sources_status.json")
                    if status_data and not self._is_artifact_error(status_data):
                        status_file = os.path.join(graph_dir, "sources_status.json")
                        with open(status_file, 'w', encoding='utf-8') as f:
                            f.write(status_data)
                        log.info(f"Downloaded sources_status.json to {status_file}")
                except Exception as e:
                    log.debug(f"No sources_status.json found or error downloading: {e}")

                # Try to download checkpoints (they may or may not exist)
                try:
                    # List all artifacts in the bucket to find checkpoints
                    artifacts = elitea_client.artifact(artifact_bucket).list(return_as_string=False)
                    checkpoint_prefix = ".ingestion-checkpoint-"

                    for artifact_info in artifacts:
                        if isinstance(artifact_info, dict):
                            artifact_path = artifact_info.get('name', '')
                            if artifact_path.startswith(checkpoint_prefix):
                                checkpoint_data = elitea_client.artifact(artifact_bucket).get(artifact_path)
                                if checkpoint_data and not self._is_artifact_error(checkpoint_data):
                                    checkpoint_file = os.path.join(graph_dir, os.path.basename(artifact_path))
                                    with open(checkpoint_file, 'w', encoding='utf-8') as f:
                                        f.write(checkpoint_data)
                                    log.info(f"Downloaded checkpoint: {checkpoint_file}")
                except Exception as e:
                    log.debug(f"No checkpoints found or error downloading checkpoints: {e}")
                
                return True
            else:
                log.info(f"Graph not found in artifacts: {graph_data}")
                return False
                
        except Exception as e:
            log.warning(f"Failed to download graph from artifacts: {e}")
            return False

    @web.method()
    def _get_or_create_wrapper(self, graph_path, request_data=None):
        """Get or create a retrieval wrapper for the given graph path

        If graph doesn't exist locally, tries to download from artifact bucket.
        Embedding model is always local (all-MiniLM-L6-v2) — initialized lazily
        by the wrapper on first semantic_search call.
        """
        import os
        from inventory import InventoryRetrievalApiWrapper

        log.info(f"[_get_or_create_wrapper] graph_path={graph_path}, has_request_data={request_data is not None}")

        # Check if graph exists locally, if not try to download from artifacts
        if graph_path and not os.path.exists(graph_path) and request_data:
            log.info(f"[_get_or_create_wrapper] Graph not found at {graph_path}, attempting to download from artifacts")

            # Get project and application IDs from request
            config = request_data.get("configuration", {})
            project_id = config.get("project_id")
            application_id = config.get("application_id")
            # Get artifact bucket from toolkit settings
            settings = config.get("settings", {})
            artifact_bucket = settings.get("toolkit_configuration_bucket", "graphs")

            log.info(f"[_get_or_create_wrapper] project_id={project_id}, application_id={application_id}, artifact_bucket={artifact_bucket}")

            if project_id and application_id:
                download_result = self._download_graph_from_artifacts(
                    graph_path, project_id, application_id, artifact_bucket
                )
                log.info(f"[_get_or_create_wrapper] Download result: {download_result}")
            else:
                log.warning(f"[_get_or_create_wrapper] Cannot download from artifacts: project_id={project_id}, application_id={application_id}")

        if graph_path not in self.graph_instances:
            wrapper = InventoryRetrievalApiWrapper(
                graph_path=graph_path or "",
                base_directory=None,
                source_toolkits={},
            )
            self.graph_instances[graph_path] = wrapper

        return self.graph_instances[graph_path]

    # ========== Ingestion Tools ==========

    @web.method()
    def _tool_run_ingestion(self, params, graph_path, request_data):
        """Run full ingestion pipeline for a toolkit - mimics CLI ingest command"""
        import os
        import json as json_module
        import tasknode_task
        from pathlib import Path
        from ..utils.ingestion_tracker import IngestionSlotError
        from ..utils.source_status import SourceStatusManager

        toolkit_id = params.get("toolkit_id")
        branch = params.get("branch")
        file_patterns = params.get("file_patterns", "")
        exclude_patterns = params.get("exclude_patterns", "")
        full_rebuild = params.get("full_rebuild", False)
        output_format = params.get("output_format", "text")

        if not toolkit_id:
            return "Error: toolkit_id is required"

        if not graph_path:
            return "Error: No graph path configured. Set bucket and graph_name in toolkit configuration."

        # Get context for slot tracking
        task_id = tasknode_task.id
        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        application_id = config.get("application_id") or params.get("application_id")

        # Try to acquire an ingestion slot
        slot_acquired = False
        try:
            self.ingestion_tracker.acquire_slot(
                task_id=task_id,
                project_id=int(project_id) if project_id else 0,
                toolkit_id=int(toolkit_id) if toolkit_id else 0,
                application_id=int(application_id) if application_id else 0,
            )
            slot_acquired = True
        except IngestionSlotError as e:
            # Return user-friendly error when all workers are busy
            error_message = str(e)
            if output_format == "json":
                return json_module.dumps({
                    "success": False,
                    "error": "ingestion_slots_busy",
                    "message": error_message,
                    "retry_after_seconds": 600,  # Suggest 10 minutes
                })
            return f"Error: {error_message}"

        self.invocation_thinking(f"Starting ingestion from toolkit {toolkit_id}...")
        log.info(f"[run_ingestion] ===== Starting ingestion for toolkit {toolkit_id} =====")

        try:
            # Get project_id and application_id from request context
            config = request_data.get("configuration", {})
            log.info(f"[run_ingestion] config keys: {list(config.keys())}")
            project_id = config.get("project_id") or params.get("project_id")
            application_id = config.get("application_id") or params.get("application_id")

            if not project_id:
                return "Error: project_id not found in request context"
            if not application_id:
                return "Error: application_id not found in request context"

            # Create EliteAClient for platform API calls
            elitea_client = self._get_elitea_client(project_id)
            if not elitea_client:
                return "Error: Platform API URL or token not configured. Check PLATFORM_API_URL and AI_RUN_PLATFORM_TOKEN."

            self.invocation_thinking(f"Connecting to platform at {elitea_client.base_url}...")

            # Instantiate source toolkit
            # Note: toolkit_id parameter refers to the SOURCE toolkit (GitHub/ADO/GitLab),
            # not the inventory toolkit itself
            # We fetch directly using the correct API path (/api/v2/elitea_core) because
            # EliteAClient.toolkit() uses the old /api/v1 path
            self.invocation_thinking(f"Loading source toolkit {toolkit_id}...")
            log.info(f"[run_ingestion] Loading source toolkit {toolkit_id}")
            try:
                import requests as http_requests
                from elitea_sdk.tools import instantiate_toolkit

                # Fetch toolkit data using correct API path with expand=true to get expanded credentials
                toolkit_api_url = f"{elitea_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}?expand=true"
                log.info(f"[run_ingestion] Fetching toolkit from: {toolkit_api_url}")

                resp = http_requests.get(toolkit_api_url, headers=elitea_client.headers, verify=False)
                if not resp.ok:
                    log.error(f"[run_ingestion] Failed to fetch toolkit: {resp.status_code} - {resp.text}")
                    return f"Error: Failed to fetch source toolkit {toolkit_id}: {resp.status_code}"

                toolkit_data = resp.json()
                log.info(f"[run_ingestion] Got toolkit data: {toolkit_data.get('name', 'unknown')}, type: {toolkit_data.get('type', 'unknown')}")

                # Add elitea client to settings (same as EliteAClient.toolkit() does)
                if 'settings' not in toolkit_data:
                    toolkit_data['settings'] = {}
                toolkit_data['settings']['elitea'] = elitea_client

                # Instantiate toolkit using instantiate_toolkit from SDK
                source_toolkit_instance = instantiate_toolkit(toolkit_data)
                log.info(f"[run_ingestion] Source toolkit loaded: {type(source_toolkit_instance)}")
            except Exception as e:
                log.exception(f"[run_ingestion] Failed to load source toolkit {toolkit_id}")
                return f"Error: Failed to load source toolkit {toolkit_id}: {e}"

            # Extract api_wrapper from the toolkit's tools
            if hasattr(source_toolkit_instance, 'tools') and source_toolkit_instance.tools:
                source_toolkit = source_toolkit_instance.tools[0].api_wrapper
            elif hasattr(source_toolkit_instance, 'api_wrapper'):
                source_toolkit = source_toolkit_instance.api_wrapper
            else:
                source_toolkit = source_toolkit_instance

            # Extract toolkit metadata - prefer name from API response (user-friendly name like "websearch"),
            # then try api_wrapper attributes, then fallback to toolkit_id
            toolkit_name = toolkit_data.get('name') or getattr(source_toolkit, 'toolkit_name', None) or getattr(source_toolkit, 'name', f"toolkit_{toolkit_id}")
            toolkit_type = toolkit_data.get('type') or getattr(source_toolkit, 'toolkit_type', None) or type(source_toolkit).__name__.lower().replace('apiwrapper', '').replace('elitea', '')

            log.info(f"Source toolkit: {toolkit_name} (type: {toolkit_type})")
            self.invocation_thinking(f"Loaded {toolkit_type} toolkit: {toolkit_name}")

            # Get inventory toolkit settings from request_data (passed when tool is invoked)
            inventory_settings = config.get("settings", {})

            # If settings not passed, fetch them from the platform API
            if not inventory_settings or not inventory_settings.get("toolkit_configuration_llm_model"):
                log.info(f"Settings not in request, fetching inventory toolkit {application_id} settings from platform...")
                try:
                    # Fetch raw toolkit data from platform API using correct path
                    inventory_toolkit_url = f"{elitea_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{application_id}"
                    log.info(f"[run_ingestion] Fetching inventory toolkit from: {inventory_toolkit_url}")
                    resp = http_requests.get(inventory_toolkit_url, headers=elitea_client.headers, verify=False)
                    if resp.ok:
                        inventory_toolkit_data = resp.json()
                        inventory_settings = inventory_toolkit_data.get("settings", {})
                        log.info(f"Fetched inventory toolkit settings keys: {list(inventory_settings.keys())}")
                    else:
                        log.warning(f"Failed to fetch inventory toolkit: {resp.status_code}")
                        inventory_settings = params
                except Exception as fetch_err:
                    log.warning(f"Could not fetch inventory toolkit settings: {fetch_err}")
                    inventory_settings = params  # Fall back to params

            log.info(f"Inventory toolkit settings keys: {list(inventory_settings.keys())}")

            # Get LLM configuration from inventory toolkit settings
            # The setting is stored with "toolkit_configuration_" prefix in the UI
            llm_model = (
                inventory_settings.get("toolkit_configuration_llm_model") or
                inventory_settings.get("llm_model") or
                params.get("llm_model")
            )

            if not llm_model:
                return "Error: No LLM model configured. Please set 'llm_model' in the inventory toolkit configuration."

            # Get embedding model for semantic search (optional — falls back to local HuggingFace)
            # Note: Entity embeddings always use the local all-MiniLM-L6-v2 model
            # to avoid configuration drift. The embedding_model setting is reserved
            # for future platform-level embedding features (e.g. vector store indexing).

            log.info(f"Using LLM model: {llm_model}")
            log.info(f"Inventory settings: {inventory_settings}")

            # Ensure graph directory exists
            graph_dir = Path(graph_path).parent
            graph_dir.mkdir(parents=True, exist_ok=True)

            # Initialize source status manager and mark ingestion as started
            status_manager = SourceStatusManager(str(graph_dir))
            status_manager.start_ingestion(
                toolkit_id=str(toolkit_id),
                toolkit_name=toolkit_name,
                toolkit_type=toolkit_type,
                branch=branch,
            )

            # Handle --fresh option: delete existing graph
            if full_rebuild and os.path.exists(graph_path):
                log.info(f"Fresh rebuild - deleting existing graph at {graph_path}")
                os.remove(graph_path)

            # Get per-source configuration from toolkit settings (if available)
            source_configs = (
                inventory_settings.get("toolkit_configuration_source_configs") or
                inventory_settings.get("source_configs") or
                {}
            )
            log.info(f"All source_configs: {source_configs}")
            log.info(f"Looking for toolkit_id key: '{toolkit_id}' (str: '{str(toolkit_id)}')")
            source_config = source_configs.get(str(toolkit_id), {})
            log.info(f"Source config for toolkit {toolkit_id}: {source_config}")

            # Use source-specific patterns if available, otherwise fall back to params
            effective_file_patterns = source_config.get("file_patterns") or file_patterns
            effective_exclude_patterns = source_config.get("exclude_patterns") or exclude_patterns

            # Override branch if specified in source config
            if source_config.get("branch") and not branch:
                branch = source_config.get("branch")

            log.info(f"Effective patterns - whitelist: '{effective_file_patterns}', blacklist: '{effective_exclude_patterns}'")

            # Build whitelist/blacklist from patterns
            whitelist = [p.strip() for p in effective_file_patterns.split(",") if p.strip()] if effective_file_patterns else None
            blacklist = [p.strip() for p in effective_exclude_patterns.split(",") if p.strip()] if effective_exclude_patterns else None

            self.invocation_thinking(f"Running ingestion from {toolkit_name}...")

            # Import ingestion modules
            from inventory import IngestionPipeline

            # Get LLM instance directly from client (reusing elitea_client created earlier)
            llm = elitea_client.get_llm(
                model_name=llm_model or 'gpt-4o-mini',
                model_config={'temperature': 0.0, 'max_tokens': 4096}
            )

            # Create progress callback that checks for stop requests and updates status
            def progress_callback(message, phase):
                self.invocation_thinking(f"[{phase}] {message}")
                # Update source status with progress message for UI display
                status_manager.update_progress(
                    toolkit_id=str(toolkit_id),
                    progress_message=message,
                )
                # Check for stop request periodically during ingestion
                self.invocation_stop_checkpoint()

            # Get ingestion config from plugin configuration
            ingestion_config = self.descriptor.config.get("ingestion", {})

            # Create ingestion pipeline with stop-aware progress callback and parallelization config
            pipeline = IngestionPipeline(
                llm=llm,
                elitea=elitea_client,
                graph_path=graph_path,
                auto_generate_embeddings=ingestion_config.get("generate_embeddings", True),
                progress_callback=progress_callback,
                # Parallelization settings from config
                max_parallel_extractions=ingestion_config.get("max_parallel_extractions", 10),
                batch_size=ingestion_config.get("batch_size", 10),
                max_parallel_chunks=ingestion_config.get("max_parallel_chunks", 5),
                min_file_lines=ingestion_config.get("min_file_lines", 20),
                min_file_chars=ingestion_config.get("min_file_chars", 300),
            )

            # source_toolkit was already instantiated via elitea_client.toolkit() above

            # Create a RunnableConfig for context (same as CLI does)
            import uuid
            cli_runnable_config = {
                'run_id': uuid.uuid4(),
                'tags': ['inventory_plugin', 'inventory', 'ingest'],
            }

            # Set the runnable config on the toolkit if it supports it
            if hasattr(source_toolkit, 'set_runnable_config'):
                source_toolkit.set_runnable_config(cli_runnable_config)

            # Register toolkit with the pipeline using toolkit name (not ID)
            # This ensures source_toolkit in citations uses human-readable name like "websearch"
            pipeline.register_toolkit(toolkit_name, source_toolkit)

            # Run the ingestion (same as CLI does)
            result = pipeline.run(
                source=toolkit_name,
                branch=branch,
                whitelist=whitelist,
                blacklist=blacklist,
                extract_relations=True,
            )

            # Clear cached wrapper to reload with new data
            if graph_path in self.graph_instances:
                del self.graph_instances[graph_path]

            # Update source status based on result
            if result.success:
                status_manager.complete_ingestion(
                    toolkit_id=str(toolkit_id),
                    entities_count=result.entities_added,
                    relations_count=result.relations_added,
                    documents_processed=result.documents_processed,
                )

                # Auto-normalize entity types after successful ingestion
                try:
                    log.info(f"[run_ingestion] Running automatic type normalization...")
                    self.invocation_thinking("Normalizing entity types...")
                    normalize_result = self._tool_normalize_types(
                        {"smart": True, "output_format": "json"},
                        graph_path,
                        request_data
                    )
                    if normalize_result:
                        normalize_data = json_module.loads(normalize_result) if isinstance(normalize_result, str) else normalize_result
                        types_reduced = normalize_data.get("types_reduced", 0)
                        if types_reduced > 0:
                            log.info(f"[run_ingestion] Type normalization reduced {types_reduced} types")
                        else:
                            log.info(f"[run_ingestion] Type normalization complete (no changes needed)")
                except Exception as norm_err:
                    log.warning(f"[run_ingestion] Type normalization failed (non-fatal): {norm_err}")
            else:
                error_summary = result.errors[0] if result.errors else "Unknown error"
                status_manager.fail_ingestion(
                    toolkit_id=str(toolkit_id),
                    error_message=error_summary,
                    documents_processed=result.documents_processed,
                )

            # Upload graph, checkpoints, and status to artifact bucket
            # Get bucket name from toolkit settings
            artifact_bucket = inventory_settings.get("toolkit_configuration_bucket", "graphs")
            try:
                # Upload main graph file if exists and ingestion succeeded
                if result.success and os.path.exists(graph_path):
                    with open(graph_path, 'rb') as f:
                        graph_data = f.read()
                    elitea_client.artifact(artifact_bucket).create("graph.json", graph_data)
                    log.info(f"Uploaded graph to artifact bucket: {artifact_bucket}/graph.json")

                # Upload checkpoint file if exists
                # Note: IngestionPipeline uses toolkit_name (e.g., "websearch") not toolkit_id for checkpoint filename
                checkpoint_file = os.path.join(str(graph_dir), f".ingestion-checkpoint-{toolkit_name}.json")
                if os.path.exists(checkpoint_file):
                    with open(checkpoint_file, 'rb') as f:
                        checkpoint_data = f.read()
                    checkpoint_artifact = f".ingestion-checkpoint-{toolkit_name}.json"
                    elitea_client.artifact(artifact_bucket).create(checkpoint_artifact, checkpoint_data)
                    log.info(f"Uploaded checkpoint to artifact bucket: {artifact_bucket}/{checkpoint_artifact}")

                # Always upload sources_status.json (even on failure, to track error state)
                status_file = os.path.join(str(graph_dir), "sources_status.json")
                if os.path.exists(status_file):
                    with open(status_file, 'rb') as f:
                        status_data = f.read()
                    elitea_client.artifact(artifact_bucket).create("sources_status.json", status_data)
                    log.info(f"Uploaded sources_status.json to artifact bucket: {artifact_bucket}/sources_status.json")
            except Exception as e:
                log.warning(f"Failed to upload artifacts to bucket: {e}")

            # Format result
            if output_format == "json":
                return json_module.dumps({
                    "success": result.success,
                    "source": result.source,
                    "documents_processed": result.documents_processed,
                    "entities_added": result.entities_added,
                    "relations_added": result.relations_added,
                    "errors": result.errors[:10] if result.errors else [],
                    "duration_seconds": result.duration_seconds,
                })

            if result.success:
                output = f"# Ingestion Complete: {toolkit_name}\n\n"
                output += f"**Source:** {result.source}\n"
                output += f"**Documents:** {result.documents_processed}\n"
                output += f"**Entities:** {result.entities_added}\n"
                output += f"**Relations:** {result.relations_added}\n"
                output += f"**Duration:** {result.duration_seconds:.1f}s\n"

                if result.errors:
                    output += f"\n**Warnings/Errors ({len(result.errors)}):**\n"
                    for err in result.errors[:5]:
                        output += f"- {err}\n"
                    if len(result.errors) > 5:
                        output += f"... and {len(result.errors) - 5} more\n"

                return output
            else:
                error_msg = f"Ingestion failed for {toolkit_name}\n\n"
                if result.errors:
                    error_msg += "Errors:\n"
                    for err in result.errors[:10]:
                        error_msg += f"- {err}\n"
                return error_msg

        except Exception as e:
            log.exception(f"Ingestion failed for toolkit {toolkit_id}")
            # Try to mark source status as error if status_manager was initialized
            try:
                if 'status_manager' in dir() or 'status_manager' in locals():
                    status_manager.fail_ingestion(
                        toolkit_id=str(toolkit_id),
                        error_message=str(e),
                        documents_processed=0,
                    )
                    # Try to upload the error status to artifacts
                    if 'elitea_client' in dir() or 'elitea_client' in locals():
                        status_file = os.path.join(str(graph_dir), "sources_status.json")
                        if os.path.exists(status_file):
                            with open(status_file, 'rb') as f:
                                status_data = f.read()
                            error_artifact_bucket = inventory_settings.get("toolkit_configuration_bucket", "graphs")
                            elitea_client.artifact(error_artifact_bucket).create("sources_status.json", status_data)
            except Exception as status_error:
                log.warning(f"Failed to update source status on error: {status_error}")
            return f"Error during ingestion: {str(e)}"

        finally:
            # Always release the ingestion slot when done
            if slot_acquired:
                try:
                    self.ingestion_tracker.release_slot(task_id)
                except Exception as release_error:
                    log.warning(f"Failed to release ingestion slot: {release_error}")

    @web.method()
    def _tool_delta_update(self, params, graph_path, request_data):
        """Run delta update for specific files"""
        toolkit_id = params.get("toolkit_id")
        file_paths = params.get("file_paths", "")
        branch = params.get("branch")

        if not toolkit_id:
            return "Error: toolkit_id is required"
        if not file_paths:
            return "Error: file_paths is required"

        # TODO: Implement delta update using EliteAClient
        return f"Delta update from toolkit {toolkit_id} - Not yet implemented.", []

    @web.method()
    def _tool_remove_source_entities(self, params, graph_path, request_data):
        """Remove all entities from a specific source toolkit"""
        toolkit_id = params.get("toolkit_id")

        if not toolkit_id:
            return "Error: toolkit_id is required"

        if not graph_path:
            return "Error: No graph loaded"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)

        # Get entities from this source
        source_toolkit = str(toolkit_id)
        removed_count = 0

        # Find and remove entities with this source_toolkit in their citations
        nodes_to_remove = []
        for node_id, data in wrapper._knowledge_graph._graph.nodes(data=True):
            citations = data.get('citations', [])
            if not citations and 'citation' in data:
                citations = [data['citation']]

            for citation in citations:
                if isinstance(citation, dict) and citation.get('source_toolkit') == source_toolkit:
                    nodes_to_remove.append(node_id)
                    break

        for node_id in nodes_to_remove:
            wrapper._knowledge_graph._graph.remove_node(node_id)
            removed_count += 1

        # Save graph
        wrapper._knowledge_graph.dump_to_json(graph_path)

        return f"Removed {removed_count} entities from toolkit {toolkit_id}"

    # ========== Graph Management Tools ==========

    @web.method()
    def _tool_list_ingested_sources(self, params, graph_path, request_data):
        """List all source toolkits that have been ingested"""
        import json as json_module

        output_format = params.get("output_format", "text")

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"sources": [], "error": "No graph configured"})
            return "No graph configured. Please set bucket and graph_name in toolkit configuration."

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        stats = wrapper._knowledge_graph.get_stats()

        sources = stats.get('source_toolkits', [])
        relations_by_source = stats.get('relations_by_source', {})

        # Count entities per source
        entities_by_source = {}
        for node_id, data in wrapper._knowledge_graph._graph.nodes(data=True):
            citations = data.get('citations', [])
            if not citations and 'citation' in data:
                citations = [data['citation']]

            for citation in citations:
                if isinstance(citation, dict):
                    source = citation.get('source_toolkit', 'unknown')
                    entities_by_source[source] = entities_by_source.get(source, 0) + 1

        if output_format == "json":
            return json_module.dumps({
                "sources": [
                    {
                        "source_toolkit": source,
                        "entity_count": entities_by_source.get(source, 0),
                        "relation_count": relations_by_source.get(source, 0),
                    }
                    for source in sources
                ],
                "total_sources": len(sources),
            })

        if not sources:
            return "No sources have been ingested yet. Use run_ingestion to add data from a toolkit."

        output = f"# Ingested Sources ({len(sources)})\n\n"
        for source in sources:
            entity_count = entities_by_source.get(source, 0)
            relation_count = relations_by_source.get(source, 0)
            output += f"- **{source}**: {entity_count} entities, {relation_count} relations\n"

        return output

    @web.method()
    def _tool_list_graphs(self, params, graph_path, request_data):
        """List available graphs in project"""
        import os
        import json as json_module

        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        output_format = params.get("output_format", "text")
        bucket_path = f"/data/graphs/{project_id}" if project_id else "/data/graphs"

        graphs = []
        if os.path.exists(bucket_path):
            for item in os.listdir(bucket_path):
                item_path = os.path.join(bucket_path, item)
                if os.path.isdir(item_path):
                    graph_file = os.path.join(item_path, "graph.json")
                    if os.path.exists(graph_file):
                        stat = os.stat(graph_file)
                        graphs.append({
                            "name": item,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        })

        if output_format == "json":
            return json_module.dumps({"graphs": graphs, "bucket": bucket})

        if not graphs:
            return f"No graphs found in bucket '{bucket}'"

        output = f"# Available Graphs in '{bucket}'\n\n"
        for g in graphs:
            size_kb = g["size"] / 1024
            output += f"- **{g['name']}** ({size_kb:.1f} KB)\n"

        return output

    @web.method()
    def _tool_load_graph(self, params, graph_path, request_data):
        """Load a specific graph"""
        bucket = params.get("bucket", "")
        graph_name = params.get("graph_name", "")

        if not graph_name:
            return "Error: graph_name is required"

        full_path = f"/data/graphs/{bucket}/{graph_name}/graph.json" if bucket else f"/data/graphs/{graph_name}/graph.json"

        # Clear cached wrapper to force reload
        if full_path in self.graph_instances:
            del self.graph_instances[full_path]

        wrapper = self._get_or_create_wrapper(full_path, request_data)
        stats = wrapper._knowledge_graph.get_stats()

        return f"Loaded graph: {graph_name}\nNodes: {stats['node_count']}, Edges: {stats['edge_count']}"

    @web.method()
    def _tool_get_graph_info(self, params, graph_path, request_data):
        """Get info about currently loaded graph"""
        import json as json_module

        output_format = params.get("output_format", "text")

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured"})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        stats = wrapper._knowledge_graph.get_stats()

        if output_format == "json":
            return json_module.dumps({
                "path": graph_path,
                **stats
            })

        return wrapper.get_stats()

    # ========== Retrieval Tools ==========

    @web.method()
    def _get_all_edges(self, wrapper):
        """Get ALL edges in the graph (for UI visualization)"""
        edges = []
        for source, target, data in wrapper._knowledge_graph._graph.edges(data=True):
            edges.append({
                "source": source,
                "target": target,
                "type": data.get("relation_type", "RELATED"),
                "properties": {k: v for k, v in data.items() if k != "relation_type"}
            })
        return edges

    @web.method()
    def _get_edges_for_entities(self, wrapper, entity_ids):
        """Get ALL edges connected to any entity in the set (for visualization)"""
        entity_set = set(entity_ids)
        edges = []
        connected_node_ids = set()

        for source, target, data in wrapper._knowledge_graph._graph.edges(data=True):
            # Include edge if either source OR target is in our entity set
            if source in entity_set or target in entity_set:
                edges.append({
                    "source": source,
                    "target": target,
                    "type": data.get("relation_type", "RELATED"),
                    "properties": {k: v for k, v in data.items() if k != "relation_type"}
                })
                # Track connected nodes that weren't in original search
                if source not in entity_set:
                    connected_node_ids.add(source)
                if target not in entity_set:
                    connected_node_ids.add(target)

        return edges, connected_node_ids

    @web.method()
    def _expand_entities_by_depth(self, wrapper, initial_entity_ids, max_depth):
        """
        Expand entity set to include connected nodes up to max_depth hops.
        
        Args:
            wrapper: Knowledge graph wrapper
            initial_entity_ids: Starting set of entity IDs
            max_depth: Maximum number of hops to traverse (0 = only initial entities)
            
        Returns:
            Tuple of (expanded_entity_ids, entity_results)
            - expanded_entity_ids: Set of all entity IDs including neighbors
            - entity_results: List of search result dictionaries for new entities
        """
        if max_depth <= 0:
            return set(initial_entity_ids), []
        
        graph = wrapper._knowledge_graph._graph
        current_layer = set(initial_entity_ids)
        all_entities = set(initial_entity_ids)
        new_entity_results = []
        
        # Perform BFS expansion for max_depth hops
        for depth in range(max_depth):
            next_layer = set()
            
            # Find all neighbors of current layer
            for entity_id in current_layer:
                if entity_id in graph:
                    # Get both incoming and outgoing neighbors
                    neighbors = set(graph.predecessors(entity_id)) | set(graph.successors(entity_id))
                    
                    # Add only new neighbors
                    new_neighbors = neighbors - all_entities
                    next_layer.update(new_neighbors)
            
            if not next_layer:
                break  # No more neighbors to expand
            
            # Get entity data for new neighbors
            for entity_id in next_layer:
                entity_data = graph.nodes.get(entity_id)
                if entity_data:
                    new_entity_results.append({
                        'entity': {
                            'id': entity_id,
                            **entity_data
                        },
                        'score': 0.5,  # Lower score for expanded nodes
                        'match_field': 'expanded'
                    })
            
            all_entities.update(next_layer)
            current_layer = next_layer
        
        return all_entities, new_entity_results

    @web.method()
    def _tool_search_graph(self, params, graph_path, request_data):
        """Search for entities"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        query = params.get("query", "")
        entity_type = params.get("entity_type")
        layer = params.get("layer")
        source_toolkit = params.get("source_toolkit")
        file_pattern = params.get("file_pattern")
        top_k = params.get("top_k", 10)
        max_depth = params.get("max_depth", 0)  # Get depth parameter
        show_all_edges = params.get("show_all_edges", True)  # Show ALL edges like visualize.py

        if output_format == "json":
            results = wrapper._knowledge_graph.search(
                query,
                top_k=top_k,
                entity_type=entity_type,
                layer=layer,
                file_pattern=file_pattern,
            )

            log.info(f"[DEBUG] Search returned {len(results)} results")
            if results:
                log.info(f"[DEBUG] First result keys: {results[0].keys()}")
                log.info(f"[DEBUG] First entity keys: {results[0]['entity'].keys()}")
                log.info(f"[DEBUG] First entity ID: {results[0]['entity'].get('id', 'NO ID FIELD')}")

            # Filter by source_toolkit if specified
            if source_toolkit:
                filtered = []
                for r in results:
                    entity = r['entity']
                    citations = entity.get('citations', [])
                    if not citations and 'citation' in entity:
                        citations = [entity['citation']]
                    for c in citations:
                        if isinstance(c, dict) and c.get('source_toolkit') == source_toolkit:
                            filtered.append(r)
                            break
                results = filtered

            # Get initial entity IDs from search results
            initial_entity_ids = [r['entity'].get('id') for r in results if r['entity'].get('id')]
            
            # Expand to connected nodes based on max_depth
            if max_depth > 0 and initial_entity_ids:
                log.info(f"[DEBUG] Expanding {len(initial_entity_ids)} entities with max_depth={max_depth}")
                expanded_ids, expanded_results = self._expand_entities_by_depth(
                    wrapper, initial_entity_ids, max_depth
                )
                log.info(f"[DEBUG] Expansion returned {len(expanded_ids)} total entities, {len(expanded_results)} new entities")
                # Combine initial results with expanded entities
                all_results = results + expanded_results
                entity_ids = list(expanded_ids)
            else:
                log.info(f"[DEBUG] No expansion: max_depth={max_depth}, initial_entity_ids={len(initial_entity_ids)}")
                all_results = results
                entity_ids = initial_entity_ids
            
            # Get ALL edges connected to entities (for visualization)
            if show_all_edges:
                # Show ALL edges in the graph, just for UI visualization
                edges = self._get_all_edges(wrapper)
                log.info(f"[DEBUG] Returning ALL {len(edges)} edges in graph (show_all_edges=True)")
            else:
                # Show only edges connected to search results
                edges, connected_node_ids = self._get_edges_for_entities(wrapper, entity_ids)
                log.info(f"[DEBUG] Found {len(edges)} edges for {len(entity_ids)} entities, {len(connected_node_ids)} additional connected nodes")
                
                # Add connected nodes to results so they can be displayed
                if connected_node_ids:
                    connected_results = []
                    for node_id in connected_node_ids:
                        if node_id in wrapper._knowledge_graph._graph.nodes:
                            node_data = dict(wrapper._knowledge_graph._graph.nodes[node_id])
                            node_data['id'] = node_id
                            connected_results.append({
                                'entity': node_data,
                                'score': 0.0,  # Connected nodes have 0 score since they didn't match the search
                                'match_field': 'connected',
                            })
                    all_results = all_results + connected_results
                    log.info(f"[DEBUG] Added {len(connected_results)} connected nodes to results")

            return json_module.dumps({
                "results": all_results,
                "edges": edges,
                "query": query,
                "filters": {
                    "entity_type": entity_type,
                    "layer": layer,
                    "source_toolkit": source_toolkit,
                    "file_pattern": file_pattern,
                    "max_depth": max_depth,
                },
                "total_results": len(all_results),
                "total_edges": len(edges),
                "initial_matches": len(results),
            })

        # Text format - use wrapper's method
        result = wrapper.search_graph(
            query=query,
            entity_type=entity_type,
            layer=layer,
            file_pattern=file_pattern,
            top_k=top_k,
        )

        # Filter by source_toolkit in text mode too
        if source_toolkit and "No entities found" not in result:
            # Re-run with filter for text output
            results = wrapper._knowledge_graph.search(
                query,
                top_k=top_k * 2,  # Get more to filter
                entity_type=entity_type,
                layer=layer,
                file_pattern=file_pattern,
            )
            filtered = []
            for r in results:
                entity = r['entity']
                citations = entity.get('citations', [])
                if not citations and 'citation' in entity:
                    citations = [entity['citation']]
                for c in citations:
                    if isinstance(c, dict) and c.get('source_toolkit') == source_toolkit:
                        filtered.append(r)
                        break

            if not filtered:
                return f"No entities found matching '{query}' from source '{source_toolkit}'"

            # Format filtered results
            output = f"Found {len(filtered[:top_k])} entities matching '{query}' from '{source_toolkit}':\n\n"
            for i, r in enumerate(filtered[:top_k], 1):
                entity = r['entity']
                output += f"{i:2}. **{entity.get('name')}** ({entity.get('type', 'unknown')})\n"
            return output

        return result

    @web.method()
    def _tool_get_entity(self, params, graph_path, request_data):
        """Get entity details"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        entity_name = params.get("entity_name", "")
        include_relations = params.get("include_relations", True)

        # Find entity supporting both "Name" and "Name (type)" formats
        entity, error_msg = self._find_entity_by_reference(wrapper, entity_name)
        if not entity:
            if output_format == "json":
                return json_module.dumps({"error": error_msg})
            return error_msg

        if output_format == "json":
            result = {"entity": entity}

            if include_relations:
                entity_id = entity.get("id")
                if entity_id:
                    relations = wrapper._knowledge_graph.get_relations(entity_id, direction="both")
                    incoming = []
                    outgoing = []
                    for rel in relations:
                        if rel["source"] == entity_id:
                            outgoing.append(rel)
                        else:
                            incoming.append(rel)
                    result["incoming"] = incoming
                    result["outgoing"] = outgoing

            return json_module.dumps(result)

        return wrapper.get_entity(entity_name, include_relations=include_relations)

    @web.method()
    def _tool_get_entity_content(self, params, graph_path, request_data):
        """Get entity source content"""
        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        entity_name = params.get("entity_name", "")
        return wrapper.get_entity_content(entity_name)

    @web.method()
    def _tool_impact_analysis(self, params, graph_path, request_data):
        """Perform impact analysis"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        entity_name = params.get("entity_name", "")
        direction = params.get("direction", "downstream")
        max_depth = params.get("max_depth", 3)

        # Find entity supporting both "Name" and "Name (type)" formats
        entity, error_msg = self._find_entity_by_reference(wrapper, entity_name)
        if not entity:
            if output_format == "json":
                return json_module.dumps({"error": error_msg})
            return error_msg

        if output_format == "json":
            entity_id = entity.get("id")
            if not entity_id:
                return json_module.dumps({"error": "Entity has no ID"})

            impact = wrapper._knowledge_graph.impact_analysis(
                entity_id, direction=direction, max_depth=max_depth
            )

            impacted_ids = [item['entity'].get('id') for item in impact.get('impacted', [])]
            all_ids = [entity_id] + impacted_ids
            edges = self._get_edges_for_entities(wrapper, all_ids)

            return json_module.dumps({
                "entity_name": entity_name,
                "direction": direction,
                "impacted": impact.get("impacted", []),
                "edges": edges,
            })

        return wrapper.impact_analysis(entity_name, direction=direction, max_depth=max_depth)

    @web.method()
    def _tool_get_related_entities(self, params, graph_path, request_data):
        """Get related entities"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        entity_name = params.get("entity_name", "")
        relation_type = params.get("relation_type")
        direction = params.get("direction", "both")

        # Find entity supporting both "Name" and "Name (type)" formats
        entity, error_msg = self._find_entity_by_reference(wrapper, entity_name)
        if not entity:
            if output_format == "json":
                return json_module.dumps({"error": error_msg})
            return error_msg

        if output_format == "json":
            entity_id = entity.get("id")
            if not entity_id:
                return json_module.dumps({"error": "Entity has no ID"})

            relations = wrapper._knowledge_graph.get_relations(entity_id, direction=direction)
            if relation_type:
                relations = [r for r in relations if r['relation_type'] == relation_type]

            return json_module.dumps({
                "entity_name": entity_name,
                "relations": relations,
            })

        return wrapper.get_related_entities(
            entity_name, relation_type=relation_type, direction=direction
        )

    @web.method()
    def _tool_query_graph(self, params, graph_path, request_data):
        """Query graph with structured filters - no similarity search"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        # Check for JQL query string parameter
        jql_query = params.get("query", "")
        if jql_query and not any(params.get(k) for k in ['types', 'layers', 'files', 'related_to']):
            # Parse JQL query and merge into params
            parsed = self._parse_graph_query(jql_query)
            for k, v in parsed.items():
                if k not in params or not params[k]:
                    params[k] = v

        # Helper to parse comma-separated strings to lists
        def to_list(val):
            if isinstance(val, list):
                return val
            if isinstance(val, str) and val.strip():
                return [v.strip() for v in val.split(',') if v.strip()]
            return []

        # Extract parameters
        entity_types = to_list(params.get("types", params.get("entity_types", [])))
        layers = to_list(params.get("layers", []))
        file_patterns = to_list(params.get("files", params.get("file_patterns", [])))
        text_filter = params.get("name", params.get("text", ""))
        has_relations = params.get("has_relations")
        limit = min(params.get("limit", 30), 100)

        # Relationship-based query
        related_to = params.get("related_to")
        relation_types = to_list(params.get("relation_types", []))
        relation_direction = params.get("direction", "both")

        # Handle relationship-based query
        if related_to:
            entity, error_msg = self._find_entity_by_reference(wrapper, related_to)
            if not entity:
                if output_format == "json":
                    return json_module.dumps({"error": error_msg})
                return error_msg

            entity_id = entity.get('id')
            if not entity_id:
                error = f"Entity '{entity.get('name')}' ({entity.get('type')}) has no ID. This may be a graph integrity issue."
                if output_format == "json":
                    return json_module.dumps({"error": error})
                return error

            # Get relations
            relations = wrapper._knowledge_graph.get_relations(entity_id, direction=relation_direction)

            # Filter by relation types
            if relation_types:
                rel_types_lower = [rt.lower() for rt in relation_types]
                relations = [r for r in relations if r.get('relation_type', '').lower() in rel_types_lower]

            # Collect related entities
            results = []
            seen_ids = set()

            for rel in relations:
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

                # Apply filters
                etype = related_entity.get('type', '').lower()
                elayer = related_entity.get('layer', '') or wrapper._knowledge_graph.TYPE_TO_LAYER.get(etype, '')

                if entity_types:
                    types_lower = [t.lower() for t in entity_types]
                    expanded_types = set(types_lower)
                    for t in types_lower:
                        if t in wrapper._knowledge_graph.LAYER_TYPE_MAPPING:
                            expanded_types.update(wrapper._knowledge_graph.LAYER_TYPE_MAPPING[t])
                    if etype not in expanded_types:
                        continue

                if layers:
                    if elayer.lower() not in [l.lower() for l in layers]:
                        continue

                if text_filter:
                    if text_filter.lower() not in related_entity.get('name', '').lower():
                        continue

                results.append({
                    'entity': related_entity,
                    'relation_type': rel.get('relation_type', 'RELATED'),
                    'direction': rel_dir,
                })

                if len(results) >= limit:
                    break

            if output_format == "json":
                return json_module.dumps({
                    "base_entity": entity,
                    "related": results,
                    "total": len(results),
                })

            if not results:
                return f"No related entities found for '{related_to}' matching filters."

            output = f"# Entities related to {entity.get('name', 'Unknown')} ({entity.get('type', '')})\n"
            output += f"Found {len(results)} results\n\n"

            for r in results:
                e = r['entity']
                arrow = "→" if r['direction'] == "outgoing" else "←"
                output += f"- {arrow} [{r['relation_type']}] **{e.get('name', 'Unknown')}** ({e.get('type', '')})"
                if e.get('source_toolkit'):
                    output += f" @ {e.get('source_toolkit')}"
                if e.get('file_path'):
                    output += f" - {e.get('file_path')}"
                output += "\n"

            return output

        # Standard structured query
        results = wrapper._knowledge_graph.search_advanced(
            query=text_filter if text_filter else None,
            entity_types=entity_types if entity_types else None,
            layers=layers if layers else None,
            file_patterns=file_patterns if file_patterns else None,
            has_relations=has_relations,
            top_k=limit,
        )

        if output_format == "json":
            return json_module.dumps({
                "results": results,
                "total": len(results),
                "filters": {
                    "types": entity_types,
                    "layers": layers,
                    "files": file_patterns,
                    "text": text_filter,
                }
            })

        if not results:
            filters = []
            if entity_types:
                filters.append(f"types={entity_types}")
            if layers:
                filters.append(f"layers={layers}")
            if file_patterns:
                filters.append(f"files={file_patterns}")
            if text_filter:
                filters.append(f"text='{text_filter}'")
            return f"No entities found matching filters: {', '.join(filters) or 'none'}"

        # Group by layer
        by_layer = {}
        for r in results:
            e = r['entity']
            etype = e.get('type', '').lower()
            layer = e.get('layer', '') or wrapper._knowledge_graph.TYPE_TO_LAYER.get(etype, 'other')
            if layer not in by_layer:
                by_layer[layer] = []
            by_layer[layer].append(r)

        output = f"# Query Results | {len(results)} entities\n"

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

        for layer in ['code', 'service', 'data', 'testing', 'configuration', 'documentation', 'domain', 'product', 'knowledge', 'structure', 'tooling', 'other']:
            if layer not in by_layer:
                continue
            entities = by_layer[layer]
            output += f"## {layer.title()} ({len(entities)})\n"

            for r in entities:
                e = r['entity']
                output += f"- **{e.get('name', 'Unknown')}** ({e.get('type', '')})"
                if e.get('source_toolkit'):
                    output += f" @ {e.get('source_toolkit')}"
                if e.get('file_path'):
                    output += f" - {e.get('file_path')}"
                output += "\n"

            output += "\n"

        return output

    @web.method()
    def _tool_get_cross_source_relations(self, params, graph_path, request_data):
        """Get relations connecting entities from different sources"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        cross_source = wrapper._knowledge_graph.get_cross_source_relations()

        if output_format == "json":
            return json_module.dumps({
                "relations": cross_source,
                "total": len(cross_source),
            })

        if not cross_source:
            return "No cross-source relations found. These appear when entities from different toolkits are related."

        output = f"# Cross-Source Relations ({len(cross_source)})\n\n"
        for rel in cross_source[:50]:
            source_name = rel.get('source', 'unknown')
            target_name = rel.get('target', 'unknown')
            rel_type = rel.get('relation_type', 'RELATED')
            source_toolkits = rel.get('source_toolkits', [])
            target_toolkits = rel.get('target_toolkits', [])
            output += f"- **{source_name}** ({', '.join(source_toolkits)}) → {rel_type} → **{target_name}** ({', '.join(target_toolkits)})\n"

        if len(cross_source) > 50:
            output += f"\n... and {len(cross_source) - 50} more\n"

        return output

    @web.method()
    def _tool_get_stats(self, params, graph_path, request_data):
        """Get graph statistics"""
        import os
        import json as json_module

        log.info(f"[DEBUG] _tool_get_stats called with graph_path: {graph_path}")
        log.info(f"[DEBUG] params: {params}")
        output_format = params.get("output_format", "text")

        if not graph_path:
            log.warning("[DEBUG] No graph_path configured!")
            if output_format == "json":
                return json_module.dumps({
                    "node_count": 0,
                    "edge_count": 0,
                    "entity_types": {},
                    "relation_types": {},
                    "sources": [],
                    "error": "No graph configured"
                })
            return "No graph configured"

        # Try to get or create wrapper - this will attempt artifact download if graph doesn't exist locally
        log.info(f"[DEBUG] Getting or creating wrapper for path: {graph_path}")
        wrapper = self._get_or_create_wrapper(graph_path, request_data)

        # Check if graph was loaded successfully (either from local or artifacts)
        if not os.path.exists(graph_path):
            log.info(f"[DEBUG] Graph file not found at {graph_path} after download attempt, returning empty stats")
            if output_format == "json":
                return json_module.dumps({
                    "node_count": 0,
                    "edge_count": 0,
                    "entity_types": {},
                    "relation_types": {},
                    "sources": [],
                    "edge_types": [],
                    "source_toolkits": [],
                    "message": "No graph data yet. Run ingestion to populate the knowledge graph."
                })
            return "No graph data yet. Run ingestion to populate the knowledge graph."

        log.info(f"[DEBUG] Got wrapper, output_format: {output_format}")

        if output_format == "json":
            stats = wrapper._knowledge_graph.get_stats()
            log.info(f"[DEBUG] Graph stats: {stats}")
            return json_module.dumps(stats)

        result = wrapper.get_stats()
        log.info(f"[DEBUG] Text stats result: {result}")
        return result

    @web.method()
    def _tool_list_entities_by_type(self, params, graph_path, request_data):
        """List entities by type"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        entity_type = params.get("entity_type", "")
        source_toolkit = params.get("source_toolkit")
        limit = params.get("limit", 50)

        entities = wrapper._knowledge_graph.get_entities_by_type(entity_type, limit=limit * 2 if source_toolkit else limit)

        # Filter by source_toolkit if specified
        if source_toolkit:
            filtered = []
            for entity in entities:
                citations = entity.get('citations', [])
                if not citations and 'citation' in entity:
                    citations = [entity['citation']]
                for c in citations:
                    if isinstance(c, dict) and c.get('source_toolkit') == source_toolkit:
                        filtered.append(entity)
                        break
            entities = filtered[:limit]

        if output_format == "json":
            entity_ids = [e.get('id') for e in entities if e.get('id')]
            edges = self._get_edges_for_entities(wrapper, entity_ids)

            return json_module.dumps({
                "entity_type": entity_type,
                "source_toolkit": source_toolkit,
                "entities": entities,
                "edges": edges,
                "total": len(entities),
            })

        if not entities:
            filter_info = f" from source '{source_toolkit}'" if source_toolkit else ""
            return f"No entities of type '{entity_type}' found{filter_info}"

        return wrapper.list_entities_by_type(entity_type, limit=limit)

    @web.method()
    def _tool_list_entities_by_layer(self, params, graph_path, request_data):
        """List entities by layer"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        layer = params.get("layer", "")
        source_toolkit = params.get("source_toolkit")
        limit = params.get("limit", 50)

        entities = wrapper._knowledge_graph.get_entities_by_layer(layer, limit=limit * 2 if source_toolkit else limit)

        # Filter by source_toolkit if specified
        if source_toolkit:
            filtered = []
            for entity in entities:
                citations = entity.get('citations', [])
                if not citations and 'citation' in entity:
                    citations = [entity['citation']]
                for c in citations:
                    if isinstance(c, dict) and c.get('source_toolkit') == source_toolkit:
                        filtered.append(entity)
                        break
            entities = filtered[:limit]

        if output_format == "json":
            entity_ids = [e.get('id') for e in entities if e.get('id')]
            edges = self._get_edges_for_entities(wrapper, entity_ids)

            return json_module.dumps({
                "layer": layer,
                "source_toolkit": source_toolkit,
                "entities": entities,
                "edges": edges,
                "total": len(entities),
            })

        if not entities:
            filter_info = f" from source '{source_toolkit}'" if source_toolkit else ""
            return f"No entities in layer '{layer}' found{filter_info}"

        return wrapper.list_entities_by_layer(layer, limit=limit)

    @web.method()
    def _tool_list_entities_by_source(self, params, graph_path, request_data):
        """List entities from a specific source toolkit"""
        import json as json_module

        if not graph_path:
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        output_format = params.get("output_format", "text")

        source_toolkit = params.get("source_toolkit", "")
        entity_type = params.get("entity_type")
        limit = params.get("limit", 50)

        if not source_toolkit:
            return "Error: source_toolkit is required"

        # Find all entities from this source
        entities = []
        for node_id, data in wrapper._knowledge_graph._graph.nodes(data=True):
            if entity_type and data.get('type', '').lower() != entity_type.lower():
                continue

            citations = data.get('citations', [])
            if not citations and 'citation' in data:
                citations = [data['citation']]

            for c in citations:
                if isinstance(c, dict) and c.get('source_toolkit') == source_toolkit:
                    entities.append(dict(data))
                    break

            if len(entities) >= limit:
                break

        if output_format == "json":
            entity_ids = [e.get('id') for e in entities if e.get('id')]
            edges = self._get_edges_for_entities(wrapper, entity_ids)

            return json_module.dumps({
                "source_toolkit": source_toolkit,
                "entity_type": entity_type,
                "entities": entities,
                "edges": edges,
                "total": len(entities),
            })

        if not entities:
            filter_info = f" of type '{entity_type}'" if entity_type else ""
            return f"No entities{filter_info} found from source '{source_toolkit}'"

        output = f"# Entities from '{source_toolkit}' ({len(entities)})\n\n"
        for entity in entities:
            etype = entity.get('type', 'unknown')
            output += f"- **{entity.get('name')}** ({etype})\n"

        if len(entities) == limit:
            output += f"\n*Limited to {limit} results*\n"

        return output

    # ========== Preset Tools ==========

    @web.method()
    def _tool_list_presets(self, params, graph_path, request_data):
        """List available presets"""
        from inventory import list_presets, PRESETS

        presets = list_presets()
        output = "# Available Ingestion Presets\n\n"
        for name in presets:
            preset = PRESETS.get(name, {})
            desc = preset.get("description", "")
            output += f"- **{name}**: {desc}\n"

        return output

    @web.method()
    def _tool_get_preset_info(self, params, graph_path, request_data):
        """Get preset details"""
        from inventory import get_preset, PRESETS

        preset_name = params.get("preset_name", "")
        preset = get_preset(preset_name)

        if not preset:
            return f"Preset '{preset_name}' not found"

        output = f"# Preset: {preset_name}\n\n"
        output += f"**Description:** {preset.get('description', 'N/A')}\n\n"

        if preset.get("include_patterns"):
            output += "**Include Patterns:**\n"
            for p in preset["include_patterns"]:
                output += f"- `{p}`\n"

        if preset.get("exclude_patterns"):
            output += "\n**Exclude Patterns:**\n"
            for p in preset["exclude_patterns"]:
                output += f"- `{p}`\n"

        return output

    # ========== Cache Management Tools ==========

    @web.method()
    def _tool_get_cache_stats(self, params, graph_path, request_data):
        """Get cache statistics"""
        import json as json_module

        output_format = params.get("output_format", "text")

        stats = self.cache_manager.get_cache_stats()
        graphs = self.cache_manager.list_cached_graphs()

        if output_format == "json":
            return json_module.dumps({
                "stats": stats,
                "graphs": graphs,
            })

        output = "# Graph Cache Statistics\n\n"
        output += f"**Total Graphs:** {stats['total_graphs']}\n"
        output += f"**Total Size:** {stats['total_size_mb']:.1f} MB / {stats['max_size_mb']:.1f} MB ({stats['usage_percent']:.1f}%)\n"
        output += f"**Max Graphs:** {stats['max_graphs']}\n"
        output += f"**Max Age:** {stats['max_age_days']:.0f} days\n\n"

        if graphs:
            output += "## Cached Graphs\n\n"
            for g in graphs:
                size_mb = g['size_bytes'] / 1024 / 1024
                metadata = g.get('metadata', {})
                last_accessed = metadata.get('last_accessed', 'unknown') if metadata else 'unknown'
                output += f"- **{g['bucket']}/{g['graph_name']}** ({size_mb:.1f} MB)\n"
                output += f"  Last accessed: {last_accessed}\n"

        return output

    @web.method()
    def _tool_cleanup_cache(self, params, graph_path, request_data):
        """Force cache cleanup"""
        import json as json_module

        output_format = params.get("output_format", "text")

        # Run cleanup
        result = self.cache_manager.cleanup_stale_graphs()

        if output_format == "json":
            return json_module.dumps(result)

        if result['removed'] == 0:
            return "No stale graphs found. Cache is within limits."

        output = f"# Cache Cleanup Complete\n\n"
        output += f"**Removed:** {result['removed']} graph(s)\n"
        output += f"**Freed:** {result['freed_mb']:.1f} MB\n\n"

        if result.get('removed_graphs'):
            output += "**Removed graphs:**\n"
            for g in result['removed_graphs']:
                output += f"- {g}\n"

        return output

    # ========== Source Status Tools ==========

    @web.method()
    def _tool_get_sources_status(self, params, graph_path, request_data):
        """
        Get status of all sources added to the knowledge graph.

        Returns status, last update time, and entity/relation counts for each source.
        Used by UI to display source card status badges.
        """
        import os
        import json as json_module
        from pathlib import Path
        from ..utils.source_status import SourceStatusManager

        output_format = params.get("output_format", "text")

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({
                    "sources": [],
                    "total_sources": 0,
                    "error": "No graph configured"
                })
            return "No graph configured. Please set bucket and graph_name in toolkit configuration."

        # Get graph directory
        graph_dir = Path(graph_path).parent

        # Try to download status from artifacts if not present locally
        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        application_id = config.get("application_id") or params.get("application_id")
        # Get artifact bucket from toolkit settings
        settings = config.get("settings", {})
        artifact_bucket = settings.get("toolkit_configuration_bucket", "graphs")

        status_file = graph_dir / "sources_status.json"
        if not status_file.exists() and project_id and application_id:
            # Try to download from artifacts using EliteAClient
            try:
                elitea_client = self._get_elitea_client(project_id)
                if elitea_client:
                    status_data = elitea_client.artifact(artifact_bucket).get("sources_status.json")
                    if status_data and not self._is_artifact_error(status_data):
                        graph_dir.mkdir(parents=True, exist_ok=True)
                        with open(status_file, 'w', encoding='utf-8') as f:
                            f.write(status_data)
                        log.info(f"Downloaded sources_status.json from artifacts")
            except Exception as e:
                log.debug(f"Could not download sources_status.json from artifacts: {e}")

        # Initialize status manager and get summary
        status_manager = SourceStatusManager(str(graph_dir))
        summary = status_manager.get_status_summary()

        if output_format == "json":
            return json_module.dumps(summary)

        # Format as text
        if summary["total_sources"] == 0:
            return "No sources have been ingested yet. Use run_ingestion to add data from a toolkit."

        output = f"# Source Status ({summary['total_sources']} sources)\n\n"

        # Show status counts
        counts = summary["status_counts"]
        output += f"**Completed:** {counts.get('completed', 0)} | "
        output += f"**In Progress:** {counts.get('in_progress', 0)} | "
        output += f"**Error:** {counts.get('error', 0)} | "
        output += f"**Pending:** {counts.get('pending', 0)}\n\n"

        output += f"**Total Entities:** {summary['total_entities']} | "
        output += f"**Total Relations:** {summary['total_relations']}\n\n"

        # List each source
        output += "## Sources\n\n"
        for source in summary["sources"]:
            status_icon = {
                "completed": "OK",
                "in_progress": "...",
                "error": "ERR",
                "pending": "---",
            }.get(source.get("status", "pending"), "?")

            output += f"- [{status_icon}] **{source.get('toolkit_name', 'Unknown')}** "
            output += f"(ID: {source.get('toolkit_id', '?')}, Type: {source.get('toolkit_type', '?')})\n"
            output += f"  - Status: {source.get('status', 'unknown')}\n"
            output += f"  - Last Updated: {source.get('last_updated', 'never')}\n"
            output += f"  - Entities: {source.get('entities_count', 0)}, Relations: {source.get('relations_count', 0)}\n"

            if source.get("error_message"):
                output += f"  - Error: {source.get('error_message')}\n"

            if source.get("branch"):
                output += f"  - Branch: {source.get('branch')}\n"

            output += "\n"

        return output

    # ========== Entity Batch Retrieval (for Chat Highlighting) ==========

    @web.method()
    def _tool_get_entities_by_ids(self, params, graph_path, request_data):
        """
        Fetch entities by their IDs along with the edges connecting them.

        Used by the chat UI to display and highlight entities that were
        accessed during a chat response.

        Parameters:
            entity_ids: List of entity IDs to fetch
            include_edges: Whether to include edges between entities (default: True)
            include_bridging: Whether to include bridging nodes that connect disjoint clusters (default: True)
            max_bridge_length: Max path length for bridging (default: 4, meaning up to 3 intermediate nodes)
            output_format: "json" or "text" (default: "json")

        Returns:
            Graph data with results (entities) and edges between them
        """
        import json as json_module

        output_format = params.get("output_format", "json")
        entity_ids = params.get("entity_ids", [])
        include_edges = params.get("include_edges", True)
        include_bridging = params.get("include_bridging", True)
        max_bridge_length = params.get("max_bridge_length", 4)

        if not entity_ids:
            if output_format == "json":
                return json_module.dumps({
                    "results": [],
                    "edges": [],
                    "error": "No entity_ids provided"
                })
            return "No entity_ids provided"

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({
                    "results": [],
                    "edges": [],
                    "error": "No graph configured"
                })
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        # Fetch entities by ID
        results = []
        entity_id_set = set(entity_ids)

        for entity_id in entity_ids:
            entity = kg.get_entity(entity_id)
            if entity:
                # Add the id to the entity (not included by get_entity since it's the graph key)
                entity['id'] = entity_id
                results.append({
                    "entity": entity,
                    "score": 1.0,
                })

        # Find and add bridging nodes to connect disjoint clusters
        bridging_info = {'bridging_nodes': [], 'bridging_edges': [], 'clusters': 1}
        if include_bridging and len(results) > 1:
            bridging_info = kg.find_bridging_nodes(
                entity_ids,
                max_bridge_length=max_bridge_length,
                max_bridges=20
            )

            # Add bridging nodes to results (marked as bridging)
            for bridge_id in bridging_info.get('bridging_nodes', []):
                if bridge_id not in entity_id_set:
                    entity = kg.get_entity(bridge_id)
                    if entity:
                        entity['id'] = bridge_id
                        entity['is_bridging'] = True  # Mark as bridging node
                        results.append({
                            "entity": entity,
                            "score": 0.5,  # Lower score for bridging nodes
                        })
                        entity_id_set.add(bridge_id)

        # Collect edges that connect our entities (including bridging nodes)
        edges = []
        if include_edges and len(results) > 0:
            # Get edges from the networkx graph between our entities
            for source_id in entity_id_set:
                if kg._graph.has_node(source_id):
                    for _, target_id, data in kg._graph.out_edges(source_id, data=True):
                        if target_id in entity_id_set:
                            edges.append({
                                'source': source_id,
                                'target': target_id,
                                'type': data.get('relation_type', 'RELATED'),
                            })

        # Add bridging edges if not already included
        if include_bridging:
            existing_edges = set(f"{e['source']}--{e['type']}-->{e['target']}" for e in edges)
            for edge in bridging_info.get('bridging_edges', []):
                edge_key = f"{edge['source']}--{edge['type']}-->{edge['target']}"
                if edge_key not in existing_edges:
                    edges.append(edge)
                    existing_edges.add(edge_key)

        if output_format == "json":
            return json_module.dumps({
                "results": results,
                "edges": edges,
                "total_entities": len(results),
                "total_edges": len(edges),
                "clusters_found": bridging_info.get('clusters', 1),
                "bridging_nodes_added": len(bridging_info.get('bridging_nodes', [])),
            })

        # Format as text
        if not results:
            return f"No entities found for the provided IDs."

        output = f"Found {len(results)} entities and {len(edges)} connecting edges:\n\n"
        for r in results:
            entity = r['entity']
            output += f"- **{entity.get('name')}** ({entity.get('type', 'unknown')})\n"
            output += f"  ID: {entity.get('id')}\n"
            if entity.get('description'):
                output += f"  Description: {entity.get('description')[:100]}...\n"
            output += "\n"

        if edges:
            output += "\n## Edges:\n"
            for edge in edges[:20]:  # Limit to first 20 edges
                output += f"- {edge.get('source')} --[{edge.get('type', 'RELATED')}]--> {edge.get('target')}\n"
            if len(edges) > 20:
                output += f"  ...and {len(edges) - 20} more edges\n"

        return output

    # ========== Ingestion Status Tools ==========

    @web.method()
    def _tool_get_ingestion_status(self, params, graph_path, request_data):
        """Get current ingestion status for this project/toolkit"""
        import json as json_module

        output_format = params.get("output_format", "text")

        # Get project_id and application_id from request context
        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        application_id = config.get("application_id") or params.get("application_id")

        # Get all active ingestions
        active_ingestions = self.ingestion_tracker.get_active_ingestions()
        tracker_status = self.ingestion_tracker.get_status()

        # Find ingestion for current project/application
        current_ingestion = None
        for ing in active_ingestions:
            if (str(ing.get("project_id")) == str(project_id) and
                str(ing.get("application_id")) == str(application_id)):
                current_ingestion = ing
                break

        # If there's an active ingestion, try to get progress_message from sources_status.json
        if current_ingestion and graph_path:
            try:
                from pathlib import Path
                from ..utils.source_status import SourceStatusManager
                # graph_path is the full path to graph.json, but SourceStatusManager expects directory
                graph_dir = str(Path(graph_path).parent)
                status_manager = SourceStatusManager(graph_dir)
                sources_status = status_manager.get_sources()
                toolkit_id = str(current_ingestion.get("toolkit_id"))
                if toolkit_id in sources_status:
                    source_info = sources_status[toolkit_id]
                    current_ingestion["progress_message"] = source_info.get("progress_message")
                    current_ingestion["toolkit_name"] = source_info.get("toolkit_name", f"toolkit_{toolkit_id}")
            except Exception as e:
                log.debug(f"Could not get progress_message from sources_status: {e}")

        result = {
            "has_active_ingestion": current_ingestion is not None,
            "current_ingestion": current_ingestion,
            "max_parallel": tracker_status["max_parallel"],
            "active_count": tracker_status["active_count"],
            "available_slots": tracker_status["available_slots"],
            "all_active_ingestions": active_ingestions,
        }

        if output_format == "json":
            return json_module.dumps(result)

        if current_ingestion:
            output = "# Active Ingestion\n\n"
            output += f"**Task ID:** {current_ingestion.get('task_id', 'unknown')}\n"
            output += f"**Source Toolkit:** {current_ingestion.get('toolkit_id', 'unknown')}\n"
            output += f"**Started:** {current_ingestion.get('started_at', 'unknown')}\n"
            if current_ingestion.get("progress_message"):
                output += f"**Progress:** {current_ingestion.get('progress_message')}\n"
            output += f"\nSlots: {tracker_status['active_count']}/{tracker_status['max_parallel']} in use\n"
        else:
            output = "No active ingestion for this toolkit.\n\n"
            output += f"Slots: {tracker_status['active_count']}/{tracker_status['max_parallel']} in use\n"

        return output

    # ========== Entity Neighbor Expansion (for Graph UI Context Menu) ==========

    @web.method()
    def _tool_get_entity_neighbors(self, params, graph_path, request_data):
        """
        Get neighbors of an entity up to a specified depth level.

        Used by the graph UI context menu to expand connections 1-3 levels deep.

        Parameters:
            entity_id: ID of the entity to expand from
            depth: Number of hops to expand (1, 2, or 3)
            output_format: "json" or "text" (default: "json")

        Returns:
            Graph data with results (entities) and edges connecting them
        """
        import json as json_module

        output_format = params.get("output_format", "json")
        entity_id = params.get("entity_id")
        depth = params.get("depth", 1)

        # Validate depth
        depth = max(1, min(3, int(depth)))

        if not entity_id:
            if output_format == "json":
                return json_module.dumps({
                    "results": [],
                    "edges": [],
                    "error": "No entity_id provided"
                })
            return "No entity_id provided"

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({
                    "results": [],
                    "edges": [],
                    "error": "No graph configured"
                })
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph
        graph = kg._graph

        # Check if entity exists
        if entity_id not in graph:
            if output_format == "json":
                return json_module.dumps({
                    "results": [],
                    "edges": [],
                    "error": f"Entity '{entity_id}' not found in graph"
                })
            return f"Entity '{entity_id}' not found in graph"

        # Perform BFS expansion to find all neighbors up to depth
        current_layer = {entity_id}
        all_entity_ids = {entity_id}

        for _ in range(depth):
            next_layer = set()
            for eid in current_layer:
                if eid in graph:
                    # Get both incoming and outgoing neighbors
                    neighbors = set(graph.predecessors(eid)) | set(graph.successors(eid))
                    # Add only new neighbors
                    new_neighbors = neighbors - all_entity_ids
                    next_layer.update(new_neighbors)

            if not next_layer:
                break  # No more neighbors to expand

            all_entity_ids.update(next_layer)
            current_layer = next_layer

        # Build results list
        results = []
        for eid in all_entity_ids:
            entity_data = graph.nodes.get(eid)
            if entity_data:
                entity = dict(entity_data)
                entity['id'] = eid
                # Mark the original entity
                entity['is_origin'] = (eid == entity_id)
                results.append({
                    'entity': entity,
                    'score': 1.0 if eid == entity_id else 0.5,
                })

        # Collect edges that connect our entities
        edges = []
        for source_id in all_entity_ids:
            if graph.has_node(source_id):
                for _, target_id, data in graph.out_edges(source_id, data=True):
                    if target_id in all_entity_ids:
                        edges.append({
                            'source': source_id,
                            'target': target_id,
                            'type': data.get('relation_type', 'RELATED'),
                        })

        if output_format == "json":
            return json_module.dumps({
                "results": results,
                "edges": edges,
                "total_entities": len(results),
                "total_edges": len(edges),
                "origin_entity_id": entity_id,
                "depth": depth,
            })

        # Format as text
        output = f"Found {len(results)} entities within {depth} hop(s) of '{entity_id}':\n\n"
        for r in results:
            entity = r['entity']
            marker = " (origin)" if entity.get('is_origin') else ""
            output += f"- **{entity.get('name')}** ({entity.get('type', 'unknown')}){marker}\n"

        if edges:
            output += f"\n{len(edges)} edges connecting these entities.\n"

        return output

    # ========== Graph Maintenance Tools ==========

    @web.method()
    def _tool_normalize_types(self, params, graph_path, request_data):
        """
        Normalize all entity types in the graph to canonical forms.

        This performs two-stage normalization:
        1. Rule-based: Consolidates variations like "Feature", "Features", "feature" into "feature"
        2. Smart (LLM-based): Maps remaining types to ~50 canonical types for consistency

        Parameters:
            smart: bool (default True) - Whether to run LLM-based smart normalization after rule-based
            smart_threshold: int (default len(CANONICAL_TYPES)) - Only run smart normalization if types exceed this threshold

        Returns statistics about normalized types.
        """
        import json as json_module

        output_format = params.get("output_format", "json")
        run_smart = params.get("smart", True)
        smart_threshold = params.get("smart_threshold", len(CANONICAL_TYPES))

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured", "normalized_count": 0})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        # Get stats before normalization
        stats_before = kg.get_stats()
        types_before = len(stats_before.get('entity_types', {}))

        # Stage 1: Rule-based normalization via index rebuild
        kg._rebuild_indices()
        kg.dump_to_json(graph_path)

        # Get stats after rule-based normalization
        stats_after_rules = kg.get_stats()
        types_after_rules = len(stats_after_rules.get('entity_types', {}))

        smart_result = None
        types_final = types_after_rules

        # Stage 2: Smart (LLM-based) normalization if enabled and threshold exceeded
        if run_smart and types_after_rules > smart_threshold:
            log.info(f"[normalize_types] Running smart normalization: {types_after_rules} types > threshold {smart_threshold}")
            try:
                smart_result = self._tool_smart_normalize_types(
                    {"dry_run": False},
                    graph_path,
                    request_data
                )
                # Parse result to get final count
                if isinstance(smart_result, str):
                    smart_data = json_module.loads(smart_result)
                    types_final = smart_data.get("types_after", types_after_rules)
            except Exception as e:
                log.error(f"[normalize_types] Smart normalization failed: {e}")
                smart_result = {"error": str(e)}

        # Get final stats
        stats_final = kg.get_stats()
        types_final = len(stats_final.get('entity_types', {}))

        result = {
            "success": True,
            "types_before": types_before,
            "types_after_rules": types_after_rules,
            "types_after": types_final,
            "types_reduced": types_before - types_final,
            "rule_based_reduction": types_before - types_after_rules,
            "smart_reduction": types_after_rules - types_final if run_smart else 0,
            "smart_normalization_ran": run_smart and types_after_rules > smart_threshold,
            "entity_types": stats_final.get('entity_types', {}),
            "message": f"Normalized entity types: {types_before} -> {types_final} unique types (rule-based: {types_after_rules}, smart: {types_final})"
        }

        if output_format == "json":
            return json_module.dumps(result)

        return f"Type normalization complete:\n- Types before: {types_before}\n- After rule-based: {types_after_rules}\n- After smart: {types_final}\n- Total reduced: {types_before - types_final}"

    @web.method()
    def _tool_rebuild_indices(self, params, graph_path, request_data):
        """
        Rebuild all graph indices (name, type, file, source).

        Use this after manual graph modifications or to fix index inconsistencies.
        Also normalizes entity types during rebuild.
        """
        import json as json_module

        output_format = params.get("output_format", "json")

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured"})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        # Rebuild all indices
        kg._rebuild_indices()

        # Save updated graph
        kg.dump_to_json(graph_path)

        # Get updated stats
        stats = kg.get_stats()

        result = {
            "success": True,
            "entity_count": stats.get('entity_count', 0),
            "relation_count": stats.get('relation_count', 0),
            "unique_types": len(stats.get('entity_types', {})),
            "unique_names": len(kg._entity_index),
            "indexed_files": len(kg._file_index),
            "message": "Indices rebuilt successfully"
        }

        if output_format == "json":
            return json_module.dumps(result)

        return f"Indices rebuilt:\n- Entities: {result['entity_count']}\n- Relations: {result['relation_count']}\n- Types: {result['unique_types']}\n- Files: {result['indexed_files']}"

    @web.method()
    def _tool_smart_normalize_types(self, params, graph_path, request_data):
        """
        Use LLM to intelligently normalize entity types to a small set of canonical types.

        This tool sends uncommon entity types to an LLM which maps them to canonical types.
        Uses structured output to ensure consistent mapping format.

        Parameters:
            threshold: Only normalize types with count below this (default: 1000)
            dry_run: If true, show proposed changes without applying (default: false)
            llm_model: LLM model to use (default: from toolkit config)
            batch_size: Number of types to process per LLM call (default: 100)

        Returns:
            Statistics about normalized types and the mapping applied.
        """
        import json as json_module
        from pydantic import BaseModel, Field
        from typing import Dict, List

        output_format = params.get("output_format", "json")
        threshold = int(params.get("threshold", 1000))
        dry_run = str(params.get("dry_run", "false")).lower() == "true"
        batch_size = int(params.get("batch_size", 100))

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured"})
            return "No graph configured"

        # CANONICAL_TYPES is imported from constants.py

        # Pydantic model for structured output
        class TypeMapping(BaseModel):
            """Mapping of original type to canonical type."""
            original: str = Field(description="The original entity type name")
            canonical: str = Field(description="The canonical type to map to")
            confidence: float = Field(description="Confidence score 0-1", ge=0, le=1)

        class TypeMappingResponse(BaseModel):
            """Response containing all type mappings."""
            mappings: List[TypeMapping] = Field(description="List of type mappings")

        self.invocation_thinking("Loading graph and analyzing types...")

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        # Get current type statistics
        stats = kg.get_stats()
        entity_types = stats.get('entity_types', {})

        # Filter types below threshold (these are candidates for normalization)
        types_to_normalize = {
            t: count for t, count in entity_types.items()
            if count < threshold and t not in CANONICAL_TYPES
        }

        if not types_to_normalize:
            result = {
                "success": True,
                "message": f"No types to normalize (all types either have count >= {threshold} or are already canonical)",
                "types_checked": len(entity_types),
                "canonical_types": len(CANONICAL_TYPES),
            }
            if output_format == "json":
                return json_module.dumps(result)
            return result["message"]

        self.invocation_thinking(f"Found {len(types_to_normalize)} types to normalize...")

        # Get LLM for smart normalization
        config = request_data.get("configuration", {})
        project_id = config.get("project_id") or params.get("project_id")
        inventory_settings = config.get("settings", {})

        llm_model = (
            params.get("llm_model") or
            inventory_settings.get("toolkit_configuration_llm_model") or
            inventory_settings.get("llm_model") or
            "gpt-4o-mini"
        )

        elitea_client = self._get_elitea_client(project_id)
        if not elitea_client:
            if output_format == "json":
                return json_module.dumps({"error": "Platform API not configured"})
            return "Error: Platform API not configured"

        llm = elitea_client.get_llm(
            model_name=llm_model,
            model_config={'temperature': 0.0, 'max_tokens': 4096}
        )

        # Create structured LLM
        structured_llm = llm.with_structured_output(TypeMappingResponse)

        # Process types in batches
        all_mappings = {}
        type_list = list(types_to_normalize.keys())
        total_batches = (len(type_list) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(type_list))
            batch_types = type_list[start_idx:end_idx]

            self.invocation_thinking(f"Processing batch {batch_num + 1}/{total_batches} ({len(batch_types)} types)...")

            # Build prompt
            prompt = f"""You are a knowledge graph type normalizer. Map each entity type to the most appropriate canonical type.

CANONICAL TYPES (you MUST map to one of these):
{', '.join(sorted(CANONICAL_TYPES))}

RULES:
1. Map each type to the SINGLE most appropriate canonical type from the list above
2. Consider semantic meaning, not just string similarity
3. Types ending in _rule, _policy, _constraint → "rule"
4. Types ending in _requirement → "requirement"
5. Types ending in _step, _procedure → "process" or "step"
6. Types ending in _example, _sample → "example"
7. Types ending in _guide, _guideline, _note, _documentation → "documentation"
8. Types ending in _parameter, _field, _attribute → "parameter"
9. Types ending in _feature, _capability → "feature"
10. Types ending in _config, _setting, _option → "configuration"
11. Types about facts, behaviors, details, info → "fact"
12. Types about UI, interaction, presentation → "component" or "feature"
13. Unknown or unclear types → "fact" (safest default)

TYPES TO NORMALIZE:
{json_module.dumps(batch_types, indent=2)}

Map each type to exactly one canonical type. Be aggressive in consolidation - we want fewer unique types."""

            try:
                response = structured_llm.invoke(prompt)

                for mapping in response.mappings:
                    if mapping.canonical in CANONICAL_TYPES:
                        all_mappings[mapping.original] = mapping.canonical
                    else:
                        # If LLM returned non-canonical type, default to "fact"
                        all_mappings[mapping.original] = "fact"

            except Exception as e:
                log.warning(f"Error processing batch {batch_num + 1}: {e}")
                # Fallback: map all types in this batch to "fact"
                for t in batch_types:
                    all_mappings[t] = "fact"

            # Check for stop request
            self.invocation_stop_checkpoint()

        self.invocation_thinking(f"Generated {len(all_mappings)} type mappings...")

        # Calculate impact
        entities_affected = sum(types_to_normalize.get(t, 0) for t in all_mappings.keys())
        target_type_counts = {}
        for orig, canonical in all_mappings.items():
            count = types_to_normalize.get(orig, 0)
            target_type_counts[canonical] = target_type_counts.get(canonical, 0) + count

        if dry_run:
            # Return preview without applying
            result = {
                "success": True,
                "dry_run": True,
                "types_to_normalize": len(all_mappings),
                "entities_affected": entities_affected,
                "target_types": len(set(all_mappings.values())),
                "mapping_preview": dict(list(all_mappings.items())[:50]),  # First 50 mappings
                "target_type_distribution": target_type_counts,
                "message": f"DRY RUN: Would normalize {len(all_mappings)} types affecting {entities_affected} entities"
            }
            if output_format == "json":
                return json_module.dumps(result)
            return result["message"]

        # Apply mappings to the graph
        self.invocation_thinking("Applying type mappings to graph...")

        normalized_count = 0
        for node_id in kg._graph.nodes():
            node_data = kg._graph.nodes[node_id]
            current_type = node_data.get('type', '')
            if current_type in all_mappings:
                node_data['type'] = all_mappings[current_type]
                normalized_count += 1

        # Rebuild indices after type changes
        kg._rebuild_indices()

        # Save the updated graph
        kg.dump_to_json(graph_path)

        # Get final stats
        final_stats = kg.get_stats()
        final_types = len(final_stats.get('entity_types', {}))

        result = {
            "success": True,
            "dry_run": False,
            "types_before": len(entity_types),
            "types_after": final_types,
            "types_reduced": len(entity_types) - final_types,
            "entities_normalized": normalized_count,
            "mappings_applied": len(all_mappings),
            "llm_model": llm_model,
            "message": f"Smart normalization complete: {len(entity_types)} → {final_types} types ({normalized_count} entities updated)"
        }

        if output_format == "json":
            return json_module.dumps(result)

        return result["message"]

    @web.method()
    def _tool_get_type_stats(self, params, graph_path, request_data):
        """
        Get detailed statistics about entity types in the graph.

        Useful for identifying type fragmentation before normalization.
        """
        import json as json_module

        output_format = params.get("output_format", "json")
        show_all = params.get("show_all", False)

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured", "types": {}})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph
        stats = kg.get_stats()

        entity_types = stats.get('entity_types', {})

        # Sort by count descending
        sorted_types = sorted(entity_types.items(), key=lambda x: x[1], reverse=True)

        # Identify potential duplicates (similar names)
        potential_duplicates = []
        type_names = list(entity_types.keys())
        for i, t1 in enumerate(type_names):
            for t2 in type_names[i+1:]:
                # Check if one is plural of other or differs only by case/underscore
                t1_norm = t1.lower().replace('_', '').replace('-', '')
                t2_norm = t2.lower().replace('_', '').replace('-', '')
                if t1_norm.rstrip('s') == t2_norm.rstrip('s') and t1 != t2:
                    potential_duplicates.append((t1, entity_types[t1], t2, entity_types[t2]))

        result = {
            "total_types": len(entity_types),
            "total_entities": sum(entity_types.values()),
            "types": dict(sorted_types[:50]) if not show_all else dict(sorted_types),
            "potential_duplicates": potential_duplicates[:20],
            "top_10": dict(sorted_types[:10]),
        }

        if output_format == "json":
            return json_module.dumps(result)

        output = f"Entity Type Statistics:\n"
        output += f"- Total unique types: {result['total_types']}\n"
        output += f"- Total entities: {result['total_entities']}\n\n"

        output += "Top 10 types:\n"
        for t, count in sorted_types[:10]:
            output += f"  {t}: {count}\n"

        if potential_duplicates:
            output += f"\nPotential duplicates ({len(potential_duplicates)}):\n"
            for t1, c1, t2, c2 in potential_duplicates[:10]:
                output += f"  '{t1}' ({c1}) vs '{t2}' ({c2})\n"

        return output

    # ========== Graph Enrichment Tools ==========

    @web.method()
    def _tool_link_toolkits_to_tools(self, params, graph_path, request_data):
        """
        Create explicit links between toolkits and their tools.

        Matches tools to toolkits based on:
        - Same source file path
        - Toolkit name appearing in tool name
        - parent_toolkit property

        Returns count of links created.
        """
        import json as json_module
        import re
        from collections import defaultdict

        output_format = params.get("output_format", "json")

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured", "links_created": 0})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        links_created = 0
        matches = []

        # Get all entities
        all_entities = []
        for node_id in kg._graph.nodes():
            node_data = kg._graph.nodes[node_id]
            all_entities.append({
                "id": node_id,
                "name": node_data.get("name", ""),
                "type": node_data.get("type", ""),
                "file_path": node_data.get("file_path", ""),
                "properties": node_data.get("properties", {}),
            })

        # Index toolkits and tools
        toolkits = [e for e in all_entities if e["type"].lower() == "toolkit"]
        tools = [e for e in all_entities if e["type"].lower() == "tool"]

        # Index toolkits by file and name
        toolkit_by_file = defaultdict(list)
        toolkit_by_name = {}

        for tk in toolkits:
            if tk["file_path"]:
                toolkit_by_file[tk["file_path"]].append(tk)
            name = tk["name"].lower()
            if name:
                toolkit_by_name[name] = tk
                # Also index by short name (without " toolkit" suffix)
                short_name = re.sub(r'\s*toolkit$', '', name, flags=re.IGNORECASE)
                toolkit_by_name[short_name] = tk

        # Match tools to toolkits
        for tool in tools:
            tool_id = tool["id"]
            tool_file = tool["file_path"]
            tool_name = tool["name"].lower()
            parent_toolkit = tool.get("properties", {}).get("parent_toolkit", "").lower()

            matched_toolkit = None
            match_reason = ""

            # Strategy 1: Match by parent_toolkit property
            if parent_toolkit:
                for tk_name, tk in toolkit_by_name.items():
                    if tk_name in parent_toolkit or parent_toolkit in tk_name:
                        matched_toolkit = tk
                        match_reason = f"parent_toolkit:{parent_toolkit}"
                        break

            # Strategy 2: Match by same file path
            if not matched_toolkit and tool_file:
                if tool_file in toolkit_by_file:
                    matched_toolkit = toolkit_by_file[tool_file][0]
                    match_reason = f"same_file"

            # Strategy 3: Match by tool name containing toolkit name
            if not matched_toolkit:
                for tk_name, tk in toolkit_by_name.items():
                    if len(tk_name) >= 3 and tk_name in tool_name:
                        matched_toolkit = tk
                        match_reason = f"name_match:{tk_name}"
                        break

            # Create link if matched and doesn't exist
            if matched_toolkit:
                # Check if edge already exists
                if not kg._graph.has_edge(matched_toolkit["id"], tool_id):
                    kg._graph.add_edge(
                        matched_toolkit["id"],
                        tool_id,
                        relation_type="contains",
                        enrichment_reason=f"toolkit_tool:{match_reason}"
                    )
                    links_created += 1
                    matches.append({
                        "toolkit": matched_toolkit["name"],
                        "tool": tool["name"],
                        "reason": match_reason
                    })

        # Save graph
        kg.dump_to_json(graph_path)

        result = {
            "success": True,
            "links_created": links_created,
            "toolkits_found": len(toolkits),
            "tools_found": len(tools),
            "matches": matches[:20],  # First 20 matches
            "message": f"Created {links_created} toolkit → tool links"
        }

        if output_format == "json":
            return json_module.dumps(result)

        output = f"Toolkit-Tool Linking:\n"
        output += f"- Toolkits found: {len(toolkits)}\n"
        output += f"- Tools found: {len(tools)}\n"
        output += f"- Links created: {links_created}\n"
        if matches:
            output += "\nSample matches:\n"
            for m in matches[:10]:
                output += f"  {m['toolkit']} → {m['tool']} ({m['reason']})\n"

        return output

    @web.method()
    def _tool_connect_orphan_nodes(self, params, graph_path, request_data):
        """
        Connect orphan nodes (nodes with no edges) to related entities.

        Uses word overlap in entity names to find potential relationships.
        Helps improve graph connectivity for isolated nodes.

        Parameters:
        - max_links_per_orphan: Maximum links to create per orphan (default: 3)
        - min_word_overlap: Minimum word overlap ratio (default: 0.3)
        """
        import json as json_module
        import re
        from difflib import SequenceMatcher

        output_format = params.get("output_format", "json")
        max_links = params.get("max_links_per_orphan", 3)
        min_overlap = params.get("min_word_overlap", 0.3)

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured", "links_created": 0})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        def normalize_name(name):
            name = name.lower().strip()
            name = re.sub(r'[_\-\.]+', ' ', name)
            return re.sub(r'\s+', ' ', name)

        def tokenize(name):
            stop_words = {'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'for', 'on', 'with', 'by', 'is', 'it'}
            words = set(normalize_name(name).split())
            return words - stop_words

        def similarity(s1, s2):
            return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

        # Find connected and orphan nodes
        connected = set()
        for u, v in kg._graph.edges():
            connected.add(u)
            connected.add(v)

        orphans = []
        non_orphans = []
        for node_id in kg._graph.nodes():
            node_data = kg._graph.nodes[node_id]
            entity = {
                "id": node_id,
                "name": node_data.get("name", ""),
                "type": node_data.get("type", ""),
            }
            if node_id in connected:
                non_orphans.append(entity)
            else:
                orphans.append(entity)

        links_created = 0
        connections = []

        # For each orphan, find potential connections
        for orphan in orphans:
            orphan_name = normalize_name(orphan["name"])
            orphan_words = tokenize(orphan["name"])

            if not orphan_words:
                continue

            candidates = []

            for node in non_orphans:
                node_name = normalize_name(node["name"])
                node_words = tokenize(node["name"])

                if not node_words:
                    continue

                # Calculate word overlap
                overlap = len(orphan_words & node_words)
                if overlap > 0:
                    word_score = overlap / max(len(orphan_words), len(node_words))
                    str_sim = similarity(orphan_name, node_name)
                    score = (word_score + str_sim) / 2

                    if score >= min_overlap:
                        candidates.append((node, score))

            # Sort and take top candidates
            candidates.sort(key=lambda x: x[1], reverse=True)

            for node, score in candidates[:max_links]:
                if not kg._graph.has_edge(orphan["id"], node["id"]):
                    kg._graph.add_edge(
                        orphan["id"],
                        node["id"],
                        relation_type="related_to",
                        enrichment_reason=f"orphan_link:score={score:.2f}"
                    )
                    links_created += 1
                    connections.append({
                        "orphan": orphan["name"],
                        "connected_to": node["name"],
                        "score": round(score, 2)
                    })

        # Save graph
        kg.dump_to_json(graph_path)

        result = {
            "success": True,
            "orphans_found": len(orphans),
            "links_created": links_created,
            "connections": connections[:20],
            "message": f"Connected {links_created} orphan nodes"
        }

        if output_format == "json":
            return json_module.dumps(result)

        output = f"Orphan Node Connection:\n"
        output += f"- Orphans found: {len(orphans)}\n"
        output += f"- Links created: {links_created}\n"
        if connections:
            output += "\nSample connections:\n"
            for c in connections[:10]:
                output += f"  {c['orphan']} → {c['connected_to']} (score: {c['score']})\n"

        return output

    @web.method()
    def _tool_validate_relationships(self, params, graph_path, request_data):
        """
        Validate relationships using heuristic rules.

        Checks relationships for semantic validity based on entity types:
        - 'imports' should only be between code entities
        - 'implements' should have code as source
        - 'tests' should have test_case as source
        - 'contains' should have container types as source

        Returns count of validated, upgraded, and removed relationships.
        """
        import json as json_module

        output_format = params.get("output_format", "json")
        confidence_threshold = params.get("confidence_threshold", 0.7)
        remove_invalid = params.get("remove_invalid", False)  # Conservative: don't remove by default

        if not graph_path:
            if output_format == "json":
                return json_module.dumps({"error": "No graph configured"})
            return "No graph configured"

        wrapper = self._get_or_create_wrapper(graph_path, request_data)
        kg = wrapper._knowledge_graph

        # Define validation rules
        invalid_combinations = {
            "imports": {
                "invalid_source": {"feature", "concept", "documentation", "requirement"},
                "invalid_target": {"feature", "concept", "documentation", "requirement"},
            },
            "implements": {
                "invalid_source": {"documentation", "concept", "glossary_term"},
            },
            "contains": {
                "invalid_source": {"constant", "variable", "field", "property"},
            },
            "tests": {
                "invalid_source": {"class", "function", "method", "module"},
            },
        }

        valid_combinations = {
            ("class", "interface", "implements"): 0.9,
            ("method", "function", "calls"): 0.85,
            ("test", "function", "tests"): 0.9,
            ("test", "class", "tests"): 0.9,
            ("documentation", "class", "documents"): 0.85,
            ("toolkit", "tool", "contains"): 0.95,
            ("module", "class", "contains"): 0.9,
            ("class", "method", "contains"): 0.95,
        }

        stats = {
            "total_edges": 0,
            "validated": 0,
            "upgraded": 0,
            "invalid": 0,
            "removed": 0,
        }

        edges_to_remove = []
        invalid_edges = []

        for u, v, data in kg._graph.edges(data=True):
            stats["total_edges"] += 1

            source_data = kg._graph.nodes.get(u, {})
            target_data = kg._graph.nodes.get(v, {})
            source_type = source_data.get("type", "").lower()
            target_type = target_data.get("type", "").lower()
            relation_type = data.get("relation_type", "").lower()
            confidence = data.get("confidence", 1.0)

            # Check for invalid combinations
            is_invalid = False
            if relation_type in invalid_combinations:
                rules = invalid_combinations[relation_type]
                if source_type in rules.get("invalid_source", set()):
                    is_invalid = True
                if target_type in rules.get("invalid_target", set()):
                    is_invalid = True

            if is_invalid:
                stats["invalid"] += 1
                invalid_edges.append({
                    "source": source_data.get("name", u),
                    "target": target_data.get("name", v),
                    "relation": relation_type,
                    "reason": f"invalid_{source_type}→{target_type}"
                })
                if remove_invalid:
                    edges_to_remove.append((u, v))
                continue

            # Check for valid combinations that should boost confidence
            combo_key = (source_type, target_type, relation_type)
            if combo_key in valid_combinations:
                suggested_confidence = valid_combinations[combo_key]
                if confidence < suggested_confidence:
                    kg._graph.edges[u, v]["confidence"] = suggested_confidence
                    stats["upgraded"] += 1

            stats["validated"] += 1

        # Remove invalid edges if requested
        if remove_invalid:
            for u, v in edges_to_remove:
                kg._graph.remove_edge(u, v)
                stats["removed"] += 1

        # Save graph
        kg.dump_to_json(graph_path)

        result = {
            "success": True,
            "total_edges": stats["total_edges"],
            "validated": stats["validated"],
            "upgraded": stats["upgraded"],
            "invalid_found": stats["invalid"],
            "removed": stats["removed"],
            "invalid_edges": invalid_edges[:20],
            "message": f"Validated {stats['validated']} edges, found {stats['invalid']} invalid"
        }

        if output_format == "json":
            return json_module.dumps(result)

        output = f"Relationship Validation:\n"
        output += f"- Total edges: {stats['total_edges']}\n"
        output += f"- Validated: {stats['validated']}\n"
        output += f"- Upgraded confidence: {stats['upgraded']}\n"
        output += f"- Invalid found: {stats['invalid']}\n"
        output += f"- Removed: {stats['removed']}\n"

        if invalid_edges:
            output += "\nInvalid relationships:\n"
            for e in invalid_edges[:10]:
                output += f"  {e['source']} --[{e['relation']}]--> {e['target']} ({e['reason']})\n"

        return output
