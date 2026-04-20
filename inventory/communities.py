"""
Community Detection for Knowledge Graphs.

Two-level community detection using igraph:
- Macro: Leiden algorithm for large thematic communities
- Micro: Infomap algorithm for optional sub-community detection within a macro cluster

Provides centroid identification, auto-labeling, and optional LLM summarization.
"""

import logging
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, List, Dict, Callable, Tuple

try:
    import igraph as ig
    HAS_IGRAPH = True
except ImportError:
    ig = None
    HAS_IGRAPH = False

try:
    import networkx as nx
except ImportError:
    nx = None

logger = logging.getLogger(__name__)


# ============================================================================
# Edge weight mapping by relation type category
# ============================================================================

STRUCTURAL_RELATIONS = frozenset({
    # Parser-extracted code structure
    'contains', 'extends', 'implements', 'defines', 'exports',
    'decorates', 'annotates',
    # Taxonomy: structural (non-code)
    'part_of', 'provides',
})

BEHAVIORAL_RELATIONS = frozenset({
    # Parser-extracted runtime
    'calls', 'returns',
    # Taxonomy: behavioral & events
    'triggers', 'depends_on', 'publishes', 'subscribes_to',
    # Taxonomy: data lineage
    'stores_in', 'reads_from', 'maps_to', 'transforms',
    # Taxonomy: UI/product
    'shown_on', 'navigates_to', 'validates',
    # Taxonomy: testing
    'tests', 'covers', 'reproduces',
    # Temporal with coupling implications
    'blocks',
})

SEMANTIC_RELATIONS = frozenset({
    # Parser-extracted references
    'uses', 'references', 'imports',
    # Taxonomy: semantic & knowledge
    'related_to', 'documents', 'duplicates', 'contradicts', 'synonym_of', "mentions",
    # Taxonomy: ownership
    'owned_by', 'maintained_by', 'assigned_to', 'reviewed_by',
    # Taxonomy: temporal
    'introduced_in', 'modified_in', 'removed_in', 'supersedes',
})

RELATION_TYPE_WEIGHTS = {
    'structural': 3.0,
    'behavioral': 2.0,
    'semantic': 1.0,
}

DEFAULT_EDGE_WEIGHT = 1.0

# Centroid scoring weights
CENTROID_PAGERANK_WEIGHT = 0.5
CENTROID_DEGREE_WEIGHT = 0.3
CENTROID_BETWEENNESS_WEIGHT = 0.2

# Architectural type priority for centroid boosting and display ordering.
# Higher priority types are boosted in centroid scoring and listed first.
TYPE_PRIORITY = {
    'class': 10, 'interface': 10, 'struct': 10, 'enum': 10, 'trait': 10,
    'function': 9,
    'module': 8, 'component': 8, 'service': 8,
    'constant': 7,
    'feature': 6, 'requirement': 6, 'test': 6, 'rule': 6, 'workflow': 6,
    'source_file': 5, 'document_file': 5, 'config_file': 5,
    'method': 4, 'property': 4, 'variable': 4,
    'fact': 2, 'concept': 2,
    'import': 1,
}

# Types with priority >= this threshold are considered architectural.
# They define community identity; everything below is implementation noise
# (variables, parameters, methods, imports, etc.).
ARCHITECTURAL_MIN_PRIORITY = 5

# Centroid count limits: scales with sqrt(community_size) up to MAX.
MIN_CENTROIDS = 1
MAX_CENTROIDS = 15

# Auto-labeling thresholds
TYPE_DOMINANCE_THRESHOLD = 0.6  # ≥60% same type to use type-based label

# Minimum graph size for community detection
MIN_NODES_FOR_DETECTION = 10


def _get_edge_weight(relation_type: str) -> float:
    """Map a relation type string to its edge weight."""
    rt = relation_type.lower().strip()
    if rt in STRUCTURAL_RELATIONS:
        return RELATION_TYPE_WEIGHTS['structural']
    if rt in BEHAVIORAL_RELATIONS:
        return RELATION_TYPE_WEIGHTS['behavioral']
    if rt in SEMANTIC_RELATIONS:
        return RELATION_TYPE_WEIGHTS['semantic']
    return DEFAULT_EDGE_WEIGHT


