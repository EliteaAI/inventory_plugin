#!/usr/bin/python3
# coding=utf-8

"""Provider Descriptor Method"""

from pylon.core.tools import log
from pylon.core.tools import web


class Method:
    """
    Method Resource

    self is pointing to current Module instance

    web.method decorator takes zero or one argument: method name
    Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def provider_descriptor(self):
        """Return the provider descriptor for platform registration"""
        service_location_url = self.descriptor.config.get(
            "service_location_url", "http://127.0.0.1:8091"
        )

        return {
            "name": "inventory",
            "service_location_url": service_location_url,
            "configuration": {
                "provided_ui": [
                    {
                        "name": "ui",
                        "path": "/ui",
                        "headers": {
                            "X-User-Id": {"type": "user_id"},
                            "X-Project-Id": {"type": "project_id"}
                        }
                    }
                ]
            },
            "provided_toolkits": [
                # Single unified inventory toolkit
                {
                    "name": "inventory",
                    "description": "Knowledge Graph for codebase understanding. Build graphs from multiple data sources, search entities, analyze dependencies, and visualize code structure.",
                    "toolkit_config": {
                        "type": "Inventory Knowledge Graph Configuration",
                        "description": "Configure your knowledge graph storage and processing settings. Data sources (repositories, wikis, etc.) are added after creation through the UI or tools.",
                        "fields_order": [
                            "bucket",
                            "llm_model",
                            "embedding_model",
                            "graph_name",
                            "sources"
                        ],
                        "parameters": {
                            "bucket": {
                                "type": "String",
                                "required": True,
                                "description": "Storage bucket for knowledge graph data and configurations"
                            },
                            "llm_model": {
                                "type": "String",
                                "required": True,
                                "description": "LLM model for entity and relation extraction",
                                "json_schema_extra": {
                                    "configuration_model": "llm"
                                }
                            },
                            "embedding_model": {
                                "type": "String",
                                "required": False,
                                "description": "Embedding model for semantic search (optional)",
                                "json_schema_extra": {
                                    "configuration_model": "embedding"
                                },
                                "default": ""
                            },
                            "graph_name": {
                                "type": "String",
                                "required": False,
                                "description": "Name for the primary graph (default: 'main')",
                                "default": "main"
                            },
                            "sources": {
                                "type": "List[Integer]",
                                "required": False,
                                "description": "List of source toolkit IDs for data ingestion",
                                "default": [],
                                "json_schema_extra": {
                                    "toolkit_types": ["github", "ado", "gitlab", "bitbucket"]
                                }
                            },
                            "source_configs": {
                                "type": "Dict",
                                "required": False,
                                "description": "Per-source configuration (keyed by toolkit ID). Each config can have: file_patterns (whitelist), exclude_patterns (blacklist), branch, preset",
                                "default": {},
                                "json_schema_extra": {
                                    "hidden": True
                                }
                            }
                        }
                    },
                    "provided_tools": [
                        # ========== Ingestion Tools ==========
                        {
                            "name": "run_ingestion",
                            "description": "Run ingestion pipeline for a toolkit to build/update the knowledge graph. Uses AlitaClient to fetch from the source toolkit.",
                            "args_schema": {
                                "toolkit_id": {
                                    "type": "Integer",
                                    "required": True,
                                    "description": "Toolkit ID of the data source (GitHub, ADO, GitLab, etc.)"
                                },
                                "branch": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Branch to analyze (uses toolkit default if not specified)"
                                },
                                "file_patterns": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Comma-separated file patterns to include (e.g., '**/*.py,**/*.js')"
                                },
                                "exclude_patterns": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Comma-separated patterns to exclude (e.g., '**/test/**,**/vendor/**')"
                                },
                                "full_rebuild": {
                                    "type": "Boolean",
                                    "required": False,
                                    "description": "Force full rebuild instead of incremental update",
                                    "default": False
                                }
                            },
                            "tool_metadata": {
                                "result_composition": "list_of_objects",
                                "result_objects": [
                                    {
                                        "object_type": "message",
                                        "result_target": "response",
                                        "result_encoding": "plain"
                                    }
                                ]
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": False,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "delta_update",
                            "description": "Update specific files from a toolkit (faster than full ingestion)",
                            "args_schema": {
                                "toolkit_id": {
                                    "type": "Integer",
                                    "required": True,
                                    "description": "Toolkit ID of the data source"
                                },
                                "file_paths": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Comma-separated file paths to update"
                                },
                                "branch": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Branch to fetch files from"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": False,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "remove_source_entities",
                            "description": "Remove all entities from a specific source toolkit from the graph",
                            "args_schema": {
                                "toolkit_id": {
                                    "type": "Integer",
                                    "required": True,
                                    "description": "Toolkit ID whose entities should be removed"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        # ========== Graph Management ==========
                        {
                            "name": "list_ingested_sources",
                            "description": "List all source toolkits that have been ingested into the current graph, with entity counts per source",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "list_graphs",
                            "description": "List all available knowledge graphs in this inventory",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "load_graph",
                            "description": "Load/switch to a specific knowledge graph",
                            "args_schema": {
                                "graph_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the graph to load"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_graph_info",
                            "description": "Get metadata about the currently loaded graph including sources, entity counts, and last update",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        # ========== Search & Retrieval ==========
                        {
                            "name": "search_graph",
                            "description": "Search for entities with token matching. Supports 'chat message' finding 'ChatMessageHandler', file patterns, layer and source filtering",
                            "args_schema": {
                                "query": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Search query for finding entities"
                                },
                                "entity_type": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by entity type (class, function, service, etc.)"
                                },
                                "layer": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by layer (code, service, data, product, domain)"
                                },
                                "source_toolkit": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by source toolkit (e.g., 'github', 'ado', or toolkit ID)"
                                },
                                "file_pattern": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by file path pattern (e.g., '**/chat*.py')"
                                },
                                "top_k": {
                                    "type": "Integer",
                                    "required": False,
                                    "description": "Maximum number of results",
                                    "default": 10
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json' (includes edges for visualization)",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_entity",
                            "description": "Get detailed information about a specific entity including properties, relations, and all source citations",
                            "args_schema": {
                                "entity_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the entity to retrieve"
                                },
                                "include_relations": {
                                    "type": "Boolean",
                                    "required": False,
                                    "description": "Include related entities",
                                    "default": True
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_entity_content",
                            "description": "Retrieve the source code for an entity using its citation",
                            "args_schema": {
                                "entity_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the entity"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "impact_analysis",
                            "description": "Analyze what entities would be impacted by changes (downstream) or dependencies (upstream). Works across all sources.",
                            "args_schema": {
                                "entity_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the entity to analyze"
                                },
                                "direction": {
                                    "type": "String",
                                    "required": False,
                                    "description": "'downstream' (what depends on this) or 'upstream' (what this depends on)",
                                    "default": "downstream"
                                },
                                "max_depth": {
                                    "type": "Integer",
                                    "required": False,
                                    "description": "Maximum traversal depth",
                                    "default": 3
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_related_entities",
                            "description": "Get entities related to a specific entity, optionally filtered by relation type",
                            "args_schema": {
                                "entity_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the entity"
                                },
                                "relation_type": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by relation type (CALLS, IMPORTS, EXTENDS, etc.)"
                                },
                                "direction": {
                                    "type": "String",
                                    "required": False,
                                    "description": "'outgoing', 'incoming', or 'both'",
                                    "default": "both"
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_cross_source_relations",
                            "description": "Get relations that connect entities from different data sources (e.g., Jira ticket -> GitHub PR)",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_stats",
                            "description": "Get statistics about the knowledge graph (entity counts by type, source, layer)",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "list_entities_by_type",
                            "description": "List all entities of a specific type across all sources",
                            "args_schema": {
                                "entity_type": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Type of entities to list (class, function, service, etc.)"
                                },
                                "source_toolkit": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by source toolkit (e.g., 'github', 'ado', or toolkit ID)"
                                },
                                "limit": {
                                    "type": "Integer",
                                    "required": False,
                                    "description": "Maximum number of entities",
                                    "default": 50
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "list_entities_by_layer",
                            "description": "List entities by semantic layer (code, service, data, product, domain, etc.)",
                            "args_schema": {
                                "layer": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Layer to filter by"
                                },
                                "source_toolkit": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by source toolkit (e.g., 'github', 'ado', or toolkit ID)"
                                },
                                "limit": {
                                    "type": "Integer",
                                    "required": False,
                                    "description": "Maximum number of entities",
                                    "default": 50
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "list_entities_by_source",
                            "description": "List all entities from a specific source toolkit",
                            "args_schema": {
                                "source_toolkit": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Source toolkit identifier (e.g., 'github', 'ado', or toolkit ID)"
                                },
                                "entity_type": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Filter by entity type"
                                },
                                "limit": {
                                    "type": "Integer",
                                    "required": False,
                                    "description": "Maximum number of entities",
                                    "default": 50
                                },
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        # ========== Preset Management ==========
                        {
                            "name": "list_presets",
                            "description": "List available language presets for ingestion (python, typescript, java, etc.)",
                            "args_schema": {},
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "get_preset_info",
                            "description": "Get details about a specific language preset including file patterns",
                            "args_schema": {
                                "preset_name": {
                                    "type": "String",
                                    "required": True,
                                    "description": "Name of the preset (python, typescript, java, etc.)"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        # ========== Cache Management ==========
                        {
                            "name": "get_cache_stats",
                            "description": "Get statistics about the local graph cache including size, count, and individual graph details",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        {
                            "name": "cleanup_cache",
                            "description": "Force cleanup of stale graphs from the cache (normally runs automatically in background)",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "text"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        },
                        # ========== Source Status ==========
                        {
                            "name": "get_sources_status",
                            "description": "Get status of all sources added to the knowledge graph. Shows ingestion status (pending/in_progress/completed/error), last update time, and entity counts for each source.",
                            "args_schema": {
                                "output_format": {
                                    "type": "String",
                                    "required": False,
                                    "description": "Output format: 'text' or 'json'",
                                    "default": "json"
                                }
                            },
                            "tool_result_type": "String",
                            "sync_invocation_supported": True,
                            "async_invocation_supported": True
                        }
                    ],
                    "toolkit_metadata": {
                        "application": True,
                        "interface": {
                            "type": "iframe",
                            "create_url": None,
                            "app_url": "/app/ui_host/inventory/ui/{project_id}/{toolkit_id}?theme={theme}"
                        }
                    }
                }
            ]
        }
