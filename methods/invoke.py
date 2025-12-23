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
    def _get_alita_client(self, project_id: int):
        """Create AlitaClient instance for platform API calls.

        Args:
            project_id: The project ID to use for the client

        Returns:
            AlitaClient instance or None if platform config is missing
        """
        from alita_sdk.runtime.clients.client import AlitaClient

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

        return AlitaClient(
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

        # Validate toolkit - now only "inventory" is valid
        if toolkit_name != "inventory":
            return self._create_error_response(
                invocation_id=invocation_id,
                operation=tool_name,
                exception=ValueError(f"Unknown toolkit: {toolkit_name}. Expected: inventory"),
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

            # Route to tool handler
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

    # ========== Graph/Wrapper Management ==========

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

        # Create AlitaClient using helper method
        alita_client = self._get_alita_client(project_id)
        if not alita_client:
            log.warning("Cannot download from artifacts: Platform API URL or token not configured")
            return False

        graph_dir = os.path.dirname(graph_path)

        try:
            # Download main graph file
            artifact_name = "graph.json"
            log.info(f"Downloading graph from artifacts: {artifact_bucket}/{artifact_name}")
            graph_data = alita_client.artifact(artifact_bucket).get(artifact_name)

            if graph_data and not self._is_artifact_error(graph_data):
                # Create directory if needed
                Path(graph_dir).mkdir(parents=True, exist_ok=True)

                # Write graph file
                with open(graph_path, 'w', encoding='utf-8') as f:
                    f.write(graph_data)
                log.info(f"Downloaded graph to {graph_path}")

                # Try to download sources_status.json
                try:
                    status_data = alita_client.artifact(artifact_bucket).get("sources_status.json")
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
                    artifacts = alita_client.artifact(artifact_bucket).list(return_as_string=False)
                    checkpoint_prefix = ".ingestion-checkpoint-"

                    for artifact_info in artifacts:
                        if isinstance(artifact_info, dict):
                            artifact_path = artifact_info.get('name', '')
                            if artifact_path.startswith(checkpoint_prefix):
                                checkpoint_data = alita_client.artifact(artifact_bucket).get(artifact_path)
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

        If graph doesn't exist locally, tries to download from artifact bucket
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

            # Create AlitaClient for platform API calls
            alita_client = self._get_alita_client(project_id)
            if not alita_client:
                return "Error: Platform API URL or token not configured. Check PLATFORM_API_URL and AI_RUN_PLATFORM_TOKEN."

            self.invocation_thinking(f"Connecting to platform at {alita_client.base_url}...")

            # Instantiate source toolkit
            # Note: toolkit_id parameter refers to the SOURCE toolkit (GitHub/ADO/GitLab),
            # not the inventory toolkit itself
            # We fetch directly using the correct API path (/api/v2/elitea_core) because
            # AlitaClient.toolkit() uses the old /api/v1 path
            self.invocation_thinking(f"Loading source toolkit {toolkit_id}...")
            log.info(f"[run_ingestion] Loading source toolkit {toolkit_id}")
            try:
                import requests as http_requests
                from alita_sdk.tools import instantiate_toolkit

                # Fetch toolkit data using correct API path
                toolkit_api_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{toolkit_id}"
                log.info(f"[run_ingestion] Fetching toolkit from: {toolkit_api_url}")

                resp = http_requests.get(toolkit_api_url, headers=alita_client.headers, verify=False)
                if not resp.ok:
                    log.error(f"[run_ingestion] Failed to fetch toolkit: {resp.status_code} - {resp.text}")
                    return f"Error: Failed to fetch source toolkit {toolkit_id}: {resp.status_code}"

                toolkit_data = resp.json()
                log.info(f"[run_ingestion] Got toolkit data: {toolkit_data.get('name', 'unknown')}, type: {toolkit_data.get('type', 'unknown')}")

                # Add alita client to settings (same as AlitaClient.toolkit() does)
                if 'settings' not in toolkit_data:
                    toolkit_data['settings'] = {}
                toolkit_data['settings']['alita'] = alita_client

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

            # Extract toolkit metadata from the api_wrapper
            toolkit_name = getattr(source_toolkit, 'toolkit_name', None) or getattr(source_toolkit, 'name', f"toolkit_{toolkit_id}")
            toolkit_type = getattr(source_toolkit, 'toolkit_type', None) or type(source_toolkit).__name__.lower().replace('apiwrapper', '').replace('alita', '')

            log.info(f"Source toolkit: {toolkit_name} (type: {toolkit_type})")
            self.invocation_thinking(f"Loaded {toolkit_type} toolkit: {toolkit_name}")

            # Get inventory toolkit settings from request_data (passed when tool is invoked)
            inventory_settings = config.get("settings", {})

            # If settings not passed, fetch them from the platform API
            if not inventory_settings or not inventory_settings.get("toolkit_configuration_llm_model"):
                log.info(f"Settings not in request, fetching inventory toolkit {application_id} settings from platform...")
                try:
                    # Fetch raw toolkit data from platform API using correct path
                    inventory_toolkit_url = f"{alita_client.base_url}/api/v2/elitea_core/tool/prompt_lib/{project_id}/{application_id}"
                    log.info(f"[run_ingestion] Fetching inventory toolkit from: {inventory_toolkit_url}")
                    resp = http_requests.get(inventory_toolkit_url, headers=alita_client.headers, verify=False)
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

            # Get LLM instance directly from client (reusing alita_client created earlier)
            llm = alita_client.get_llm(
                model_name=llm_model or 'gpt-4o-mini',
                model_config={'temperature': 0.0, 'max_tokens': 4096}
            )

            # Create progress callback that checks for stop requests
            def progress_callback(message, phase):
                self.invocation_thinking(f"[{phase}] {message}")
                # Check for stop request periodically during ingestion
                self.invocation_stop_checkpoint()

            # Get ingestion config from plugin configuration
            ingestion_config = self.descriptor.config.get("ingestion", {})

            # Create ingestion pipeline with stop-aware progress callback and parallelization config
            pipeline = IngestionPipeline(
                llm=llm,
                alita=alita_client,
                graph_path=graph_path,
                progress_callback=progress_callback,
                # Parallelization settings from config
                max_parallel_extractions=ingestion_config.get("max_parallel_extractions", 10),
                batch_size=ingestion_config.get("batch_size", 10),
                max_parallel_chunks=ingestion_config.get("max_parallel_chunks", 5),
                min_file_lines=ingestion_config.get("min_file_lines", 20),
                min_file_chars=ingestion_config.get("min_file_chars", 300),
            )

            # source_toolkit was already instantiated via alita_client.toolkit() above

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
                    alita_client.artifact(artifact_bucket).create("graph.json", graph_data)
                    log.info(f"Uploaded graph to artifact bucket: {artifact_bucket}/graph.json")

                # Upload checkpoint file if exists
                # Note: IngestionPipeline uses toolkit_name (e.g., "websearch") not toolkit_id for checkpoint filename
                checkpoint_file = os.path.join(str(graph_dir), f".ingestion-checkpoint-{toolkit_name}.json")
                if os.path.exists(checkpoint_file):
                    with open(checkpoint_file, 'rb') as f:
                        checkpoint_data = f.read()
                    checkpoint_artifact = f".ingestion-checkpoint-{toolkit_name}.json"
                    alita_client.artifact(artifact_bucket).create(checkpoint_artifact, checkpoint_data)
                    log.info(f"Uploaded checkpoint to artifact bucket: {artifact_bucket}/{checkpoint_artifact}")

                # Always upload sources_status.json (even on failure, to track error state)
                status_file = os.path.join(str(graph_dir), "sources_status.json")
                if os.path.exists(status_file):
                    with open(status_file, 'rb') as f:
                        status_data = f.read()
                    alita_client.artifact(artifact_bucket).create("sources_status.json", status_data)
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
                    if 'alita_client' in dir() or 'alita_client' in locals():
                        status_file = os.path.join(str(graph_dir), "sources_status.json")
                        if os.path.exists(status_file):
                            with open(status_file, 'rb') as f:
                                status_data = f.read()
                            error_artifact_bucket = inventory_settings.get("toolkit_configuration_bucket", "graphs")
                            alita_client.artifact(error_artifact_bucket).create("sources_status.json", status_data)
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

        # TODO: Implement delta update using AlitaClient
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
        """Get ALL edges in the graph (like visualize.py does)"""
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
        """Get ALL edges connected to any entity in the set (like visualize.py)"""
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
            
            # Get ALL edges connected to entities (like visualize.py)
            if show_all_edges:
                # Show ALL edges in the graph, just like visualize.py does
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

        if output_format == "json":
            entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
            if not entity:
                return json_module.dumps({"error": f"Entity '{entity_name}' not found"})

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

        if output_format == "json":
            entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
            if not entity:
                return json_module.dumps({"error": f"Entity '{entity_name}' not found"})

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

        if output_format == "json":
            entity = wrapper._knowledge_graph.find_entity_by_name(entity_name)
            if not entity:
                return json_module.dumps({"error": f"Entity '{entity_name}' not found"})

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
            # Try to download from artifacts using AlitaClient
            try:
                alita_client = self._get_alita_client(project_id)
                if alita_client:
                    status_data = alita_client.artifact(artifact_bucket).get("sources_status.json")
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
            output += f"**Started:** {current_ingestion.get('started_at', 'unknown')}\n\n"
            output += f"Slots: {tracker_status['active_count']}/{tracker_status['max_parallel']} in use\n"
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