class CommunityAnalyzer:
    """
    Community detection and analysis for knowledge graphs.

    Operates on a NetworkX DiGraph and produces community metadata
    that can be stored back into the KnowledgeGraph._metadata.
    """

    def __init__(self, resolution: float = 1.0):
        """
        Args:
            resolution: Leiden resolution parameter. Higher values produce
                more (smaller) communities. Default 1.0.
        """
        self.resolution = resolution

    # ========== NetworkX → igraph Conversion ==========

    def _nx_to_igraph(self, nx_graph) -> 'ig.Graph':
        """
        Convert a NetworkX DiGraph to an igraph Graph with edge weights.

        Returns an undirected igraph graph suitable for community detection.
        The vertex attribute '_nx_name' maps back to the original node IDs.
        """
        if not HAS_IGRAPH:
            raise ImportError(
                "igraph is required for community detection. "
                "Install with: pip install igraph>=0.11.0"
            )

        ig_graph = ig.Graph.from_networkx(nx_graph)

        # Set edge weights based on relation type
        weights = []
        has_rt = 'relation_type' in ig_graph.es.attributes() if ig_graph.ecount() > 0 else False
        for edge in ig_graph.es:
            rt = edge['relation_type'] if has_rt else ''
            weights.append(_get_edge_weight(rt or ''))
        ig_graph.es['weight'] = weights

        # Convert to undirected for Leiden/Louvain (modularity needs undirected)
        ig_undirected = ig_graph.as_undirected(mode="each")
        return ig_undirected

    def _igraph_vertex_to_nx_id(self, ig_graph: 'ig.Graph', vertex_idx: int) -> str:
        """Map an igraph vertex index back to the original NetworkX node ID."""
        return ig_graph.vs[vertex_idx]['_nx_name']

    # ========== Macro Clustering (Leiden) ==========

    def detect_communities(self, nx_graph) -> Dict[str, Any]:
        """
        Run Leiden community detection on the graph.

        Falls back to Louvain (igraph) if Leiden fails,
        then to NetworkX louvain_communities if igraph is unavailable.

        Args:
            nx_graph: NetworkX DiGraph

        Returns:
            community_data dict ready to store in _metadata
        """
        node_count = nx_graph.number_of_nodes()
        if node_count < MIN_NODES_FOR_DETECTION:
            logger.info(
                f"Graph too small for community detection "
                f"({node_count} < {MIN_NODES_FOR_DETECTION} nodes)"
            )
            return {}

        if HAS_IGRAPH:
            return self._detect_with_igraph(nx_graph)
        else:
            return self._detect_with_networkx(nx_graph)

    def _detect_with_igraph(self, nx_graph) -> Dict[str, Any]:
        """Community detection using igraph (Leiden with Louvain fallback)."""
        ig_graph = self._nx_to_igraph(nx_graph)

        # Try Leiden first
        algorithm = "leiden"
        try:
            clustering = ig_graph.community_leiden(
                objective_function="modularity",
                weights="weight",
                resolution=self.resolution,
                n_iterations=-1,
            )
            modularity = clustering.modularity
            logger.info(
                f"Leiden: {len(clustering)} communities, "
                f"modularity={modularity:.4f}"
            )
        except Exception as e:
            logger.warning(f"Leiden failed ({e}), falling back to Louvain")
            algorithm = "louvain"
            try:
                clustering = ig_graph.community_multilevel(weights="weight")
                modularity = clustering.modularity
                logger.info(
                    f"Louvain: {len(clustering)} communities, "
                    f"modularity={modularity:.4f}"
                )
            except Exception as e2:
                logger.error(f"Both Leiden and Louvain failed: {e2}")
                return {}

        # Build community data
        return self._build_community_data(
            ig_graph, nx_graph, clustering, algorithm, modularity
        )

    def _detect_with_networkx(self, nx_graph) -> Dict[str, Any]:
        """Fallback: community detection using NetworkX louvain_communities."""
        if nx is None:
            return {}

        try:
            from networkx.algorithms.community import louvain_communities
        except ImportError:
            logger.warning("NetworkX louvain_communities not available")
            return {}

        try:
            undirected = nx_graph.to_undirected()
            communities = louvain_communities(
                undirected, weight='weight', resolution=self.resolution
            )
            communities = list(communities)

            # Build membership mapping
            membership = {}
            for cid, members in enumerate(communities):
                for node_id in members:
                    membership[node_id] = cid

            # Compute modularity
            modularity = nx.algorithms.community.quality.modularity(
                undirected, communities, weight='weight'
            )

            logger.info(
                f"NetworkX Louvain: {len(communities)} communities, "
                f"modularity={modularity:.4f}"
            )

            return self._build_community_data_nx(
                nx_graph, communities, membership, modularity
            )
        except Exception as e:
            logger.error(f"NetworkX community detection failed: {e}")
            return {}

    # ========== Community Data Construction ==========

    def _build_community_data(
        self,
        ig_graph: 'ig.Graph',
        nx_graph,
        clustering,
        algorithm: str,
        modularity: float,
    ) -> Dict[str, Any]:
        """Build community_data from igraph clustering result."""
        communities = {}

        # Pre-compute global centrality arrays once (O(V+E) each)
        # instead of recomputing per community.
        try:
            pagerank = ig_graph.pagerank(weights="weight")
        except Exception:
            pagerank = [1.0 / ig_graph.vcount()] * ig_graph.vcount()

        # Betweenness interprets weights as distances/costs (larger = farther),
        # but our edge "weight" encodes relationship strength (larger = closer).
        # Convert to inverse distances so stronger edges = shorter paths.
        try:
            bt_distances = []
            for e in ig_graph.es:
                w = e["weight"] if "weight" in e.attributes() else None
                if isinstance(w, (int, float)) and w > 0:
                    bt_distances.append(1.0 / w)
                else:
                    bt_distances.append(1.0)
            betweenness = ig_graph.betweenness(weights=bt_distances)
        except Exception:
            betweenness = [0.0] * ig_graph.vcount()

        degree = ig_graph.strength(weights="weight")

        for cid, members_ig in enumerate(clustering):
            if not members_ig:
                continue

            community_id = f"community_{cid}"
            member_nx_ids = [
                self._igraph_vertex_to_nx_id(ig_graph, v) for v in members_ig
            ]

            # Compute centroids using pre-computed centrality arrays
            centroids = self._compute_centroids_igraph(
                ig_graph, nx_graph, members_ig, member_nx_ids,
                pagerank=pagerank, betweenness=betweenness, degree=degree,
            )

            # Compute statistics
            stats = self._compute_stats(nx_graph, member_nx_ids)

            # Entity type and layer distribution
            dominant_types = self._get_dominant_types(nx_graph, member_nx_ids)
            dominant_layers = self._get_dominant_layers(nx_graph, member_nx_ids)

            # Auto-label
            label = self._auto_label(
                nx_graph, member_nx_ids, centroids, dominant_types, dominant_layers
            )

            communities[community_id] = {
                "members": member_nx_ids,
                "centroids": centroids,
                "stats": stats,
                "label": label,
                "summary": None,
                "dominant_types": [t for t, _ in dominant_types],
                "dominant_layers": [l for l, _ in dominant_layers],
                "micro_clusters": None,
            }

        return {
            "algorithm": algorithm,
            "resolution": self.resolution,
            "modularity": modularity,
            "num_communities": len(communities),
            "communities": communities,
        }

    def _build_community_data_nx(
        self,
        nx_graph,
        communities_list: list,
        membership: Dict[str, int],
        modularity: float,
    ) -> Dict[str, Any]:
        """Build community_data from NetworkX louvain result (fallback)."""
        communities = {}

        for cid, member_set in enumerate(communities_list):
            if not member_set:
                continue

            community_id = f"community_{cid}"
            member_nx_ids = list(member_set)

            centroids = self._compute_centroids_nx(nx_graph, member_nx_ids)
            stats = self._compute_stats(nx_graph, member_nx_ids)
            dominant_types = self._get_dominant_types(nx_graph, member_nx_ids)
            dominant_layers = self._get_dominant_layers(nx_graph, member_nx_ids)
            label = self._auto_label(
                nx_graph, member_nx_ids, centroids, dominant_types, dominant_layers
            )

            communities[community_id] = {
                "members": member_nx_ids,
                "centroids": centroids,
                "stats": stats,
                "label": label,
                "summary": None,
                "dominant_types": [t for t, _ in dominant_types],
                "dominant_layers": [l for l, _ in dominant_layers],
                "micro_clusters": None,
            }

        return {
            "algorithm": "louvain_nx",
            "resolution": self.resolution,
            "modularity": modularity,
            "num_communities": len(communities),
            "communities": communities,
        }

    # ========== Centroid Identification ==========

    def _compute_centroids_igraph(
        self,
        ig_graph: 'ig.Graph',
        nx_graph,
        members_ig: List[int],
        members_nx: List[str],
        top_k: Optional[int] = None,
        pagerank: Optional[List[float]] = None,
        betweenness: Optional[List[float]] = None,
        degree: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Identify centroid entities in a community using igraph centrality.

        Centroids are selected exclusively from architectural members
        (priority >= ARCHITECTURAL_MIN_PRIORITY).  Only falls back to
        all members when the community contains zero architectural symbols.

        Composite score: 50% PageRank + 30% degree + 20% betweenness.

        Pre-computed centrality arrays can be passed to avoid redundant
        O(V+E) computations when called in a per-community loop.
        """
        if not members_ig:
            return []

        k = top_k or min(MAX_CENTROIDS, max(MIN_CENTROIDS, round(math.sqrt(len(members_ig)))))

        # Use pre-computed arrays or compute on demand (single-community call)
        if pagerank is None:
            try:
                pagerank = ig_graph.pagerank(weights="weight")
            except Exception:
                pagerank = [1.0 / ig_graph.vcount()] * ig_graph.vcount()
        if betweenness is None:
            try:
                bt_distances = []
                for e in ig_graph.es:
                    w = e["weight"] if "weight" in e.attributes() else None
                    if isinstance(w, (int, float)) and w > 0:
                        bt_distances.append(1.0 / w)
                    else:
                        bt_distances.append(1.0)
                betweenness = ig_graph.betweenness(weights=bt_distances)
            except Exception:
                betweenness = [0.0] * ig_graph.vcount()
        if degree is None:
            degree = ig_graph.strength(weights="weight")

        # Extract scores for community members and normalize
        pr_scores = [pagerank[v] for v in members_ig]
        bt_scores = [betweenness[v] for v in members_ig]
        dg_scores = [degree[v] for v in members_ig]

        pr_norm = _normalize_scores(pr_scores)
        bt_norm = _normalize_scores(bt_scores)
        dg_norm = _normalize_scores(dg_scores)

        # Score all members by pure centrality
        scored = []
        for i, v_ig in enumerate(members_ig):
            composite = (
                CENTROID_PAGERANK_WEIGHT * pr_norm[i]
                + CENTROID_DEGREE_WEIGHT * dg_norm[i]
                + CENTROID_BETWEENNESS_WEIGHT * bt_norm[i]
            )
            nx_id = members_nx[i]
            node_data = nx_graph.nodes.get(nx_id, {})
            etype = node_data.get("type", "unknown").lower()
            scored.append({
                "id": nx_id,
                "score": composite,
                "name": node_data.get("name", nx_id),
                "type": node_data.get("type", "unknown"),
                "_priority": TYPE_PRIORITY.get(etype, 0),
            })

        # Select centroids from architectural members only
        arch_candidates = [
            s for s in scored
            if s["_priority"] >= ARCHITECTURAL_MIN_PRIORITY
        ]
        pool = arch_candidates if arch_candidates else scored
        pool.sort(key=lambda x: x["score"], reverse=True)
        result = pool[:k]

        # Re-normalize so scores stay in [0, 1]
        max_score = max((s["score"] for s in result), default=1.0) or 1.0
        for s in result:
            s["score"] = round(s["score"] / max_score, 4)
            del s["_priority"]

        return result

    def _compute_centroids_nx(
        self,
        nx_graph,
        members_nx: List[str],
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fallback centroid computation using NetworkX.

        Same architectural-first filtering as the igraph path.
        """
        if not members_nx:
            return []

        k = top_k or min(MAX_CENTROIDS, max(MIN_CENTROIDS, round(math.sqrt(len(members_nx)))))
        member_set = set(members_nx)
        subgraph = nx_graph.subgraph(member_set)

        try:
            pagerank = nx.pagerank(subgraph, weight='weight')
        except Exception:
            pagerank = {n: 1.0 / len(member_set) for n in member_set}

        # NX betweenness also treats weight as distance/cost.
        # Invert so stronger edges = shorter paths.
        try:
            inv_graph = subgraph.copy()
            for u, v, data in inv_graph.edges(data=True):
                w = data.get('weight', 1.0)
                data['weight'] = 1.0 / w if isinstance(w, (int, float)) and w > 0 else 1.0
            betweenness = nx.betweenness_centrality(inv_graph, weight='weight')
        except Exception:
            betweenness = {n: 0.0 for n in member_set}

        degree = dict(subgraph.degree(weight='weight'))

        pr_vals = [pagerank.get(n, 0) for n in members_nx]
        bt_vals = [betweenness.get(n, 0) for n in members_nx]
        dg_vals = [degree.get(n, 0) for n in members_nx]

        pr_norm = _normalize_scores(pr_vals)
        bt_norm = _normalize_scores(bt_vals)
        dg_norm = _normalize_scores(dg_vals)

        scored = []
        for i, nx_id in enumerate(members_nx):
            composite = (
                CENTROID_PAGERANK_WEIGHT * pr_norm[i]
                + CENTROID_DEGREE_WEIGHT * dg_norm[i]
                + CENTROID_BETWEENNESS_WEIGHT * bt_norm[i]
            )
            node_data = nx_graph.nodes.get(nx_id, {})
            etype = node_data.get("type", "unknown").lower()
            scored.append({
                "id": nx_id,
                "score": composite,
                "name": node_data.get("name", nx_id),
                "type": node_data.get("type", "unknown"),
                "_priority": TYPE_PRIORITY.get(etype, 0),
            })

        # Select centroids from architectural members only
        arch_candidates = [
            s for s in scored
            if s["_priority"] >= ARCHITECTURAL_MIN_PRIORITY
        ]
        pool = arch_candidates if arch_candidates else scored
        pool.sort(key=lambda x: x["score"], reverse=True)
        result = pool[:k]

        # Re-normalize so scores stay in [0, 1]
        max_score = max((s["score"] for s in result), default=1.0) or 1.0
        for s in result:
            s["score"] = round(s["score"] / max_score, 4)
            del s["_priority"]

        return result

    # ========== Statistics ==========

    def _compute_stats(
        self, nx_graph, member_ids: List[str]
    ) -> Dict[str, Any]:
        """Compute community statistics."""
        member_set = set(member_ids)
        size = len(member_set)

        if size <= 1:
            return {"size": size, "density": 0.0, "cohesion": 0.0}

        # Count internal vs external edges
        # Iterate only over edges incident to community members
        # instead of scanning the full graph.
        internal_edges = 0
        internal_weight = 0.0
        total_weight = 0.0

        for u, v, data in nx_graph.edges(member_set, data=True):
            w = data.get('weight', _get_edge_weight(data.get('relation_type', '')))
            total_weight += w
            if u in member_set and v in member_set:
                internal_edges += 1
                internal_weight += w

        possible_edges = size * (size - 1)  # directed
        density = internal_edges / possible_edges if possible_edges > 0 else 0.0
        cohesion = internal_weight / total_weight if total_weight > 0 else 0.0

        return {
            "size": size,
            "density": round(density, 4),
            "cohesion": round(cohesion, 4),
            "internal_edges": internal_edges,
        }

    # ========== Type & Layer Distribution ==========

    def _get_dominant_types(
        self, nx_graph, member_ids: List[str], top_n: int = 3
    ) -> List[tuple]:
        """Return top-N *architectural* entity types that define community identity.

        Filters out implementation-noise types (variable, parameter, method,
        import, etc.) whose counts always dwarf architectural symbols.  Only
        falls back to noise types when zero architectural types are present.
        """
        counter = Counter()
        for nid in member_ids:
            t = nx_graph.nodes.get(nid, {}).get('type', 'unknown')
            counter[t] += 1

        # Split into architectural (priority >= threshold) and noise
        arch_items = [
            (t, c) for t, c in counter.items()
            if TYPE_PRIORITY.get(t.lower(), 0) >= ARCHITECTURAL_MIN_PRIORITY
        ]
        noise_items = [
            (t, c) for t, c in counter.items()
            if TYPE_PRIORITY.get(t.lower(), 0) < ARCHITECTURAL_MIN_PRIORITY
        ]

        # Sort each bucket: priority desc, then count desc
        arch_items.sort(
            key=lambda x: (TYPE_PRIORITY.get(x[0].lower(), 0), x[1]),
            reverse=True,
        )
        noise_items.sort(
            key=lambda x: (TYPE_PRIORITY.get(x[0].lower(), 0), x[1]),
            reverse=True,
        )

        # Prefer architectural; only fall back to noise if none exist
        result = arch_items if arch_items else noise_items
        return result[:top_n]

    def _get_dominant_layers(
        self, nx_graph, member_ids: List[str], top_n: int = 2
    ) -> List[tuple]:
        """Return top-N entity layers in the community."""
        counter = Counter()
        for nid in member_ids:
            layer = nx_graph.nodes.get(nid, {}).get('layer', '')
            if layer:
                counter[layer] += 1
        return counter.most_common(top_n)

    # ========== Auto-Labeling ==========

    def _auto_label(
        self,
        nx_graph,
        member_ids: List[str],
        centroids: List[Dict],
        dominant_types: List[tuple],
        dominant_layers: List[tuple],
    ) -> str:
        """Generate a human-readable label for a community.

        Uses *architectural* dominant types (already filtered by
        ``_get_dominant_types``) to describe the community identity.
        Noise types (variable, method, import) never appear in labels.
        """
        if not centroids:
            return "Empty community"

        top_centroid_name = centroids[0]["name"]

        # Count only architectural members for dominance check
        arch_count = sum(
            1 for nid in member_ids
            if TYPE_PRIORITY.get(
                nx_graph.nodes.get(nid, {}).get('type', 'unknown').lower(), 0
            ) >= ARCHITECTURAL_MIN_PRIORITY
        )

        # Check for type dominance among architectural symbols
        if dominant_types and arch_count > 0:
            top_type, top_count = dominant_types[0]
            if top_count / arch_count >= TYPE_DOMINANCE_THRESHOLD:
                centroid_names = ", ".join(c["name"] for c in centroids[:3])
                return f"{top_type.capitalize()} cluster: {centroid_names}"

        # Check for documentation dominance
        if dominant_layers:
            top_layer, _ = dominant_layers[0]
            if top_layer == "documentation":
                centroid_names = ", ".join(c["name"] for c in centroids[:3])
                return f"Documentation: {centroid_names}"

        # Default: top centroid + architectural type
        dt = dominant_types[0][0] if dominant_types else "entities"
        return f"{top_centroid_name} & related ({dt})"

    # ========== Micro Clustering (Infomap) ==========

    def detect_micro_clusters(
        self,
        nx_graph,
        community_data: Dict[str, Any],
        community_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Infomap micro-clustering within a single macro community.

        This is an on-demand operation, NOT run during standard ingestion.

        Args:
            nx_graph: The full NetworkX DiGraph
            community_data: The community_data dict from _metadata
            community_id: Which macro community to subdivide

        Returns:
            Dict of micro_clusters or None if not applicable
        """
        if not HAS_IGRAPH:
            logger.warning("igraph required for micro-clustering")
            return None

        community = community_data.get("communities", {}).get(community_id)
        if not community:
            logger.warning(f"Community {community_id} not found")
            return None

        member_ids = community["members"]
        if len(member_ids) < 5:
            logger.info(f"Community {community_id} too small for micro-clustering")
            return None

        # Extract subgraph and convert to igraph
        subgraph = nx_graph.subgraph(member_ids).copy()
        ig_sub = self._nx_to_igraph(subgraph)

        try:
            clustering = ig_sub.community_infomap(
                edge_weights="weight", trials=10
            )
        except Exception as e:
            logger.warning(f"Infomap micro-clustering failed: {e}")
            return None

        if len(clustering) <= 1:
            return None

        micro_clusters = {}
        for mid, members_ig in enumerate(clustering):
            if not members_ig:
                continue

            micro_id = f"micro_{mid}"
            members_nx = [
                self._igraph_vertex_to_nx_id(ig_sub, v) for v in members_ig
            ]

            centroids = self._compute_centroids_igraph(
                ig_sub, nx_graph, members_ig, members_nx, top_k=3
            )
            label = self._auto_label(
                nx_graph,
                members_nx,
                centroids,
                self._get_dominant_types(nx_graph, members_nx),
                self._get_dominant_layers(nx_graph, members_nx),
            )

            micro_clusters[micro_id] = {
                "members": members_nx,
                "centroids": centroids,
                "label": label,
            }

        logger.info(
            f"Infomap: {len(micro_clusters)} micro-clusters in {community_id}"
        )
        return micro_clusters

    # ========== LLM Labels (Optional) ==========

    def generate_labels(
        self,
        nx_graph,
        community_data: Dict[str, Any],
        llm_callable: Callable[[str], str],
        max_workers: int = 5,
    ) -> int:
        """
        Generate LLM-driven capability labels for each community in parallel.

        Replaces heuristic auto-labels with concise intent/capability names
        produced by the LLM.  Uses the same centroid + relationship context as
        summaries.  Should be called *before* ``generate_summaries`` so the
        improved labels feed into the summary prompts.

        Args:
            nx_graph: The full NetworkX DiGraph
            community_data: Mutated in-place — ``label`` fields updated
            llm_callable: ``str → str`` (prompt in, text out)
            max_workers: Maximum parallel LLM calls

        Returns:
            Number of labels successfully generated
        """
        communities = community_data.get("communities", {})
        if not communities:
            return 0

        tasks: List[Tuple[str, str]] = []
        for cid, community in communities.items():
            prompt = self._build_label_prompt(nx_graph, community)
            tasks.append((cid, prompt))

        def _label(item: Tuple[str, str]) -> Tuple[str, Optional[str]]:
            cid, prompt = item
            try:
                raw = llm_callable(prompt)
                # Clean LLM output: strip quotes, collapse whitespace, strip trailing punctuation
                label = raw.strip().strip('"\'').strip()
                # Collapse multiple internal whitespace characters
                label = " ".join(label.split())
                # Enforce reasonable length — truncate if LLM went verbose
                if len(label) > 80:
                    label = label[:80]
                # Strip common trailing punctuation to align with "no punctuation" contract
                label = label.rstrip('.,;:!?\'"\u2026\u00bb\u00ab)]}')
                return (cid, label)
            except Exception as e:
                logger.warning(f"Label generation failed for {cid}: {e}")
                return (cid, None)

        count = 0
        effective_workers = min(max_workers, len(tasks))
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {executor.submit(_label, t): t[0] for t in tasks}
            for future in as_completed(futures):
                cid, label = future.result()
                if label:
                    communities[cid]["label"] = label
                    count += 1

        logger.info(f"Generated {count}/{len(tasks)} community labels via LLM")
        return count

    def _build_label_prompt(
        self,
        nx_graph,
        community: Dict[str, Any],
    ) -> str:
        """Build prompt for LLM-driven community label generation."""
        centroids = community.get("centroids", [])
        members = community.get("members", [])
        heuristic_label = community.get("label", "Unknown")

        # Centroid details (same as summary prompt)
        centroid_lines = []
        for c in centroids:
            node = nx_graph.nodes.get(c["id"], {})
            sig = node.get("signature", "")
            doc = node.get("docstring", "") or node.get("description", "")
            line = f"- {c['name']} ({c['type']})"
            if sig:
                line += f": {sig}"
            if doc:
                doc_short = doc[:120].strip()
                if len(doc) > 120:
                    doc_short += "..."
                line += f" — {doc_short}"
            centroid_lines.append(line)

        # Key relationships for centroids
        relation_lines = []
        centroid_ids = {c["id"] for c in centroids}
        for cid_node in centroid_ids:
            for _, target, data in nx_graph.out_edges(cid_node, data=True):
                rt = data.get("relation_type", "related_to")
                target_name = nx_graph.nodes.get(target, {}).get("name", target)
                relation_lines.append(
                    f"- {nx_graph.nodes.get(cid_node, {}).get('name', cid_node)} "
                    f"--[{rt}]--> {target_name}"
                )
            for source, _, data in nx_graph.in_edges(cid_node, data=True):
                rt = data.get("relation_type", "related_to")
                source_name = nx_graph.nodes.get(source, {}).get("name", source)
                relation_lines.append(
                    f"- {source_name} --[{rt}]--> "
                    f"{nx_graph.nodes.get(cid_node, {}).get('name', cid_node)}"
                )
        relation_lines = list(dict.fromkeys(relation_lines))[:10]

        # Architectural members only (skip noise types)
        non_centroid = [m for m in members if m not in centroid_ids]
        arch_members = [
            m for m in non_centroid
            if TYPE_PRIORITY.get(
                nx_graph.nodes.get(m, {}).get('type', 'unknown').lower(), 0
            ) >= ARCHITECTURAL_MIN_PRIORITY
        ]
        roster_lines = []
        for nid in arch_members[:15]:
            node = nx_graph.nodes.get(nid, {})
            roster_lines.append(
                f"- {node.get('name', nid)} ({node.get('type', 'unknown')})"
            )
        if len(arch_members) > 15:
            roster_lines.append(f"- ... and {len(arch_members) - 15} more")

        prompt = (
            "Given this code community, generate a short, descriptive label "
            "(3-7 words) that captures its primary capability or responsibility.\n\n"
            "## Key Entities (Centroids)\n"
            + ("\n".join(centroid_lines) if centroid_lines else "- None")
            + "\n\n## Key Relationships\n"
            + ("\n".join(relation_lines) if relation_lines else "- None")
            + "\n\n## Other Architectural Members\n"
            + ("\n".join(roster_lines) if roster_lines else "- None")
            + f"\n\nCurrent heuristic label: \"{heuristic_label}\"\n\n"
            "Reply with ONLY the label — no quotes, no explanation, no punctuation. "
            "Examples of good labels:\n"
            "- Authentication & Session Management\n"
            "- REST API Request Handling\n"
            "- Database Connection Pooling\n"
            "- UI Component Rendering Pipeline\n"
            "- Test Infrastructure & Fixtures\n"
        )
        return prompt

    # ========== LLM Summaries (Optional) ==========

    def generate_summaries(
        self,
        nx_graph,
        community_data: Dict[str, Any],
        llm_callable: Callable[[str], str],
        max_tokens: int = 200,
        max_workers: int = 5,
    ) -> int:
        """
        Generate LLM summaries for each community in parallel.

        Args:
            nx_graph: The full NetworkX DiGraph
            community_data: The community_data dict (modified in-place)
            llm_callable: Function that takes a prompt string and returns a response
            max_tokens: Approximate token cap for summaries
            max_workers: Maximum parallel LLM calls (default: 5)

        Returns:
            Number of summaries generated
        """
        communities = community_data.get("communities", {})
        if not communities:
            return 0

        # Build all prompts upfront (read-only on the graph, safe to prepare here)
        tasks: List[Tuple[str, str]] = []  # (community_id, prompt)
        for cid, community in communities.items():
            prompt = self._build_summary_prompt(nx_graph, community, max_tokens)
            tasks.append((cid, prompt))

        def _summarise(item: Tuple[str, str]) -> Tuple[str, Optional[str]]:
            cid, prompt = item
            try:
                summary = llm_callable(prompt)
                return (cid, summary.strip())
            except Exception as e:
                logger.warning(f"Summary generation failed for {cid}: {e}")
                return (cid, None)

        count = 0
        effective_workers = min(max_workers, len(tasks))
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {executor.submit(_summarise, t): t[0] for t in tasks}
            for future in as_completed(futures):
                cid, summary = future.result()
                communities[cid]["summary"] = summary
                if summary is not None:
                    count += 1

        logger.info(f"Generated {count}/{len(tasks)} community summaries")
        return count

    def _build_summary_prompt(
        self,
        nx_graph,
        community: Dict[str, Any],
        max_tokens: int,
    ) -> str:
        """Build the LLM prompt for community summarization."""
        centroids = community.get("centroids", [])
        members = community.get("members", [])
        label = community.get("label", "Unknown")

        # Centroid details
        centroid_lines = []
        for c in centroids:
            node = nx_graph.nodes.get(c["id"], {})
            sig = node.get("signature", "")
            doc = node.get("docstring", "") or node.get("description", "")
            line = f"- {c['name']} ({c['type']})"
            if sig:
                line += f": {sig}"
            if doc:
                # Truncate long docstrings
                doc_short = doc[:150].strip()
                if len(doc) > 150:
                    doc_short += "..."
                line += f" — {doc_short}"
            centroid_lines.append(line)

        # Centroid relations
        relation_lines = []
        centroid_ids = {c["id"] for c in centroids}
        for cid_node in centroid_ids:
            for _, target, data in nx_graph.out_edges(cid_node, data=True):
                rt = data.get("relation_type", "related_to")
                target_name = nx_graph.nodes.get(target, {}).get("name", target)
                relation_lines.append(
                    f"- {nx_graph.nodes.get(cid_node, {}).get('name', cid_node)} "
                    f"--[{rt}]--> {target_name}"
                )
            for source, _, data in nx_graph.in_edges(cid_node, data=True):
                rt = data.get("relation_type", "related_to")
                source_name = nx_graph.nodes.get(source, {}).get("name", source)
                relation_lines.append(
                    f"- {source_name} --[{rt}]--> "
                    f"{nx_graph.nodes.get(cid_node, {}).get('name', cid_node)}"
                )
        # Deduplicate and limit
        relation_lines = list(dict.fromkeys(relation_lines))[:15]

        # Member roster: only architectural members (priority >= threshold)
        # Noise types (variable, parameter, method, import) add nothing to
        # LLM understanding and waste tokens.
        non_centroid = [m for m in members if m not in centroid_ids]
        arch_members = [
            m for m in non_centroid
            if TYPE_PRIORITY.get(
                nx_graph.nodes.get(m, {}).get('type', 'unknown').lower(), 0
            ) >= ARCHITECTURAL_MIN_PRIORITY
        ]
        noise_count = len(non_centroid) - len(arch_members)

        roster_lines = []
        for nid in arch_members[:20]:
            node = nx_graph.nodes.get(nid, {})
            roster_lines.append(
                f"- {node.get('name', nid)} ({node.get('type', 'unknown')})"
            )
        if len(arch_members) > 20:
            roster_lines.append(f"- ... and {len(arch_members) - 20} more architectural members")
        if noise_count > 0:
            roster_lines.append(f"- ({noise_count} implementation-level members omitted)")

        prompt = (
            f"Summarize this code community labeled '{label}' in 5-8 sentences "
            f"(~{max_tokens} tokens max).\n\n"
            f"## Key Entities (Centroids)\n"
            + "\n".join(centroid_lines)
            + "\n\n## Key Relationships\n"
            + ("\n".join(relation_lines) if relation_lines else "- None captured")
            + "\n\n## Other Members\n"
            + ("\n".join(roster_lines) if roster_lines else "- None")
            + "\n\nDescribe the community's purpose, main responsibilities, "
            "and how the centroids relate to each other."
        )
        return prompt


# ============================================================================
# Utility functions
# ============================================================================

def _normalize_scores(scores: List[float]) -> List[float]:
    """Min-max normalize a list of scores to [0, 1]."""
    if not scores:
        return []
    mn = min(scores)
    mx = max(scores)
    rng = mx - mn
    if rng == 0:
        return [1.0 / len(scores)] * len(scores)
    return [(s - mn) / rng for s in scores]
