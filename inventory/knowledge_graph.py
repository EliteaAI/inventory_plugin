"""
NetworkX-based Knowledge Graph implementation.

Provides lightweight in-memory graph storage with JSON persistence.
Entities contain citations (source file, line numbers) instead of raw content.
Raw data should be retrieved on-demand using filesystem tools.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional, List, Dict, Set
from collections import defaultdict

try:
    import networkx as nx
    from networkx import DiGraph
except ImportError:
    nx = None

# Import type normalization constants from central location
# Handle both relative import (when used as subpackage) and direct import contexts
try:
    from ..constants import (
        TYPE_NORMALIZATION_MAP,
        TYPE_PRIORITY,
        NEVER_DEDUPLICATE_TYPES,
        KNOWN_TYPE_PREFIXES,
        KNOWN_TYPE_SUFFIXES,
        TYPE_SUFFIX_NORMALIZATION,
    )
except ImportError:
    from plugins.inventory_plugin.constants import (
        TYPE_NORMALIZATION_MAP,
        TYPE_PRIORITY,
        NEVER_DEDUPLICATE_TYPES,
        KNOWN_TYPE_PREFIXES,
        KNOWN_TYPE_SUFFIXES,
        TYPE_SUFFIX_NORMALIZATION,
    )

logger = logging.getLogger(__name__)


def _normalize_entity_type(entity_type: str) -> str:
    """
    Normalize entity type to canonical lowercase form.

    Handles many LLM-generated type variations:
    - Explicit mappings from TYPE_NORMALIZATION_MAP
    - Slash/comma/colon-separated composite types (picks highest priority part)
    - Triple-underscore separators (api_contract___parameter)
    - Trailing underscore before slash (api_contract_/integration)
    - '_or_' patterns (api_contract_or_integration_point)
    - Parenthetical suffixes (api_contract(parameter))
    - Joined words without underscores (businessrule → business_rule)
    - Plural forms
    """
    if not entity_type:
        return "unknown"

    # Check explicit mapping first (before any processing)
    if entity_type in TYPE_NORMALIZATION_MAP:
        return TYPE_NORMALIZATION_MAP[entity_type]

    # Pre-cleanup: normalize separators
    normalized = entity_type.lower().strip()
    normalized = normalized.replace(" ", "_").replace("-", "_")
    normalized = normalized.replace("_/", "/")      # api_contract_/integration → api_contract/integration
    normalized = normalized.replace("___", "/")     # api_contract___parameter → api_contract/parameter
    normalized = normalized.replace("::", "/")      # documentation::guide → documentation/guide
    if ":" in normalized and "/" not in normalized:
        normalized = normalized.replace(":", "/")   # documentation:guide → documentation/guide

    # Remove duplicate underscores
    while "__" in normalized:
        normalized = normalized.replace("__", "_")

    # Strip leading/trailing underscores
    normalized = normalized.strip("_")

    # Check mapping after cleanup
    if normalized in TYPE_NORMALIZATION_MAP:
        return TYPE_NORMALIZATION_MAP[normalized]

    # Handle parenthetical suffixes: api_contract(parameter) → extract base or pick best
    if "(" in normalized and normalized.endswith(")"):
        base = normalized.split("(")[0].strip("_")
        inner = normalized.split("(")[1].rstrip(")").strip("_")
        if base and inner:
            # Treat as composite, pick higher priority
            parts = [base, inner]
            normalized = _pick_best_type_part(parts)
        elif base:
            normalized = base

    # Handle '_or_' patterns: api_contract_or_integration_point → pick first part
    if "_or_" in normalized:
        parts = normalized.split("_or_")
        normalized = _pick_best_type_part(parts)

    # Handle comma-separated: workflows,processes,procedures → pick best
    if "," in normalized:
        parts = [p.strip().strip("_") for p in normalized.split(",")]
        parts = [p for p in parts if p]
        if parts:
            normalized = _pick_best_type_part(parts)

    # Handle slash-separated composite types (most common)
    if "/" in normalized:
        parts = [p.strip().strip("_") for p in normalized.split("/")]
        parts = [p for p in parts if p]
        if parts:
            normalized = _pick_best_type_part(parts)
        else:
            return "unknown"

    # Try to split joined words (businessrule → business_rule)
    normalized = _insert_word_boundaries(normalized)

    # Check mapping again after all transformations
    if normalized in TYPE_NORMALIZATION_MAP:
        return TYPE_NORMALIZATION_MAP[normalized]

    # Handle plural forms by removing trailing 's' (but not 'ss' like 'class')
    if normalized.endswith('s') and not normalized.endswith('ss') and len(normalized) > 3:
        singular = normalized[:-1]
        if singular in TYPE_NORMALIZATION_MAP:
            return TYPE_NORMALIZATION_MAP[singular]
        # Check if singular form is in the map values (canonical forms)
        if singular in set(TYPE_NORMALIZATION_MAP.values()):
            return singular

    # Apply suffix-based normalization for compound types like "validation_rule" → "rule"
    # Check longest suffixes first to avoid partial matches
    for suffix in sorted(TYPE_SUFFIX_NORMALIZATION.keys(), key=len, reverse=True):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            # Make sure we're not just matching the suffix alone
            prefix = normalized[:-len(suffix)]
            if prefix and prefix != "_":
                return TYPE_SUFFIX_NORMALIZATION[suffix]

    return normalized


def _pick_best_type_part(parts: list) -> str:
    """Pick the part with highest TYPE_PRIORITY from a list of type parts."""
    if not parts:
        return "unknown"
    if len(parts) == 1:
        return parts[0]

    def get_priority(part):
        # Try to normalize the part first
        p = part.lower().strip().strip("_")
        # Insert word boundaries for joined words
        p = _insert_word_boundaries(p)
        # Handle plurals
        if p.endswith('s') and not p.endswith('ss') and len(p) > 3:
            singular = p[:-1]
            if singular in TYPE_NORMALIZATION_MAP or singular in TYPE_PRIORITY:
                p = singular
        mapped = TYPE_NORMALIZATION_MAP.get(p, p)
        return TYPE_PRIORITY.get(mapped, 0)

    best = max(parts, key=get_priority)
    # Return the normalized form of the best part
    p = best.lower().strip().strip("_")
    p = _insert_word_boundaries(p)
    if p.endswith('s') and not p.endswith('ss') and len(p) > 3:
        singular = p[:-1]
        if singular in TYPE_NORMALIZATION_MAP or singular in TYPE_PRIORITY:
            p = singular
    return p


def _insert_word_boundaries(type_str: str) -> str:
    """
    Insert underscores at word boundaries for joined words.
    E.g., 'businessrule' → 'business_rule', 'domainconcept' → 'domain_concept'
    Only splits if both prefix AND suffix are recognized words.
    """
    if "_" in type_str:
        # Already has underscores, don't modify
        return type_str

    result = type_str
    for prefix in KNOWN_TYPE_PREFIXES:
        if result.startswith(prefix) and len(result) > len(prefix):
            suffix = result[len(prefix):]
            if suffix and suffix[0] != "_":
                # Only split if suffix starts with a known suffix word
                # or is itself a known type
                suffix_valid = any(suffix.startswith(s) for s in KNOWN_TYPE_SUFFIXES)
                suffix_valid = suffix_valid or suffix in TYPE_NORMALIZATION_MAP
                suffix_valid = suffix_valid or suffix in TYPE_PRIORITY
                if suffix_valid:
                    result = prefix + "_" + suffix
                    break  # Only split at first match

    return result


class Citation:
    """
    Represents a source citation for an entity.
    
    Citations are lightweight references to source files and line ranges.
    The actual content should be retrieved on-demand using filesystem tools.
    """
    
    def __init__(
        self,
        file_path: str,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        source_toolkit: Optional[str] = None,
        doc_id: Optional[str] = None,
        content_hash: Optional[str] = None,
    ):
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.source_toolkit = source_toolkit
        self.doc_id = doc_id
        self.content_hash = content_hash
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert citation to dictionary."""
        return {
            'file_path': self.file_path,
            'line_start': self.line_start,
            'line_end': self.line_end,
            'source_toolkit': self.source_toolkit,
            'doc_id': self.doc_id,
            'content_hash': self.content_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Citation':
        """Create citation from dictionary."""
        return cls(
            file_path=data.get('file_path', ''),
            line_start=data.get('line_start'),
            line_end=data.get('line_end'),
            source_toolkit=data.get('source_toolkit'),
            doc_id=data.get('doc_id'),
            content_hash=data.get('content_hash'),
        )
    
    def __repr__(self) -> str:
        if self.line_start and self.line_end:
            return f"{self.file_path}:{self.line_start}-{self.line_end}"
        elif self.line_start:
            return f"{self.file_path}:{self.line_start}"
        return self.file_path


class KnowledgeGraph:
    """
    Lightweight NetworkX-based knowledge graph for storing entities and relationships.
    
    Design principles:
    - Graph contains only entity metadata and citations (not raw content)
    - Citations reference source files and line numbers
    - Raw content is retrieved on-demand via filesystem tools
    - Graph file stays small and portable
    
    Features:
    - In-memory property graph using NetworkX
    - JSON persistence via node_link_data format
    - Delta update support with source document tracking
    - Entity deduplication with merge strategies
    - Impact analysis via graph traversal
    - Enhanced search with fuzzy matching, token-based search, and file path patterns
    """
    
    # Layer classification based on entity types
    LAYER_TYPE_MAPPING = {
        'code': {
            'class', 'function', 'method', 'module', 'import', 'variable', 
            'constant', 'attribute', 'decorator', 'exception', 'enum',
            'class_reference', 'class_import', 'function_import', 'function_reference',
            'function_call', 'method_call', 'test_function', 'pydanticmodel'
        },
        'service': {
            'api_endpoint', 'rpc_method', 'route', 'service', 'handler',
            'controller', 'middleware', 'event', 'sio', 'rpc'
        },
        'data': {
            'model', 'schema', 'field', 'table', 'database', 'migration',
            'entity', 'pydantic_model', 'dictionary', 'list', 'object'
        },
        'product': {
            'feature', 'capability', 'platform', 'product', 'application',
            'menu', 'ui_element', 'ui_component', 'interface_element'
        },
        'domain': {
            'concept', 'process', 'action', 'use_case', 'workflow',
            'requirement', 'guideline', 'best_practice'
        },
        'documentation': {
            'document', 'guide', 'section', 'subsection', 'tip',
            'example', 'resource', 'reference', 'documentation'
        },
        'configuration': {
            'configuration', 'configuration_option', 'configuration_section',
            'setting', 'credential', 'secret', 'integration'
        },
        'testing': {
            'test', 'test_case', 'test_function', 'fixture', 'mock'
        },
        'tooling': {
            'tool', 'toolkit', 'command', 'node_type', 'node'
        },
        'knowledge': {
            # Facts extracted from code and documentation
            'fact',
            # Code-specific fact types
            'algorithm', 'behavior', 'validation', 'dependency', 'error_handling',
            # Text-specific fact types
            'decision', 'definition', 'date', 'contact',
        },
        'structure': {
            # File-level container nodes
            'file', 'source_file', 'document_file', 'config_file', 'web_file',
            # Directory/package structure
            'directory', 'package',
        }
    }
    
    # Reverse mapping: type -> layer
    TYPE_TO_LAYER = {}
    for layer, types in LAYER_TYPE_MAPPING.items():
        for t in types:
            TYPE_TO_LAYER[t] = layer
    
    def __init__(self):
        """Initialize an empty knowledge graph."""
        if nx is None:
            raise ImportError("networkx is required for KnowledgeGraph. Install with: pip install networkx>=3.0")
        
        self._graph: DiGraph = DiGraph()
        self._entity_index: Dict[str, Set[str]] = defaultdict(set)  # name -> set of node_ids (handles duplicates)
        self._type_index: Dict[str, Set[str]] = defaultdict(set)  # type (lowercase) -> node_ids
        self._file_index: Dict[str, Set[str]] = defaultdict(set)  # file_path -> node_ids
        self._source_doc_index: Dict[str, Set[str]] = defaultdict(set)  # source_doc_id -> node_ids
        self._metadata: Dict[str, Any] = {}  # Graph metadata (sources, timestamps)
        self._schema: Optional[Dict[str, Any]] = None  # Discovered entity schema
        self._embedding_model: Any = None  # LangChain Embeddings instance (set by generate_embeddings)
        self._community_index: Dict[str, List[str]] = {}  # community_id -> [node_ids]
    
    # ========== Entity Operations ==========
    
    def add_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: str,
        citation: Optional[Citation] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Add an entity to the graph with optional citation.
        
        If an entity with this ID already exists, the citation is merged
        into the existing entity's citations list (enabling same-named
        entities from different files to be unified).
        
        Args:
            entity_id: Unique identifier for the entity
            name: Human-readable entity name
            entity_type: Type classification (e.g., 'Class', 'Function', 'Service')
            citation: Source citation (file path, line numbers)
            properties: Additional properties (no raw content, only metadata)
            
        Returns:
            The entity_id (node ID in graph)
        """
        # Normalize entity type to canonical lowercase form
        # This ensures consistent type handling regardless of source variations
        entity_type = _normalize_entity_type(entity_type)

        # Check if entity already exists (for merging citations)
        existing = self._graph.nodes.get(entity_id)
        
        if existing:
            # Entity exists - merge the new citation
            if citation:
                new_citation_dict = citation.to_dict()
                existing_citations = existing.get('citations', [])
                
                # Migrate legacy single 'citation' to list
                if 'citation' in existing and existing['citation']:
                    legacy = existing['citation']
                    if legacy not in existing_citations:
                        existing_citations.append(legacy)
                
                # Add new citation if not duplicate
                if new_citation_dict not in existing_citations:
                    existing_citations.append(new_citation_dict)
                
                # Update node with merged citations
                self._graph.nodes[entity_id]['citations'] = existing_citations
                self._graph.nodes[entity_id].pop('citation', None)  # Remove legacy field
                
                # Track source document
                if citation.doc_id:
                    self._source_doc_index[citation.doc_id].add(entity_id)
            
            logger.debug(f"Merged citation into existing entity: {entity_type} '{name}' ({entity_id})")
            return entity_id
        
        # New entity - prepare node data
        node_data = {
            'id': entity_id,
            'name': name,
            'type': entity_type,
        }
        
        # Auto-assign layer based on entity type
        inferred_layer = self.TYPE_TO_LAYER.get(entity_type.lower())
        if inferred_layer:
            node_data['layer'] = inferred_layer
        
        # Store citation in list format from the start
        if citation:
            node_data['citations'] = [citation.to_dict()]
            # Track source document
            if citation.doc_id:
                self._source_doc_index[citation.doc_id].add(entity_id)
            # Track file index
            if citation.file_path:
                self._file_index[citation.file_path].add(entity_id)
        
        # Add other properties (excluding any large content)
        if properties:
            # Filter out raw content fields
            excluded_keys = {'content', 'text', 'raw', 'body', 'source_content'}
            for key, value in properties.items():
                if key not in excluded_keys:
                    # Only store if serializable and reasonably sized
                    if isinstance(value, (str, int, float, bool, list, dict)) and \
                       (not isinstance(value, str) or len(value) < 1000):
                        node_data[key] = value
        
        # Add new node
        self._graph.add_node(entity_id, **node_data)
        
        # Update indices - store ALL entities with this name (not just one)
        self._entity_index[name.lower()].add(entity_id)
        self._type_index[entity_type.lower()].add(entity_id)
        
        logger.debug(f"Added entity: {entity_type} '{name}' ({entity_id})")
        return entity_id
    
    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get entity by ID."""
        if self._graph.has_node(entity_id):
            data = dict(self._graph.nodes[entity_id])
            data['id'] = entity_id  # NetworkX uses ID as node key, add it to data
            return data
        return None
    
    def find_entity_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Find entity by name (case-insensitive).
        
        If multiple entities have the same name, returns the first one found.
        Use find_all_entities_by_name to get all matches.
        """
        node_ids = self._entity_index.get(name.lower(), set())
        if node_ids:
            # Return first match
            return self.get_entity(next(iter(node_ids)))
        return None
    
    def find_all_entities_by_name(self, name: str) -> List[Dict[str, Any]]:
        """
        Find all entities with the given name (case-insensitive).
        
        Returns all entities if multiple have the same name but different types.
        """
        node_ids = self._entity_index.get(name.lower(), set())
        return [self.get_entity(nid) for nid in node_ids if nid]
    
    def get_entities_by_type(self, entity_type: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all entities of a specific type (case-insensitive).
        
        Also checks layer-based type groups. For example, searching for 'code'
        will return classes, functions, methods, etc.
        """
        entity_type_lower = entity_type.lower()
        
        # Check if this is a layer name
        if entity_type_lower in self.LAYER_TYPE_MAPPING:
            # Get all types in this layer
            results = []
            for t in self.LAYER_TYPE_MAPPING[entity_type_lower]:
                node_ids = self._type_index.get(t, set())
                for nid in node_ids:
                    entity = self.get_entity(nid)
                    if entity:
                        results.append(entity)
            if limit:
                return results[:limit]
            return results
        
        # Use type index for fast lookup
        node_ids = self._type_index.get(entity_type_lower, set())
        if node_ids:
            results = [self.get_entity(nid) for nid in node_ids if nid]
            if limit:
                return results[:limit]
            return results
        
        # Fallback: linear scan (for types not in index)
        results = [
            dict(data)
            for _, data in self._graph.nodes(data=True)
            if data.get('type', '').lower() == entity_type_lower
        ]
        if limit:
            return results[:limit]
        return results
    
    def get_entities_by_layer(self, layer: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all entities in a specific layer (product, domain, service, code, data, etc.).
        
        Layer is inferred from entity type if not explicitly set on the entity.
        """
        layer_lower = layer.lower()
        
        # Get types that belong to this layer
        layer_types = self.LAYER_TYPE_MAPPING.get(layer_lower, set())
        
        results = []
        for _, data in self._graph.nodes(data=True):
            # Check explicit layer
            if data.get('layer', '').lower() == layer_lower:
                results.append(dict(data))
                continue
            
            # Check if type belongs to this layer
            entity_type = data.get('type', '').lower()
            if entity_type in layer_types:
                results.append(dict(data))
        
        if limit:
            return results[:limit]
        return results
    
    def get_all_entities(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all entities in the graph."""
        results = [
            {'id': node_id, **dict(data)}
            for node_id, data in self._graph.nodes(data=True)
        ]
        if limit:
            return results[:limit]
        return results
    
    def get_all_entity_types(self) -> List[str]:
        """Get list of all entity types in the graph."""
        types = set()
        for _, data in self._graph.nodes(data=True):
            if 'type' in data:
                types.add(data['type'])
        return sorted(types)
    
    def update_entity(self, entity_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update entity properties.
        
        Args:
            entity_id: Entity to update
            updates: Properties to update (merged with existing)
            
        Returns:
            True if entity exists and was updated
        """
        if not self._graph.has_node(entity_id):
            return False
        
        # Filter out raw content
        excluded_keys = {'content', 'text', 'raw', 'body', 'source_content'}
        filtered_updates = {
            k: v for k, v in updates.items()
            if k not in excluded_keys
        }
        
        current = dict(self._graph.nodes[entity_id])
        current.update(filtered_updates)
        
        for key, value in current.items():
            self._graph.nodes[entity_id][key] = value
        
        return True
    
    def remove_entity(self, entity_id: str) -> bool:
        """Remove entity and its edges from the graph."""
        if not self._graph.has_node(entity_id):
            return False
        
        # Remove from all indices
        entity = self.get_entity(entity_id)
        if entity:
            # Remove from name index
            name = entity.get('name', '').lower()
            if name in self._entity_index:
                self._entity_index[name].discard(entity_id)
                if not self._entity_index[name]:
                    del self._entity_index[name]
            
            # Remove from type index
            entity_type = entity.get('type', '').lower()
            if entity_type in self._type_index:
                self._type_index[entity_type].discard(entity_id)
                if not self._type_index[entity_type]:
                    del self._type_index[entity_type]
            
            # Remove from file index
            file_path = entity.get('file_path', '')
            if file_path in self._file_index:
                self._file_index[file_path].discard(entity_id)
                if not self._file_index[file_path]:
                    del self._file_index[file_path]
            
            # Remove from source doc index
            for citation in entity.get('citations', []):
                if isinstance(citation, dict):
                    doc_id = citation.get('doc_id')
                    if doc_id and entity_id in self._source_doc_index.get(doc_id, set()):
                        self._source_doc_index[doc_id].discard(entity_id)
        
        self._graph.remove_node(entity_id)
        return True
    
    # ========== Relation Operations ==========
    
    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Add a directed relation between entities.
        
        Args:
            source_id: Source entity ID
            target_id: Target entity ID
            relation_type: Type of relationship (e.g., 'CALLS', 'IMPORTS', 'INHERITS')
            properties: Additional edge properties
            
        Returns:
            True if relation was added
        """
        if not self._graph.has_node(source_id):
            logger.warning(f"Source entity {source_id} not found")
            return False
        if not self._graph.has_node(target_id):
            logger.warning(f"Target entity {target_id} not found")
            return False
        
        edge_data = {'relation_type': relation_type.lower()}
        if properties:
            edge_data.update(properties)
        
        self._graph.add_edge(source_id, target_id, **edge_data)
        logger.debug(f"Added relation: {source_id} --[{relation_type}]--> {target_id}")
        return True
    
    def get_relations(self, entity_id: str, direction: str = 'both') -> List[Dict[str, Any]]:
        """
        Get relations for an entity.
        
        Args:
            entity_id: Entity ID
            direction: 'outgoing', 'incoming', or 'both'
            
        Returns:
            List of relation dicts with source, target, type, properties
        """
        relations = []
        
        if direction in ('outgoing', 'both'):
            for _, target, data in self._graph.out_edges(entity_id, data=True):
                relations.append({
                    'source': entity_id,
                    'target': target,
                    'relation_type': data.get('relation_type'),
                    'properties': {k: v for k, v in data.items() if k != 'relation_type'}
                })
        
        if direction in ('incoming', 'both'):
            for source, _, data in self._graph.in_edges(entity_id, data=True):
                relations.append({
                    'source': source,
                    'target': entity_id,
                    'relation_type': data.get('relation_type'),
                    'properties': {k: v for k, v in data.items() if k != 'relation_type'}
                })
        
        return relations
    
    def remove_relation(self, source_id: str, target_id: str) -> bool:
        """Remove a relation between entities."""
        if self._graph.has_edge(source_id, target_id):
            self._graph.remove_edge(source_id, target_id)
            return True
        return False
    
    def get_relations_by_source(
        self, 
        source_toolkit: str,
        relation_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all relations from a specific source toolkit.
        
        Args:
            source_toolkit: Name of source toolkit (e.g., 'github', 'jira')
            relation_type: Optional filter by relation type
            
        Returns:
            List of relations with their properties
        """
        relations = []
        
        for source, target, data in self._graph.edges(data=True):
            # Check if this relation is from the specified source
            rel_source = data.get('source_toolkit')
            if rel_source == source_toolkit:
                # Filter by relation type if specified
                if relation_type is None or data.get('relation_type') == relation_type:
                    relations.append({
                        'source': source,
                        'target': target,
                        'relation_type': data.get('relation_type'),
                        'source_toolkit': rel_source,
                        'properties': {k: v for k, v in data.items() 
                                     if k not in ('relation_type', 'source_toolkit')}
                    })
        
        return relations
    
    def get_cross_source_relations(self) -> List[Dict[str, Any]]:
        """
        Get relations that connect entities from different sources.
        
        These are particularly valuable for understanding how different
        data sources relate to each other (e.g., Jira ticket references GitHub PR).
        
        Returns:
            List of cross-source relations
        """
        cross_source = []
        
        for source, target, data in self._graph.edges(data=True):
            source_node = self._graph.nodes.get(source, {})
            target_node = self._graph.nodes.get(target, {})
            
            # Get source toolkits from entity citations
            source_citations = source_node.get('citations', [])
            target_citations = target_node.get('citations', [])
            
            if not source_citations or not target_citations:
                continue
            
            # Get unique source toolkits for each entity
            source_toolkits = set()
            target_toolkits = set()
            
            for citation in source_citations:
                if isinstance(citation, dict):
                    toolkit = citation.get('source_toolkit')
                elif hasattr(citation, 'source_toolkit'):
                    toolkit = citation.source_toolkit
                else:
                    toolkit = None
                if toolkit:
                    source_toolkits.add(toolkit)
            
            for citation in target_citations:
                if isinstance(citation, dict):
                    toolkit = citation.get('source_toolkit')
                elif hasattr(citation, 'source_toolkit'):
                    toolkit = citation.source_toolkit
                else:
                    toolkit = None
                if toolkit:
                    target_toolkits.add(toolkit)
            
            # Check if entities come from different sources
            if source_toolkits and target_toolkits and source_toolkits != target_toolkits:
                cross_source.append({
                    'source': source,
                    'target': target,
                    'source_toolkits': list(source_toolkits),
                    'target_toolkits': list(target_toolkits),
                    'relation_type': data.get('relation_type'),
                    'relation_source': data.get('source_toolkit'),
                    'properties': {k: v for k, v in data.items() 
                                 if k not in ('relation_type', 'source_toolkit')}
                })
        
        return cross_source
    
    # ========== Graph Analysis ==========
    
    def get_neighbors(
        self,
        entity_id: str,
        max_depth: int = 1,
        relation_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get neighboring entities up to a certain depth.
        
        Args:
            entity_id: Starting entity
            max_depth: How many hops to traverse
            relation_types: Filter by relation types
            
        Returns:
            Dict with entities and relations
        """
        if not self._graph.has_node(entity_id):
            return {'entities': [], 'relations': []}
        
        visited = {entity_id}
        entities = [self.get_entity(entity_id)]
        relations = []
        
        current_level = [entity_id]
        
        for _ in range(max_depth):
            next_level = []
            
            for node in current_level:
                # Outgoing edges
                for _, target, data in self._graph.out_edges(node, data=True):
                    rel_type = data.get('relation_type')
                    if relation_types and rel_type not in relation_types:
                        continue
                    
                    relations.append({
                        'source': node,
                        'target': target,
                        'relation_type': rel_type,
                    })
                    
                    if target not in visited:
                        visited.add(target)
                        next_level.append(target)
                        entities.append(self.get_entity(target))
                
                # Incoming edges
                for source, _, data in self._graph.in_edges(node, data=True):
                    rel_type = data.get('relation_type')
                    if relation_types and rel_type not in relation_types:
                        continue
                    
                    relations.append({
                        'source': source,
                        'target': node,
                        'relation_type': rel_type,
                    })
                    
                    if source not in visited:
                        visited.add(source)
                        next_level.append(source)
                        entities.append(self.get_entity(source))
            
            current_level = next_level
        
        return {'entities': entities, 'relations': relations}
    
    def find_path(self, source_id: str, target_id: str) -> Optional[List[str]]:
        """Find shortest path between two entities."""
        if not self._graph.has_node(source_id) or not self._graph.has_node(target_id):
            return None
        
        try:
            path = nx.shortest_path(self._graph, source_id, target_id)
            return path
        except nx.NetworkXNoPath:
            return None
    
    def impact_analysis(
        self,
        entity_id: str,
        direction: str = 'downstream',
        max_depth: int = 3,
    ) -> Dict[str, Any]:
        """
        Analyze impact of changes to an entity.
        
        Args:
            entity_id: Entity to analyze
            direction: 'downstream' (what depends on this) or 'upstream' (what this depends on)
            max_depth: Maximum traversal depth
            
        Returns:
            Dict with impacted entities and paths
        """
        if not self._graph.has_node(entity_id):
            return {'impacted': [], 'paths': []}
        
        impacted = []
        paths = []
        
        # Use BFS for level-by-level analysis
        visited = {entity_id}
        queue = [(entity_id, [entity_id], 0)]
        
        while queue:
            current, path, depth = queue.pop(0)
            
            if depth >= max_depth:
                continue
            
            # Get edges based on direction
            if direction == 'downstream':
                edges = self._graph.in_edges(current, data=True)
            else:  # upstream
                edges = self._graph.out_edges(current, data=True)
            
            for edge in edges:
                if direction == 'downstream':
                    neighbor = edge[0]
                else:
                    neighbor = edge[1]
                
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = path + [neighbor]
                    
                    entity = self.get_entity(neighbor)
                    impacted.append({
                        'entity': entity,
                        'depth': depth + 1,
                        'path': new_path,
                    })
                    paths.append(new_path)
                    
                    queue.append((neighbor, new_path, depth + 1))
        
        return {'impacted': impacted, 'paths': paths}
    
    # ========== Search Operations ==========
    
    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize text into searchable tokens (handles camelCase, snake_case, etc.)."""
        import re
        if not text:
            return set()
        
        # Split on non-alphanumeric
        words = re.split(r'[^a-zA-Z0-9]+', text.lower())
        
        # Also split camelCase
        tokens = set()
        for word in words:
            if word:
                tokens.add(word)
                # Split camelCase: "ChatMessageHandler" -> ["chat", "message", "handler"]
                camel_parts = re.findall(r'[a-z]+|[A-Z][a-z]*|[0-9]+', word)
                tokens.update(p.lower() for p in camel_parts if p)
        
        return tokens
    
    def _calculate_match_score(
        self,
        query_tokens: Set[str],
        query_lower: str,
        name: str,
        entity_type: str,
        description: str,
        file_path: str,
    ) -> tuple:
        """
        Calculate match score for an entity.
        
        Returns (score, match_field) tuple.
        Higher scores mean better matches.
        """
        name_lower = name.lower()
        name_tokens = self._tokenize(name)
        
        # Exact name match (highest priority)
        if query_lower == name_lower:
            return (1.0, 'name_exact')
        
        # Exact substring in name
        if query_lower in name_lower:
            # Prefer matches at word boundaries
            score = 0.85 if name_lower.startswith(query_lower) else 0.75
            return (score, 'name_contains')
        
        # Token overlap in name (for camelCase matching)
        if query_tokens and name_tokens:
            overlap = len(query_tokens & name_tokens)
            if overlap > 0:
                # Score based on percentage of query tokens matched
                score = 0.6 * (overlap / len(query_tokens))
                if overlap == len(query_tokens):  # All query tokens found
                    score = 0.7
                return (score, 'name_tokens')
        
        # Check file path
        if file_path and query_lower in file_path.lower():
            return (0.55, 'file_path')
        
        # Check description
        if description:
            desc_lower = description.lower()
            if query_lower in desc_lower:
                return (0.5, 'description')
            # Token match in description
            desc_tokens = self._tokenize(description)
            if query_tokens and desc_tokens:
                overlap = len(query_tokens & desc_tokens)
                if overlap > 0:
                    score = 0.35 * (overlap / len(query_tokens))
                    return (score, 'description_tokens')
        
        # Check entity type
        if query_lower in entity_type.lower():
            return (0.3, 'type')
        
        return (0.0, None)
    
    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
        layer: Optional[str] = None,
        file_pattern: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search entities with enhanced matching capabilities.
        
        Supports:
        - Exact and partial name matching
        - Token-based matching (handles camelCase, snake_case)
        - Description and property search
        - File path pattern matching
        - Type and layer filtering
        
        Args:
            query: Search query string
            top_k: Maximum results to return
            entity_type: Filter by entity type (case-insensitive)
            layer: Filter by layer (code, service, data, product, etc.)
            file_pattern: Filter by file path pattern (glob-like)
            
        Returns:
            List of matching entities with scores
        """
        import re
        
        results = []
        query_lower = query.lower().strip()
        query_tokens = self._tokenize(query)
        
        # Get layer types for filtering
        layer_types = set()
        if layer:
            layer_types = self.LAYER_TYPE_MAPPING.get(layer.lower(), set())
        
        # Compile file pattern if provided
        file_regex = None
        if file_pattern:
            # Convert glob pattern to regex
            pattern = file_pattern.replace('.', r'\.').replace('*', '.*').replace('?', '.')
            try:
                file_regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                pass
        
        for node_id, data in self._graph.nodes(data=True):
            # Type filter (case-insensitive)
            data_type = data.get('type', '').lower()
            if entity_type and data_type != entity_type.lower():
                continue
            
            # Layer filter
            if layer:
                entity_layer = data.get('layer', '').lower()
                if entity_layer != layer.lower() and data_type not in layer_types:
                    continue
            
            # File pattern filter
            citations = data.get('citations', [])
            if not citations and 'citation' in data:
                citations = [data['citation']]
            
            file_paths = [c.get('file_path', '') for c in citations if isinstance(c, dict)]
            primary_file = file_paths[0] if file_paths else data.get('file_path', '')
            
            if file_regex and primary_file:
                if not file_regex.search(primary_file):
                    continue
            
            # Calculate match score
            name = data.get('name', '')
            description = data.get('description', '')
            if isinstance(data.get('properties'), dict):
                description = description or data['properties'].get('description', '')
            
            score, match_field = self._calculate_match_score(
                query_tokens, query_lower, name, data_type, description, primary_file
            )
            
            if score > 0:
                # Include node_id in entity data (needed for graph operations)
                entity_data = dict(data)
                entity_data['id'] = node_id
                results.append({
                    'entity': entity_data,
                    'score': score,
                    'match_field': match_field,
                })
        
        # Sort by score (descending), then by name
        results.sort(key=lambda x: (-x['score'], x['entity'].get('name', '').lower()))
        return results[:top_k]
    
    def search_by_file(self, file_path_pattern: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Search entities by file path pattern.
        
        Args:
            file_path_pattern: Glob-like pattern (e.g., "api/*.py", "**/chat*.py")
            limit: Maximum results
            
        Returns:
            List of entities from matching files
        """
        import re
        
        # Convert glob to regex
        pattern = file_path_pattern.replace('.', r'\.').replace('**', '.*').replace('*', '[^/]*').replace('?', '.')
        try:
            file_regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        
        results = []
        for file_path, node_ids in self._file_index.items():
            if file_regex.search(file_path):
                for nid in node_ids:
                    entity = self.get_entity(nid)
                    if entity:
                        results.append(entity)
                        if len(results) >= limit:
                            return results
        
        # Also check entities with file_path attribute (backup)
        if not results:
            for _, data in self._graph.nodes(data=True):
                fp = data.get('file_path', '')
                if fp and file_regex.search(fp):
                    results.append(dict(data))
                    if len(results) >= limit:
                        break
        
        return results
    
    def search_advanced(
        self,
        query: Optional[str] = None,
        entity_types: Optional[List[str]] = None,
        layers: Optional[List[str]] = None,
        file_patterns: Optional[List[str]] = None,
        has_relations: Optional[bool] = None,
        min_citations: Optional[int] = None,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Advanced search with multiple filter criteria.
        
        Args:
            query: Text search query (optional)
            entity_types: List of types to include (OR logic)
            layers: List of layers to include (OR logic)
            file_patterns: List of file patterns to include (OR logic)
            has_relations: If True, only entities with relations; if False, isolated entities
            min_citations: Minimum number of citations required
            top_k: Maximum results
            
        Returns:
            List of matching entities
        """
        import re
        
        # Build type filter set
        type_filter = set()
        if entity_types:
            for t in entity_types:
                type_filter.add(t.lower())
                # Expand layer names to types
                if t.lower() in self.LAYER_TYPE_MAPPING:
                    type_filter.update(self.LAYER_TYPE_MAPPING[t.lower()])
        
        # Build layer filter set
        layer_filter = set()
        if layers:
            for l in layers:
                layer_filter.add(l.lower())
        
        # Build file regex patterns
        file_regexes = []
        if file_patterns:
            for fp in file_patterns:
                pattern = fp.replace('.', r'\.').replace('**', '.*').replace('*', '[^/]*')
                try:
                    file_regexes.append(re.compile(pattern, re.IGNORECASE))
                except re.error:
                    pass
        
        query_tokens = self._tokenize(query) if query else set()
        query_lower = query.lower().strip() if query else ''
        
        results = []
        
        for node_id, data in self._graph.nodes(data=True):
            data_type = data.get('type', '').lower()
            data_layer = data.get('layer', '').lower() or self.TYPE_TO_LAYER.get(data_type, '')
            
            # Type filter
            if type_filter and data_type not in type_filter:
                continue
            
            # Layer filter
            if layer_filter and data_layer not in layer_filter:
                continue
            
            # File pattern filter
            file_path = data.get('file_path', '')
            if file_regexes:
                if not any(rx.search(file_path) for rx in file_regexes):
                    continue
            
            # Relations filter
            if has_relations is not None:
                has_edges = (
                    self._graph.in_degree(node_id) > 0 or 
                    self._graph.out_degree(node_id) > 0
                )
                if has_relations and not has_edges:
                    continue
                if not has_relations and has_edges:
                    continue
            
            # Citations filter
            if min_citations:
                citations = data.get('citations', [])
                if len(citations) < min_citations:
                    continue
            
            # Text search
            score = 1.0
            match_field = 'filter'
            
            if query:
                name = data.get('name', '')
                description = data.get('description', '')
                if isinstance(data.get('properties'), dict):
                    description = description or data['properties'].get('description', '')
                
                score, match_field = self._calculate_match_score(
                    query_tokens, query_lower, name, data_type, description, file_path
                )
                
                if score == 0:
                    continue
            
            results.append({
                'entity': dict(data),
                'score': score,
                'match_field': match_field,
            })
        
        results.sort(key=lambda x: (-x['score'], x['entity'].get('name', '').lower()))
        return results[:top_k]
    
    def get_entities_by_source(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all entities from a specific source document."""
        node_ids = self._source_doc_index.get(doc_id, set())
        return [self.get_entity(nid) for nid in node_ids if nid]
    
    def get_entities_by_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Get all entities with citations from a specific file."""
        # First try the file index
        node_ids = self._file_index.get(file_path, set())
        if node_ids:
            return [self.get_entity(nid) for nid in node_ids if nid]
        
        # Fallback to linear scan for partial matches
        results = []
        for _, data in self._graph.nodes(data=True):
            # Check file_path attribute
            if data.get('file_path') == file_path:
                results.append(dict(data))
                continue
            
            # Check citations
            for citation in data.get('citations', []):
                if isinstance(citation, dict) and citation.get('file_path') == file_path:
                    results.append(dict(data))
                    break
        
        return results
    
    # ========== Delta Operations ==========
    
    def remove_entities_by_source(self, doc_id: str) -> int:
        """
        Remove all entities from a specific source document.
        Used for delta updates to clean stale entities.
        
        Returns:
            Number of entities removed
        """
        node_ids = list(self._source_doc_index.get(doc_id, set()))
        for node_id in node_ids:
            self.remove_entity(node_id)
        return len(node_ids)
    
    def remove_entities_by_file(self, file_path: str) -> int:
        """
        Remove all entities with citations from a specific file.
        Used for delta updates when a file changes.
        
        Returns:
            Number of entities removed
        """
        to_remove = []
        for node_id, data in self._graph.nodes(data=True):
            citation = data.get('citation', {})
            if isinstance(citation, dict) and citation.get('file_path') == file_path:
                to_remove.append(node_id)
        
        for node_id in to_remove:
            self.remove_entity(node_id)
        
        return len(to_remove)
    
    # ========== Schema Operations ==========
    
    def set_schema(self, schema: Dict[str, Any]) -> None:
        """Store the discovered entity schema."""
        self._schema = schema
    
    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Get the discovered schema."""
        return self._schema
    
    # ========== Statistics ==========
    
    def get_stats(self) -> Dict[str, Any]:
        """Get graph statistics."""
        entity_types = defaultdict(int)
        relation_types = defaultdict(int)
        sources = set()
        relations_by_source = defaultdict(int)

        for _, data in self._graph.nodes(data=True):
            if 'type' in data:
                entity_types[data['type']] += 1
            # Check source_toolkit directly on node
            if data.get('source_toolkit'):
                sources.add(data['source_toolkit'])
            # Check citations array (new format)
            citations = data.get('citations', [])
            if isinstance(citations, list):
                for citation in citations:
                    if isinstance(citation, dict) and citation.get('source_toolkit'):
                        sources.add(citation['source_toolkit'])
            # Check single citation dict (legacy format)
            citation = data.get('citation', {})
            if isinstance(citation, dict) and citation.get('source_toolkit'):
                sources.add(citation['source_toolkit'])
        
        for _, _, data in self._graph.edges(data=True):
            if 'relation_type' in data:
                relation_types[data['relation_type']] += 1
            # Track relations by source
            rel_source = data.get('source_toolkit')
            if rel_source:
                relations_by_source[rel_source] += 1
        
        return {
            'node_count': self._graph.number_of_nodes(),
            'edge_count': self._graph.number_of_edges(),
            'entity_types': dict(entity_types),
            'relation_types': dict(relation_types),
            'edge_types': sorted(relation_types.keys()),  # Sorted list of unique edge types for UI filters
            'source_toolkits': sorted(sources),
            'relations_by_source': dict(relations_by_source),
            'cross_source_relations': len(self.get_cross_source_relations()),
            'last_saved': self._metadata.get('last_saved'),
            'has_embeddings': any(
                data.get('embedding') for _, data in self._graph.nodes(data=True)
            ),
            'embeddings_count': sum(
                1 for _, data in self._graph.nodes(data=True) if data.get('embedding')
            ),
            'embeddings_model': self._metadata.get('embeddings_model'),
            'has_communities': bool(self._metadata.get('community_data')),
            'num_communities': self._metadata.get('community_data', {}).get('num_communities', 0),
        }
    
    # ========== Community Operations ==========

    def rebuild_community_index(self) -> None:
        """Rebuild _community_index from community_data in metadata."""
        self._community_index = {}
        community_data = self._metadata.get('community_data', {})
        communities = community_data.get('communities', {})
        for cid, cinfo in communities.items():
            self._community_index[cid] = cinfo.get('members', [])
        if self._community_index:
            logger.info(
                f"Rebuilt community index: {len(self._community_index)} communities"
            )

    def set_community_data(self, community_data: Dict[str, Any]) -> None:
        """
        Store community detection results and update node attributes.

        Args:
            community_data: Dict from CommunityAnalyzer.detect_communities()
        """
        self._metadata['community_data'] = community_data

        # Set community_id attribute on each node
        for cid, cinfo in community_data.get('communities', {}).items():
            for node_id in cinfo.get('members', []):
                if self._graph.has_node(node_id):
                    self._graph.nodes[node_id]['community_id'] = cid

        self.rebuild_community_index()

    def get_communities(self) -> Dict[str, Any]:
        """
        Return community overview without full member lists.

        Includes label, size, top centroids, and stats for each community.
        """
        community_data = self._metadata.get('community_data', {})
        if not community_data:
            return {}

        overview = {
            "algorithm": community_data.get("algorithm"),
            "modularity": community_data.get("modularity"),
            "num_communities": community_data.get("num_communities", 0),
            "communities": {},
        }

        for cid, cinfo in community_data.get("communities", {}).items():
            overview["communities"][cid] = {
                "label": cinfo.get("label", ""),
                "size": cinfo.get("stats", {}).get("size", len(cinfo.get("members", []))),
                "centroids": cinfo.get("centroids", []),
                "dominant_types": cinfo.get("dominant_types", []),
                "dominant_layers": cinfo.get("dominant_layers", []),
                "summary": cinfo.get("summary"),
            }

        return overview

    def get_community(self, community_id: str) -> Optional[Dict[str, Any]]:
        """Return full detail for a specific community."""
        community_data = self._metadata.get('community_data', {})
        return community_data.get('communities', {}).get(community_id)

    def get_community_for_entity(self, entity_id: str) -> Optional[str]:
        """Look up which community an entity belongs to."""
        node = self._graph.nodes.get(entity_id)
        if node:
            return node.get('community_id')
        return None

    def get_community_centroids(self, community_id: str) -> List[Dict]:
        """Return centroid entities with scores for a community."""
        community = self.get_community(community_id)
        if not community:
            return []
        return community.get('centroids', [])

    def get_community_members(self, community_id: str) -> List[str]:
        """Return member node IDs for a community."""
        return self._community_index.get(community_id, [])

    # ========== Persistence ==========
    
    def dump_to_json(self, path: str, exclude_embeddings: bool = False) -> None:
        """
        Export graph to JSON file using node_link format.
        
        The graph file is lightweight - contains only:
        - Entity metadata and citations (no raw content)
        - Relationships
        - Schema and indices
        
        Args:
            path: File path to write JSON
            exclude_embeddings: If True, strip embedding vectors from output
                for lightweight exports (e.g. visualization).
        """
        # Use edges="links" explicitly for NetworkX 3.5+ compatibility
        # This ensures consistent format that load_from_json expects
        data = nx.node_link_data(self._graph, edges="links")
        
        if exclude_embeddings:
            for node in data.get('nodes', []):
                node.pop('embedding', None)
        
        # Add index data for persistence
        data['_indices'] = {
            'entity_index': {k: list(v) for k, v in self._entity_index.items()},
            'type_index': {k: list(v) for k, v in self._type_index.items()},
            'file_index': {k: list(v) for k, v in self._file_index.items()},
            'source_doc_index': {k: list(v) for k, v in self._source_doc_index.items()}
        }
        
        # Add schema if discovered
        if self._schema:
            data['_schema'] = self._schema
        
        # Add metadata (use a copy so we don't mutate the live object)
        metadata = dict(self._metadata)
        metadata['last_saved'] = datetime.now().isoformat()
        metadata['version'] = '2.1'  # Enhanced indices version
        if exclude_embeddings:
            # Clear embedding metadata to avoid stale stats after reload
            metadata.pop('embeddings_model', None)
            metadata.pop('embeddings_count', None)
            metadata.pop('embeddings_generated_at', None)
        data['_metadata'] = metadata
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info(f"Saved graph to {path} ({self._graph.number_of_nodes()} entities, {self._graph.number_of_edges()} relations)")
    
    def load_from_json(self, path: str) -> None:
        """
        Load graph from JSON file.
        
        Args:
            path: File path to read JSON from
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Restore indices
        indices = data.pop('_indices', {})
        
        # Entity index - convert to set (handles both old string format and new list format)
        self._entity_index = defaultdict(set)
        for k, v in indices.get('entity_index', {}).items():
            if isinstance(v, list):
                self._entity_index[k] = set(v)
            elif isinstance(v, str):
                self._entity_index[k] = {v}  # Legacy format
        
        # Type index
        self._type_index = defaultdict(set)
        for k, v in indices.get('type_index', {}).items():
            self._type_index[k] = set(v) if isinstance(v, list) else set()
        
        # File index
        self._file_index = defaultdict(set)
        for k, v in indices.get('file_index', {}).items():
            self._file_index[k] = set(v) if isinstance(v, list) else set()
        
        # Source doc index
        self._source_doc_index = defaultdict(set)
        for k, v in indices.get('source_doc_index', {}).items():
            self._source_doc_index[k] = set(v) if isinstance(v, list) else set()
        
        # Restore schema
        self._schema = data.pop('_schema', None)
        
        # Restore metadata
        self._metadata = data.pop('_metadata', {})
        
        # Restore graph - handle both "links" and "edges" keys for compatibility
        # NetworkX 3.5+ defaults to "edges", but we write "links" for visualization compatibility
        if 'edges' in data and 'links' not in data:
            # Data uses new NetworkX 3.5+ default "edges" key - rename to "links" for node_link_graph
            data['links'] = data.pop('edges')
        
        self._graph = nx.node_link_graph(data, edges="links")
        
        # Normalize edge relation types to lowercase for consistent querying
        for u, v, edge_data in self._graph.edges(data=True):
            if 'relation_type' in edge_data:
                edge_data['relation_type'] = edge_data['relation_type'].lower()
        
        # Rebuild missing indices if needed (for legacy graphs)
        if not self._type_index or not self._file_index:
            self._rebuild_indices()
        
        # Rebuild community index from stored community metadata
        self.rebuild_community_index()
        
        logger.info(f"Loaded graph from {path} ({self._graph.number_of_nodes()} entities, {self._graph.number_of_edges()} relations)")
    
    def _rebuild_indices(self) -> None:
        """Rebuild all indices from graph data (for legacy graph files)."""
        self._entity_index = defaultdict(set)
        self._type_index = defaultdict(set)
        self._file_index = defaultdict(set)
        self._source_doc_index = defaultdict(set)
        
        for node_id, data in self._graph.nodes(data=True):
            # Name index
            name = data.get('name', '').lower()
            if name:
                self._entity_index[name].add(node_id)
            
            # Type index - normalize for consistency
            raw_type = data.get('type', '')
            if raw_type:
                entity_type = _normalize_entity_type(raw_type)
                self._type_index[entity_type].add(node_id)
                # Also update the node data if type changed during normalization
                if entity_type != raw_type:
                    self._graph.nodes[node_id]['type'] = entity_type
            
            # File index (from file_path attribute)
            file_path = data.get('file_path', '')
            if file_path:
                self._file_index[file_path].add(node_id)
            
            # Also index from citations
            for citation in data.get('citations', []):
                if isinstance(citation, dict):
                    fp = citation.get('file_path', '')
                    if fp:
                        self._file_index[fp].add(node_id)
                    doc_id = citation.get('doc_id', '')
                    if doc_id:
                        self._source_doc_index[doc_id].add(node_id)
        
        logger.info(f"Rebuilt indices: {len(self._entity_index)} names, {len(self._type_index)} types, {len(self._file_index)} files")
    
    def clear(self) -> None:
        """Clear all data from the graph."""
        self._graph.clear()
        self._entity_index.clear()
        self._type_index.clear()
        self._file_index.clear()
        self._source_doc_index.clear()
        self._community_index = {}
        self._schema = None
        self._metadata = {}
    
    # ========== Subgraph Operations ==========
    
    def get_subgraph(self, node_ids: List[str]) -> 'KnowledgeGraph':
        """
        Get a subgraph containing only specified nodes and their edges.
        
        Args:
            node_ids: List of node IDs to include
            
        Returns:
            New KnowledgeGraph instance with subgraph
        """
        subgraph = KnowledgeGraph()
        subgraph._graph = self._graph.subgraph(node_ids).copy()
        
        # Rebuild indices for subgraph
        for node_id, data in subgraph._graph.nodes(data=True):
            name = data.get('name', '').lower()
            if name:
                subgraph._entity_index[name] = node_id
            
            citation = data.get('citation', {})
            if isinstance(citation, dict):
                doc_id = citation.get('doc_id')
                if doc_id:
                    subgraph._source_doc_index[doc_id].add(node_id)
        
        return subgraph
    
    def get_connected_component(self, node_id: str) -> List[str]:
        """
        Get all nodes in the same connected component as the given node.
        
        Args:
            node_id: Starting node ID
            
        Returns:
            List of node IDs in the connected component
        """
        if not self._graph.has_node(node_id):
            return []
        
        # For directed graphs, use weakly connected components
        undirected = self._graph.to_undirected()
        component = nx.node_connected_component(undirected, node_id)
        return list(component)

    def find_bridging_nodes(
        self,
        entity_ids: List[str],
        max_bridge_length: int = 3,
        max_bridges: int = 20
    ) -> Dict[str, Any]:
        """
        Find minimal bridging nodes to connect disjoint entity clusters.

        Uses a Steiner tree-like approach: for each pair of disconnected clusters,
        find the shortest path and add intermediate nodes to create a more
        connected visualization.

        Args:
            entity_ids: List of entity IDs to connect
            max_bridge_length: Maximum path length between nodes (default 3 = 2 intermediate nodes max)
            max_bridges: Maximum number of bridging paths to add

        Returns:
            Dict with:
                - bridging_nodes: List of intermediate node IDs to add
                - bridging_edges: List of edges in the bridging paths
                - clusters: Number of original disconnected clusters
        """
        # Filter to entities that exist
        valid_ids = [eid for eid in entity_ids if self._graph.has_node(eid)]
        if len(valid_ids) < 2:
            return {'bridging_nodes': [], 'bridging_edges': [], 'clusters': len(valid_ids)}

        # Build undirected view for path finding
        undirected = self._graph.to_undirected()

        # Find connected components among our target nodes
        target_set = set(valid_ids)
        components = []
        visited = set()

        for node_id in valid_ids:
            if node_id in visited:
                continue
            # Find all target nodes reachable from this one
            component = set()
            queue = [node_id]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                if current in target_set:
                    component.add(current)
                    # Explore neighbors in full graph
                    for neighbor in undirected.neighbors(current):
                        if neighbor not in visited:
                            queue.append(neighbor)
            if component:
                components.append(component)

        if len(components) <= 1:
            # Already connected
            return {'bridging_nodes': [], 'bridging_edges': [], 'clusters': 1}

        # Find shortest bridges between component pairs
        bridging_nodes = set()
        bridging_edges = []
        bridges_found = 0

        # Sort components by size (connect smaller ones first)
        components.sort(key=len)

        # Try to connect each component to another
        for i, comp1 in enumerate(components):
            if bridges_found >= max_bridges:
                break

            best_path = None
            best_length = float('inf')

            for comp2 in components[i+1:]:
                # Find shortest path between any node in comp1 and any node in comp2
                for n1 in comp1:
                    if bridges_found >= max_bridges:
                        break
                    for n2 in comp2:
                        try:
                            path = nx.shortest_path(undirected, n1, n2)
                            if len(path) <= max_bridge_length and len(path) < best_length:
                                best_path = path
                                best_length = len(path)
                        except nx.NetworkXNoPath:
                            continue

            # Add the best bridge found
            if best_path and len(best_path) > 2:
                bridges_found += 1
                # Add intermediate nodes (exclude endpoints which are already in our set)
                for node in best_path[1:-1]:
                    bridging_nodes.add(node)

                # Add all edges along the path
                for j in range(len(best_path) - 1):
                    source, target = best_path[j], best_path[j+1]
                    # Get edge data from the actual directed graph
                    if self._graph.has_edge(source, target):
                        edge_data = dict(self._graph.edges[source, target])
                        bridging_edges.append({
                            'source': source,
                            'target': target,
                            'type': edge_data.get('relation_type', 'RELATED'),
                        })
                    elif self._graph.has_edge(target, source):
                        edge_data = dict(self._graph.edges[target, source])
                        bridging_edges.append({
                            'source': target,
                            'target': source,
                            'type': edge_data.get('relation_type', 'RELATED'),
                        })

        return {
            'bridging_nodes': list(bridging_nodes),
            'bridging_edges': bridging_edges,
            'clusters': len(components)
        }

    # ========== Embedding Operations ==========

    def generate_embeddings(
        self,
        embedding_model: Any,
        force: bool = False,
        batch_size: int = 256,
    ) -> int:
        """
        Generate embeddings for all entities using a LangChain Embeddings instance.

        Composes text from entity name, type, description, and properties,
        then calls embedding_model.embed_documents() in batches.

        Args:
            embedding_model: A LangChain Embeddings instance
                (e.g. HuggingFaceEmbeddings or OpenAIEmbeddings).
            force: If True, regenerate embeddings even for entities that already have them.
            batch_size: Number of texts to embed per batch call.

        Returns:
            Number of entities that received embeddings.
        """
        self._embedding_model = embedding_model

        # Collect nodes that need embedding
        node_ids = []
        texts = []

        for node_id, data in self._graph.nodes(data=True):
            if not force and data.get('embedding'):
                continue

            text = self._compose_embedding_text(data)
            if not text:
                continue

            node_ids.append(node_id)
            texts.append(text)

        if not texts:
            logger.info("No entities need embedding generation")
            return 0

        # Embed in batches
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = embedding_model.embed_documents(batch)
            all_embeddings.extend(batch_embeddings)

        # Store embeddings on nodes
        for node_id, emb in zip(node_ids, all_embeddings):
            self._graph.nodes[node_id]['embedding'] = emb

        # Track metadata
        model_info = type(embedding_model).__name__
        if hasattr(embedding_model, 'model_name'):
            model_info += f":{embedding_model.model_name}"
        self._metadata['embeddings_model'] = model_info
        self._metadata['embeddings_generated_at'] = datetime.now().isoformat()
        self._metadata['embeddings_count'] = len(all_embeddings)

        logger.info(f"Generated embeddings for {len(all_embeddings)} entities using {model_info}")
        return len(all_embeddings)

    def semantic_search(
        self,
        query: str,
        embedding_model: Any = None,
        top_k: int = 10,
        entity_type: Optional[str] = None,
        layer: Optional[str] = None,
        file_pattern: Optional[str] = None,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Search entities by semantic similarity using embeddings.

        Args:
            query: Natural language search query.
            embedding_model: LangChain Embeddings instance for encoding the query.
                Falls back to the model used in generate_embeddings().
            top_k: Maximum number of results to return.
            entity_type: Filter by entity type (case-insensitive).
            layer: Filter by layer (code, service, data, etc.).
            file_pattern: Filter by file path pattern (glob-like).
            min_score: Minimum cosine similarity threshold.

        Returns:
            List of dicts with 'entity', 'score', and 'match_field' keys,
            sorted by descending similarity score.

        Raises:
            ValueError: If no embedding model is available.
        """
        import numpy as np

        model = embedding_model or getattr(self, '_embedding_model', None)
        if model is None:
            raise ValueError(
                "No embedding model available. Pass embedding_model parameter "
                "or call generate_embeddings() first."
            )

        query_embedding = np.array(model.embed_query(query), dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding)
        if query_norm == 0:
            return []

        import re as _re

        # Compile file pattern once
        file_regex = None
        if file_pattern:
            pattern = file_pattern.replace('.', r'\.').replace('*', '.*').replace('?', '.')
            try:
                file_regex = _re.compile(pattern, _re.IGNORECASE)
            except _re.error:
                pass

        # Layer types for filtering
        layer_types = set()
        if layer:
            layer_types = self.LAYER_TYPE_MAPPING.get(layer.lower(), set())

        results = []
        for node_id, data in self._graph.nodes(data=True):
            emb = data.get('embedding')
            if not emb:
                continue

            # Type filter
            data_type = data.get('type', '').lower()
            if entity_type and data_type != entity_type.lower():
                continue

            # Layer filter
            if layer:
                entity_layer = data.get('layer', '').lower()
                if entity_layer != layer.lower() and data_type not in layer_types:
                    continue

            # File pattern filter
            if file_regex:
                citations = data.get('citations', [])
                if not citations and 'citation' in data:
                    citations = [data['citation']]
                file_paths = [c.get('file_path', '') for c in citations if isinstance(c, dict)]
                primary_file = file_paths[0] if file_paths else data.get('file_path', '')
                if primary_file and not file_regex.search(primary_file):
                    continue

            # Cosine similarity
            node_emb = np.array(emb, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm == 0:
                continue
            score = float(np.dot(query_embedding, node_emb) / (query_norm * node_norm))

            if score < min_score:
                continue

            entity_data = dict(data)
            entity_data['id'] = node_id
            # Exclude embedding vector from results — it's large and not useful for display
            entity_data.pop('embedding', None)
            results.append({
                'entity': entity_data,
                'score': round(score, 4),
                'match_field': 'semantic',
            })

        results.sort(key=lambda x: (-x['score'], x['entity'].get('name', '').lower()))
        return results[:top_k]

    @staticmethod
    def _compose_embedding_text(node_data: Dict[str, Any]) -> str:
        """Compose text for embedding from entity attributes."""
        parts = []
        name = node_data.get('name', '')
        if name:
            parts.append(name)

        entity_type = node_data.get('type', '')
        if entity_type:
            parts.append(entity_type.replace('_', ' '))

        description = node_data.get('description', '')
        if not description and isinstance(node_data.get('properties'), dict):
            description = node_data['properties'].get('description', '')
        if description:
            parts.append(description)

        # Include select properties that add semantic value
        props = node_data.get('properties', {})
        if isinstance(props, dict):
            for key in ('purpose', 'summary', 'signature', 'docstring'):
                val = props.get(key, '')
                if val and isinstance(val, str):
                    parts.append(val)

        return ' '.join(parts).strip()

    # ========== Pattern Query Engine ==========
    
    MAX_PATTERN_HOPS = 5
    MAX_PATTERN_RESULTS = 100
    
    # Synonym map: common LLM-generated names → canonical graph relation types (lowercase)
    # Based on actual graph schema: contains, related_to, calls, implements, extends, imports, decorates
    # All values are lowercase because edge matching uses .lower() comparison
    RELATION_SYNONYMS = {
        # → contains
        'contain': 'contains', 'has': 'contains', 'includes': 'contains',
        'include': 'contains', 'owns': 'contains', 'parent': 'contains',
        'has_child': 'contains', 'defines': 'contains',
        # → related_to (the primary doc↔code and doc↔doc connector)
        'related': 'related_to', 'relates': 'related_to',
        'reference': 'related_to', 'refers_to': 'related_to',
        'associated': 'related_to', 'associated_with': 'related_to',
        'linked': 'related_to', 'linked_to': 'related_to',
        'describes': 'related_to', 'documents': 'related_to',
        'about': 'related_to',
        # NOTE: 'mentions' and 'references' are first-class relation types
        # in the graph (cross-file content mentions / LLM-extracted references),
        # so they must NOT be synonyms for 'related_to'.
        # → calls
        'invoke': 'calls', 'invokes': 'calls', 'call': 'calls',
        'triggers': 'calls', 'trigger': 'calls', 'uses': 'calls',
        # → implements
        'implement': 'implements', 'realizes': 'implements', 'fulfills': 'implements',
        'satisfies': 'implements',
        # → extends
        'inherit': 'extends', 'inherits': 'extends', 'inheritance': 'extends',
        'extend': 'extends', 'derives': 'extends', 'subclass': 'extends',
        # → imports
        'require': 'imports', 'requires': 'imports', 'import': 'imports',
        'depends': 'imports', 'depends_on': 'imports', 'dependency': 'imports',
        # → decorates
        'decorate': 'decorates', 'wraps': 'decorates', 'annotates': 'decorates',
    }
    
    PATTERN_SYNTAX_HELP = (
        "Pattern syntax:\n"
        "  Single:  (source)-[:relation*min..max]->(target)\n"
        "  Chain:   (A)-[:rel1]->(B)-[:rel2]->(C)  (up to 4 segments)\n"
        "  Nodes:   (?) = any, (?:class) = typed wildcard, (Name) = named, (Name:type) = named+typed\n"
        "  Rels:    [:calls] = 1 hop, [:calls*1..3] = 1-3 hops, [:*1..3] = any type, [:]= any 1 hop\n"
        "  Dir:     -> = forward, <- = backward (per segment)\n"
        "  Examples:\n"
        "    (UserService)-[:calls*1..3]->(?)\n"
        "    (?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)\n"
        "    (?:user_story)-[:related_to]->(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)"
    )
    
    MAX_CHAIN_SEGMENTS = 4
    
    @staticmethod
    def _parse_node_spec(spec: str) -> Dict[str, Any]:
        """Parse node specifier from pattern syntax.
        
        Formats:
            ?             -> wildcard (any node)
            ?:class       -> typed wildcard (any node of type 'class')
            ?:class,func  -> multi-typed wildcard
            Name          -> named node (case-insensitive)
            Name:class    -> named node with type constraint
        """
        spec = spec.strip()
        if not spec or spec == '?':
            return {'name': None, 'types': None}
        
        if spec.startswith('?:'):
            types_str = spec[2:]
            types = [t.strip().lower() for t in types_str.split(',') if t.strip()]
            return {'name': None, 'types': types or None}
        
        if ':' in spec:
            name, types_str = spec.split(':', 1)
            types = [t.strip().lower() for t in types_str.split(',') if t.strip()]
            return {'name': name.strip() or None, 'types': types or None}
        
        return {'name': spec, 'types': None}
    
    def _parse_pattern(self, pattern: str) -> List[Dict[str, Any]]:
        """Parse graph pattern query string into a list of segment dicts.
        
        Supports both single-segment and multi-segment (chain) patterns:
            Single:  (source)-[:rel*min..max]->(target)
            Chain:   (A)-[:rel1]->(B)-[:rel2]->(C)-[:rel3]->(D)
        
        Each segment in the chain can have its own relation types, hop range,
        and direction. Up to MAX_CHAIN_SEGMENTS segments allowed.
        
        Returns:
            List of segment dicts, each with keys:
                source, target, rel_types, min_hops, max_hops, direction
        """
        import re
        
        pattern = pattern.strip()
        
        # Regex for one segment: (node)<arrow+relation>(next_node)
        # We tokenize segments by finding all (node)-[:rel]->(node) blocks,
        # where intermediate nodes are shared between adjacent segments.
        segment_re = re.compile(
            r'\(([^)]*)\)\s*'            # (source)
            r'(<?\-\[:([^\]]*)\]\-?>?)'  # arrow + relation block
            r'\s*\(([^)]*)\)'            # (target)
        )
        
        # Find all segments by scanning for overlapping (target)(next_source) boundaries
        # Strategy: split the full pattern into segments at )( boundaries that follow an arrow
        segments = []
        remaining = pattern
        
        while remaining:
            m = segment_re.match(remaining)
            if not m:
                if segments:
                    raise ValueError(
                        f"Invalid pattern continuation: ...{remaining[:40]}\n"
                        f"{self.PATTERN_SYNTAX_HELP}"
                    )
                raise ValueError(
                    f"Invalid pattern: {pattern}\n{self.PATTERN_SYNTAX_HELP}"
                )
            
            seg = self._parse_single_segment(
                m.group(1), m.group(2), m.group(3), m.group(4)
            )
            segments.append(seg)
            
            # Advance past the matched segment
            remaining = remaining[m.end():].strip()
            
            if remaining:
                # Next segment must start with the arrow: -[:...]-> or <-[:...]-
                # But the source node is the previous target, so we expect -[:...]->(...) 
                # Prepend the previous target as the new source
                prev_target = m.group(4)
                remaining = f"({prev_target}){remaining}"
        
        if not segments:
            raise ValueError(
                f"Invalid pattern: {pattern}\n{self.PATTERN_SYNTAX_HELP}"
            )
        
        if len(segments) > self.MAX_CHAIN_SEGMENTS:
            raise ValueError(
                f"Pattern has {len(segments)} segments, maximum is "
                f"{self.MAX_CHAIN_SEGMENTS}. Simplify the pattern."
            )
        
        return segments
    
    def _parse_single_segment(
        self,
        source_str: str,
        arrow_block: str,
        rel_spec_str: str,
        target_str: str,
    ) -> Dict[str, Any]:
        """Parse a single pattern segment into a structured query dict."""
        # Determine direction from arrow
        if arrow_block.startswith('<-') and arrow_block.endswith('-'):
            direction = 'backward'
        elif arrow_block.endswith('->'):
            direction = 'forward'
        else:
            raise ValueError(
                f"Ambiguous arrow in pattern: {arrow_block}\n"
                "Use -> for forward or <- for backward.\n"
                f"{self.PATTERN_SYNTAX_HELP}"
            )
        
        # Parse relation spec: "calls,imports*2..4" or "calls" or "*1..3" or "" or "calls*2"
        rel_types = None
        min_hops = 1
        max_hops = 1
        
        if '*' in rel_spec_str:
            parts = rel_spec_str.split('*', 1)
            types_part = parts[0].strip()
            hops_part = parts[1].strip()
            
            if types_part:
                rel_types = [t.strip().lower() for t in types_part.split(',') if t.strip()]
            
            if '..' in hops_part:
                lo, hi = hops_part.split('..', 1)
                try:
                    min_hops = int(lo)
                    max_hops = int(hi)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid hop specification '{hops_part}' in relation segment "
                        f"'{rel_spec_str}'.\n{self.PATTERN_SYNTAX_HELP}"
                    )
            else:
                try:
                    min_hops = max_hops = int(hops_part)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid hop specification '{hops_part}' in relation segment "
                        f"'{rel_spec_str}'.\n{self.PATTERN_SYNTAX_HELP}"
                    )
        elif rel_spec_str.strip():
            rel_types = [t.strip().lower() for t in rel_spec_str.split(',') if t.strip()]
        
        # Normalize relation type synonyms (e.g., 'inherit' → 'extends')
        if rel_types:
            rel_types = [self.RELATION_SYNONYMS.get(rt, rt) for rt in rel_types]
        
        # Validate hop range
        if min_hops < 1:
            raise ValueError(f"Minimum hops must be >= 1, got {min_hops}")
        if max_hops > self.MAX_PATTERN_HOPS:
            raise ValueError(f"Maximum hops must be <= {self.MAX_PATTERN_HOPS}, got {max_hops}")
        if min_hops > max_hops:
            raise ValueError(f"min_hops ({min_hops}) > max_hops ({max_hops})")
        
        return {
            'source': self._parse_node_spec(source_str),
            'target': self._parse_node_spec(target_str),
            'rel_types': rel_types,
            'min_hops': min_hops,
            'max_hops': max_hops,
            'direction': direction,
        }
    
    def _resolve_pattern_nodes(self, spec: Dict[str, Any]) -> Optional[Set[str]]:
        """Resolve a node specifier to a set of node IDs.
        
        Returns None for full wildcard (any node) to avoid materializing huge sets.
        """
        name = spec.get('name')
        types = spec.get('types')
        
        if name is None and types is None:
            return None  # Full wildcard — caller handles iteration
        
        if name is not None:
            # Named node lookup
            node_ids = set(self._entity_index.get(name.lower(), set()))
            if not node_ids:
                # Fuzzy fallback
                results = self.search(name, top_k=5)
                node_ids = {r['entity']['id'] for r in results if 'entity' in r and 'id' in r['entity']}
            # Filter by types if specified
            if types and node_ids:
                node_ids = {
                    nid for nid in node_ids
                    if self._graph.nodes[nid].get('type', '').lower() in types
                }
            return node_ids
        
        # Typed wildcard: union of type indices
        node_ids = set()
        for t in types:
            node_ids.update(self._type_index.get(t, set()))
            # Also check layer mapping
            if t in self.LAYER_TYPE_MAPPING:
                for lt in self.LAYER_TYPE_MAPPING[t]:
                    node_ids.update(self._type_index.get(lt, set()))
        return node_ids
    
    def _execute_pattern(
        self,
        parsed: Dict[str, Any],
        max_results: int,
        source_node_override: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a parsed pattern query via BFS.
        
        Strategy: BFS from the anchored (smaller resolved set) side, checking
        target constraints at each step once min_hops is reached.
        
        Args:
            source_node_override: If provided, uses these node IDs as the source
                instead of resolving from parsed['source']. Used by chain execution.
        """
        if source_node_override is not None:
            source_nodes = source_node_override
        else:
            source_nodes = self._resolve_pattern_nodes(parsed['source'])
        target_nodes = self._resolve_pattern_nodes(parsed['target'])
        direction = parsed['direction']
        rel_types = parsed.get('rel_types')
        min_hops = parsed['min_hops']
        max_hops = parsed['max_hops']
        
        # Decide BFS start side
        # For forward queries: BFS from source using out_edges
        # For backward queries: BFS from source using in_edges
        # When one side is wildcard and the other is anchored, flip to BFS from the anchored side
        if direction == 'forward':
            start_nodes = source_nodes
            end_nodes = target_nodes
            get_edges = self._graph.out_edges
            neighbor_idx = 1  # target is at index 1 in (src, tgt, data)
        else:  # backward
            start_nodes = source_nodes
            end_nodes = target_nodes
            get_edges = self._graph.in_edges
            neighbor_idx = 0  # source is at index 0 in (src, tgt, data)
        
        # If start is wildcard but end is not, flip: BFS from end with reversed edges
        flipped = False
        if start_nodes is None and end_nodes is not None:
            start_nodes, end_nodes = end_nodes, start_nodes
            flipped = True
            if direction == 'forward':
                get_edges = self._graph.in_edges
                neighbor_idx = 0
            else:
                get_edges = self._graph.out_edges
                neighbor_idx = 1
        
        # If both wildcard — iterate all edges matching rel_types as seeds
        if start_nodes is None:
            start_nodes = self._seed_from_edges(rel_types, neighbor_idx)
        
        rel_types_set = set(rel_types) if rel_types else None
        end_nodes_set = end_nodes  # None means any node is valid endpoint
        
        results = []
        
        # BFS: each item is (current_node, path_of_node_ids, edge_types_along_path)
        from collections import deque
        queue = deque((nid, [nid], []) for nid in start_nodes)
        
        while queue and len(results) < max_results:
            current, path, edges = queue.popleft()
            depth = len(edges)
            
            # If at or past min_hops, check if current matches target
            if depth >= min_hops:
                if self._node_matches_spec(current, end_nodes_set):
                    # Reverse path/edges when flipped so output matches pattern order
                    if flipped:
                        results.append(self._format_path_result(
                            path[::-1], edges[::-1]))
                    else:
                        results.append(self._format_path_result(path, edges))
                    if len(results) >= max_results:
                        break
            
            # If at max depth, don't expand further
            if depth >= max_hops:
                continue
            
            # Expand neighbors
            for edge_tuple in get_edges(current, data=True):
                neighbor = edge_tuple[neighbor_idx]
                edge_data = edge_tuple[2]
                edge_rel = edge_data.get('relation_type', '').lower()
                
                # Filter by relation type
                if rel_types_set and edge_rel not in rel_types_set:
                    continue
                
                # Avoid cycles within a single path
                if neighbor in path:
                    continue
                
                queue.append((neighbor, path + [neighbor], edges + [edge_rel]))
        
        return results
    
    def _seed_from_edges(
        self,
        rel_types: Optional[List[str]],
        neighbor_idx: int,
    ) -> Set[str]:
        """For dual-wildcard queries, collect unique start nodes from matching edges.
        
        Seeds from the correct edge endpoint based on BFS direction:
        - neighbor_idx=1 (forward/out_edges): seed from u (source), BFS follows outgoing
        - neighbor_idx=0 (backward/in_edges): seed from v (target), BFS follows incoming
        """
        rel_set = set(rel_types) if rel_types else None
        # Seed from the opposite end of where BFS will traverse toward
        seed_idx = 0 if neighbor_idx == 1 else 1
        seeds = set()
        for u, v, data in self._graph.edges(data=True):
            edge_rel = data.get('relation_type', '').lower()
            if rel_set and edge_rel not in rel_set:
                continue
            seeds.add((u, v)[seed_idx])
            if len(seeds) >= 500:  # Cap seeds for dual-wildcard performance
                break
        return seeds
    
    def _node_matches_spec(
        self,
        node_id: str,
        resolved_set: Optional[Set[str]],
    ) -> bool:
        """Check if a node matches target specification (resolved set or wildcard)."""
        if resolved_set is None:
            return True  # Wildcard — any node matches
        return node_id in resolved_set
    
    def _format_path_result(
        self,
        path: List[str],
        edges: List[str],
    ) -> Dict[str, Any]:
        """Format a path result into a compact dict."""
        path_entities = []
        for nid in path:
            data = self._graph.nodes.get(nid, {})
            path_entities.append({
                'id': nid,
                'name': data.get('name', nid),
                'type': data.get('type', ''),
            })
        return {
            'path': path_entities,
            'edges': edges,
            'length': len(edges),
        }
    
    def query_pattern(
        self,
        pattern: str,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher-like graph pattern query.
        
        Finds paths matching the pattern with multi-hop traversal and
        relation type filtering. Supports both single-segment and
        multi-segment chain patterns.
        
        Args:
            pattern: Cypher-like pattern string.
            
                Single segment:
                    (source)-[:relation*min..max]->(target)
                
                Multi-segment chain (each segment can have its own relation/hops):
                    (A)-[:rel1]->(B)-[:rel2]->(C)
                    (A)-[:rel1*1..2]->(B)-[:rel2]->(C)-[:rel3*1..3]->(D)
                
                Node specifiers:
                    (?)             - any node (wildcard)
                    (?:class)       - any node of type 'class'
                    (?:class,func)  - any node of type 'class' or 'func'
                    (UserService)   - node named 'UserService'
                    (UserService:class) - named node with type filter
                
                Relation specifiers:
                    [:calls]        - exactly 1 hop, edge type 'calls'
                    [:calls*1..3]   - 1-3 hops, all edges 'calls'
                    [:*1..3]        - 1-3 hops, any edge type
                    [:]             - 1 hop, any edge type
                
                Direction (per segment):
                    ->              - forward (outgoing edges)
                    <-              - backward (incoming edges)
            
            max_results: Maximum paths to return (default 100, hard cap).
            
        Returns:
            List of path results, each containing:
                path: List of {id, name, type} for each node
                edges: List of relation types along the path
                length: Number of hops
                
        Raises:
            ValueError: If pattern syntax is invalid or hop limit exceeded.
            
        Examples:
            # Single segment
            query_pattern("(UserService)-[:calls*1..3]->(?)")
            query_pattern("(?:class)-[:extends]->(BaseModel)")
            query_pattern("(Controller)<-[:calls*1..2]-(?)")
            
            # Multi-segment chain — per-hop relation control
            query_pattern("(?:feature)-[:implements]->(?:requirement)-[:related_to]->(?:class)")
            query_pattern("(?:user_story)-[:related_to]->(?:feature)-[:related_to]->(?:class)")
            query_pattern("(Controller)-[:calls]->(?:class)-[:extends]->(BaseService)")
            query_pattern("(?:user_story)-[:related_to]->(?:feature)-[:implements]->(?:requirement)")
        """
        max_results = min(max_results, self.MAX_PATTERN_RESULTS)
        segments = self._parse_pattern(pattern)
        
        if len(segments) == 1:
            return self._execute_pattern(segments[0], max_results)
        
        return self._execute_chain(segments, max_results)
    
    def _execute_chain(
        self,
        segments: List[Dict[str, Any]],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        """Execute a multi-segment chain pattern.
        
        Runs each segment sequentially, feeding the endpoint node IDs
        from one segment as the start nodes of the next, then stitches
        the partial paths together.
        """
        MAX_ENDPOINT_CAP = 50  # Limit endpoints fed into next segment
        
        # -- Segment 1 --------------------------------------------------
        seg1_results = self._execute_pattern(segments[0], max_results * 5)
        if not seg1_results:
            return []
        
        # For segments 2..N, iteratively extend the partial paths
        partial_paths = seg1_results  # each is {path, edges, length}
        
        for seg in segments[1:]:
            # Collect endpoint node IDs from current partial results
            endpoint_ids: Set[str] = set()
            for p in partial_paths:
                last_entity = p['path'][-1]
                endpoint_ids.add(last_entity['id'])
                if len(endpoint_ids) >= MAX_ENDPOINT_CAP:
                    break
            
            if not endpoint_ids:
                return []
            
            # Short-circuit: if target is a named/typed spec that resolves to
            # nothing, skip the BFS entirely
            target_nodes = self._resolve_pattern_nodes(seg['target'])
            if target_nodes is not None and not target_nodes:
                return []
            
            # Run BFS for this segment from each endpoint
            seg_results_by_start: Dict[str, List[Dict]] = {}
            for start_id in endpoint_ids:
                per_node = self._execute_pattern(
                    seg, max_results * 3,
                    source_node_override={start_id},
                )
                if per_node:
                    seg_results_by_start[start_id] = per_node
            
            if not seg_results_by_start:
                return []
            
            # Stitch: for each partial path, find continuations from its endpoint
            next_partial: List[Dict[str, Any]] = []
            for pp in partial_paths:
                endpoint_id = pp['path'][-1]['id']
                continuations = seg_results_by_start.get(endpoint_id, [])
                for cont in continuations:
                    # cont['path'][0] is the same node as pp['path'][-1] — skip duplicate
                    stitched_path = pp['path'] + cont['path'][1:]
                    stitched_edges = pp['edges'] + cont['edges']
                    next_partial.append({
                        'path': stitched_path,
                        'edges': stitched_edges,
                        'length': len(stitched_edges),
                    })
                    if len(next_partial) >= max_results:
                        break
                if len(next_partial) >= max_results:
                    break
            
            partial_paths = next_partial
            if not partial_paths:
                return []
        
        return partial_paths[:max_results]
    
    def get_pattern_vocabulary(self) -> Dict[str, Any]:
        """Return graph vocabulary for pattern query composition.
        
        Provides entity types and relation types with counts so an LLM or user
        can compose valid query_pattern calls without prior knowledge of the
        graph's schema.
        
        Returns dict with:
            entity_types: {type: count} sorted by count descending
            relation_types: {type: count} sorted by count descending
            example_patterns: auto-generated examples from actual graph content
        """
        stats = self.get_stats()
        entity_types = stats.get('entity_types', {})
        relation_types = stats.get('relation_types', {})
        
        # Sort by count descending
        sorted_etypes = dict(sorted(entity_types.items(), key=lambda x: -x[1]))
        sorted_rtypes = dict(sorted(relation_types.items(), key=lambda x: -x[1]))
        
        # Auto-generate example patterns from actual graph content
        examples = []
        top_rtypes = list(sorted_rtypes.keys())[:3]
        top_etypes = list(sorted_etypes.keys())[:3]
        
        if top_rtypes and top_etypes:
            rt = top_rtypes[0]
            et = top_etypes[0]
            examples.append(f"(?:{et})-[:{rt}*1..2]->(?)")
        if len(top_rtypes) > 1 and top_etypes:
            rt = top_rtypes[1]
            et = top_etypes[0]
            examples.append(f"(?:{et})-[:{rt}]->(?)")
        if len(top_etypes) > 1 and top_rtypes:
            rt = top_rtypes[0]
            et1, et2 = top_etypes[0], top_etypes[1]
            examples.append(f"(?:{et1})-[:{rt}*1..3]->(?:{et2})")
        
        return {
            'entity_types': sorted_etypes,
            'relation_types': sorted_rtypes,
            'example_patterns': examples,
        }
    
    # ========== Citation Helpers ==========
    
    def get_citation(self, entity_id: str) -> Optional[Citation]:
        """Get citation for an entity."""
        entity = self.get_entity(entity_id)
        if entity and 'citation' in entity:
            return Citation.from_dict(entity['citation'])
        return None
    
    def get_citations_for_query(self, query: str, top_k: int = 5) -> List[Citation]:
        """
        Get citations for entities matching a query.
        
        Useful for the LLM to retrieve source content on-demand.
        
        Args:
            query: Search query
            top_k: Maximum citations to return
            
        Returns:
            List of Citation objects
        """
        results = self.search(query, top_k=top_k)
        citations = []
        
        for result in results:
            entity = result['entity']
            if 'citation' in entity:
                citations.append(Citation.from_dict(entity['citation']))
        
        return citations
    
    def export_citations_summary(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Export a summary of all citations grouped by file.
        
        Returns:
            Dict mapping file paths to lists of entity summaries
        """
        by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        
        for node_id, data in self._graph.nodes(data=True):
            citation = data.get('citation', {})
            if isinstance(citation, dict) and citation.get('file_path'):
                by_file[citation['file_path']].append({
                    'entity_id': node_id,
                    'name': data.get('name'),
                    'type': data.get('type'),
                    'line_start': citation.get('line_start'),
                    'line_end': citation.get('line_end'),
                })
        
        # Sort entities within each file by line number
        for file_path in by_file:
            by_file[file_path].sort(key=lambda x: x.get('line_start') or 0)
        
        return dict(by_file)
