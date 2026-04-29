import { useEffect, useRef, useCallback, useState, useImperativeHandle, forwardRef } from 'react';
import { Box, Typography, Paper } from '@mui/material';
import cytoscape from 'cytoscape';
import coseBilkent from 'cytoscape-cose-bilkent';
import NodeContextMenu from './NodeContextMenu';

// Register the layout extension (only once)
if (!cytoscape.prototype._coseBilkentRegistered) {
  cytoscape.use(coseBilkent);
  cytoscape.prototype._coseBilkentRegistered = true;
}

// Entity type colors - exported for use in filters
// Comprehensive palette covering all normalized entity types
export const typeColors = {
  // === Code Structure (Blues/Cyans) ===
  class: '#2196F3',        // Blue
  function: '#1976D2',     // Dark Blue
  method: '#03A9F4',       // Light Blue
  module: '#0288D1',       // Blue 700
  interface: '#00ACC1',    // Cyan 600
  component: '#0097A7',    // Cyan 700
  field: '#4FC3F7',        // Light Blue 300
  property: '#29B6F6',     // Light Blue 400
  variable: '#81D4FA',     // Light Blue 200
  constant: '#0D47A1',     // Blue 900
  parameter: '#64B5F6',    // Blue 300
  import: '#5C6BC0',       // Indigo 400
  export: '#7986CB',       // Indigo 300
  enum: '#3949AB',         // Indigo 600

  // === Files & Packages (Oranges/Browns) ===
  file: '#FF9800',         // Orange
  source_file: '#F57C00',  // Orange 700
  package: '#795548',      // Brown

  // === Services & APIs (Pinks/Reds) ===
  service: '#E91E63',      // Pink
  api: '#AD1457',          // Pink 800
  rest_api: '#C2185B',     // Pink 700
  endpoint: '#D81B60',     // Pink 600
  integration: '#EC407A',  // Pink 400

  // === Data (Greens) ===
  database: '#388E3C',     // Green 700
  table: '#4CAF50',        // Green
  schema: '#66BB6A',       // Green 400
  data: '#81C784',         // Green 300

  // === Features & Requirements (Purples) ===
  feature: '#9C27B0',      // Purple
  requirement: '#7B1FA2',  // Purple 700
  user_story: '#8E24AA',   // Purple 600
  epic: '#6A1B9A',         // Purple 800
  capability: '#AB47BC',   // Purple 400

  // === Tools & Toolkits (Teals) ===
  tool: '#009688',         // Teal
  toolkit: '#00796B',      // Teal 700
  mcp_server: '#00695C',   // Teal 800
  mcp_tool: '#26A69A',     // Teal 400

  // === Knowledge & Facts (Ambers/Yellows) ===
  fact: '#FFC107',         // Amber
  concept: '#FFB300',      // Amber 600
  rule: '#FF8F00',         // Amber 800
  business_rule: '#FFA000', // Amber 700

  // === Testing (Light Greens/Limes) ===
  test: '#8BC34A',         // Light Green
  test_case: '#7CB342',    // Light Green 600

  // === Documentation & Config (Blue Grays) ===
  configuration: '#607D8B', // Blue Gray
  config: '#546E7A',       // Blue Gray 600
  documentation: '#78909C', // Blue Gray 400

  // === Process & Workflow (Deep Oranges) ===
  process: '#FF5722',      // Deep Orange
  workflow: '#E64A19',     // Deep Orange 700

  // === UI & Interface (Light Blues) ===
  ui_component: '#B3E5FC', // Light Blue 100
  ui_element: '#E1F5FE',   // Light Blue 50

  // === Issues & Todos (Reds) ===
  todo: '#F44336',         // Red
  error_handling: '#D32F2F', // Red 700
  issue: '#E53935',        // Red 600

  // === Misc ===
  unknown: '#9E9E9E',      // Gray
  default: '#9E9E9E',      // Gray (fallback)
};

// Layer colors
const layerColors = {
  code: '#2196F3',
  service: '#E91E63',
  data: '#4CAF50',
  product: '#FF9800',
  domain: '#9C27B0',
};

const GraphView = forwardRef(function GraphView({ data, onNodeSelect, onExpandNode, selectedNode, highlightedNodes = [], theme = 'light', filters = {} }, ref) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [layoutRunning, setLayoutRunning] = useState(false);
  const [contextMenu, setContextMenu] = useState({ open: false, position: null, node: null });

  // Expose methods to parent via ref
  useImperativeHandle(ref, () => ({
    zoomIn: () => cyRef.current?.zoom(cyRef.current.zoom() * 1.2),
    zoomOut: () => cyRef.current?.zoom(cyRef.current.zoom() / 1.2),
    fit: () => cyRef.current?.fit(undefined, 50),
    relayout: () => runLayout(),
    reset: () => {
      if (cyRef.current) {
        cyRef.current.elements().remove();
        onNodeSelect?.(null);
      }
    },
  }));

  const runLayout = useCallback(() => {
    if (!cyRef.current) return;
    const cy = cyRef.current;
    if (cy.nodes().length === 0) {
      setLayoutRunning(false);
      return;
    }

    setLayoutRunning(true);

    // Use grid layout for isolated nodes (no edges), otherwise use force-directed
    const hasEdges = cy.edges().length > 0;
    const layoutConfig = hasEdges ? {
      name: 'cose-bilkent',
      animate: false,
      randomize: true,
      nodeDimensionsIncludeLabels: true,
      idealEdgeLength: 100,
      nodeRepulsion: 8000,
      gravity: 0.25,
      numIter: 2500,
    } : {
      name: 'grid',
      animate: false,
      rows: Math.ceil(Math.sqrt(cy.nodes().length)),
      cols: Math.ceil(Math.sqrt(cy.nodes().length)),
      padding: 50,
    };

    const layout = cy.layout(layoutConfig);

    // Set up a timeout fallback in case layoutstop doesn't fire
    const timeoutId = setTimeout(() => {
      setLayoutRunning(false);
      cy.fit(undefined, 50);
    }, 5000);

    layout.promiseOn('layoutstop').then(() => {
      clearTimeout(timeoutId);
      setLayoutRunning(false);
      cy.fit(undefined, 50);
    }).catch(() => {
      clearTimeout(timeoutId);
      setLayoutRunning(false);
    });

    layout.run();
  }, []);

  // Convert data to Cytoscape format
  const convertToCytoscape = useCallback((graphData, activeFilters) => {
    if (!graphData) return { nodes: [], edges: [] };

    const nodes = [];
    const edges = [];
    const nodeIds = new Set();

    // Get active node type filters
    const activeNodeTypes = activeFilters?.nodeTypes || [];
    const activeEdgeTypes = activeFilters?.edgeTypes || [];
    const hasNodeTypeFilter = activeNodeTypes.length > 0;
    const hasEdgeTypeFilter = activeEdgeTypes.length > 0;

    // Add nodes from results/entities
    const entities = graphData.results || graphData.entities || [];
    entities.forEach((item) => {
      const entity = item.entity || item;
      if (!entity.id) return;

      const entityType = (entity.type || 'default').toLowerCase();

      // Apply node type filter
      if (hasNodeTypeFilter && !activeNodeTypes.includes(entityType)) {
        return;
      }

      nodeIds.add(entity.id);
      const layer = entity.layer || 'code';

      nodes.push({
        data: {
          id: entity.id,
          label: entity.name || entity.id,
          type: entityType,
          layer: layer,
          color: typeColors[entityType] || typeColors.default,
          borderColor: layerColors[layer] || layerColors.code,
          ...entity,
        },
      });
    });

    // Add edges
    const graphEdges = graphData.edges || [];
    graphEdges.forEach((edge, index) => {
      const sourceId = edge.source;
      const targetId = edge.target;
      const edgeType = (edge.type || edge.relation_type || '').toLowerCase();

      // Apply edge type filter
      if (hasEdgeTypeFilter && !activeEdgeTypes.includes(edgeType)) {
        return;
      }

      // Only add edge if both nodes exist
      if (nodeIds.has(sourceId) && nodeIds.has(targetId)) {
        edges.push({
          data: {
            id: `e${index}`,
            source: sourceId,
            target: targetId,
            label: edge.type || edge.relation_type || '',
            ...edge,
          },
        });
      }
    });

    return { nodes, edges };
  }, []);

  // Initialize Cytoscape
  useEffect(() => {
    if (!containerRef.current) return;

    const isDark = theme === 'dark';

    const cy = cytoscape({
      container: containerRef.current,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            'border-width': 3,
            'border-color': 'data(borderColor)',
            label: 'data(label)',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'font-size': '10px',
            color: isDark ? '#FFFFFF' : '#333',
            'text-background-color': isDark ? '#181F2A' : '#fff',
            'text-background-opacity': 0.7,
            'text-background-padding': '2px',
            width: 30,
            height: 30,
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 4,
            'border-color': '#ff5722',
            'background-color': '#ff5722',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'line-color': isDark ? '#3B3E46' : '#ccc',
            'target-arrow-color': isDark ? '#3B3E46' : '#ccc',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': '8px',
            color: isDark ? '#A9B7C1' : '#666',
            'text-rotation': 'autorotate',
            'text-background-color': isDark ? '#181F2A' : '#fff',
            'text-background-opacity': 0.7,
          },
        },
        {
          selector: 'edge:selected',
          style: {
            width: 2.5,
            'line-color': '#ff5722',
            'target-arrow-color': '#ff5722',
          },
        },
        // Highlighted nodes (from chat)
        {
          selector: 'node.highlighted',
          style: {
            'border-width': 5,
            'border-color': '#6ae8fa', // EliteaUI primary for highlighting
            'box-shadow': '0 0 10px #6ae8fa',
            width: 40,
            height: 40,
            'z-index': 999,
          },
        },
        // Dimmed nodes (not highlighted when others are)
        {
          selector: 'node.dimmed',
          style: {
            opacity: 0.3,
          },
        },
        {
          selector: 'edge.dimmed',
          style: {
            opacity: 0.15,
          },
        },
      ],
      layout: { name: 'preset' },
      minZoom: 0.1,
      maxZoom: 3,
    });

    // Handle node selection
    cy.on('tap', 'node', (evt) => {
      const node = evt.target;
      if (onNodeSelect) {
        onNodeSelect(node.data());
      }
      // Close context menu on left click
      setContextMenu({ open: false, position: null, node: null });
    });

    // Handle background tap to deselect
    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        if (onNodeSelect) {
          onNodeSelect(null);
        }
        // Close context menu when clicking background
        setContextMenu({ open: false, position: null, node: null });
      }
    });

    // Handle right-click on node (context menu)
    cy.on('cxttap', 'node', (evt) => {
      const node = evt.target;
      const nodeData = node.data();

      // Get the position in screen coordinates
      const renderedPosition = node.renderedPosition();
      const containerRect = containerRef.current?.getBoundingClientRect();

      if (containerRect) {
        // Calculate absolute position on screen
        const x = containerRect.left + renderedPosition.x;
        const y = containerRect.top + renderedPosition.y;

        // Adjust to prevent menu going off screen
        const menuWidth = 200;
        const menuHeight = 180;
        const adjustedX = Math.min(x, window.innerWidth - menuWidth - 10);
        const adjustedY = Math.min(y, window.innerHeight - menuHeight - 10);

        setContextMenu({
          open: true,
          position: { x: adjustedX, y: adjustedY },
          node: nodeData,
        });
      }
    });

    // Close context menu on background right-click
    cy.on('cxttap', (evt) => {
      if (evt.target === cy) {
        setContextMenu({ open: false, position: null, node: null });
      }
    });

    cyRef.current = cy;

    return () => {
      try {
        cy.destroy();
      } catch (e) {
        // Ignore destroy errors (can occur during React StrictMode double-renders)
        console.warn('[GraphView] Error destroying cytoscape instance:', e.message);
      }
    };
  }, [theme, onNodeSelect]);

  // Update graph data when data or filters change
  useEffect(() => {
    if (!cyRef.current || !data) return;

    const cy = cyRef.current;
    const elements = convertToCytoscape(data, filters);

    // Clear and add new elements
    cy.elements().remove();
    cy.add(elements.nodes);
    cy.add(elements.edges);

    // Run layout
    if (elements.nodes.length > 0) {
      runLayout();
    }
  }, [data, filters, convertToCytoscape, runLayout]);

  // Highlight selected node
  useEffect(() => {
    if (!cyRef.current) return;

    const cy = cyRef.current;
    cy.elements().unselect();

    if (selectedNode) {
      const node = cy.getElementById(selectedNode.id);
      if (node.length > 0) {
        node.select();
      }
    }
  }, [selectedNode]);

  // Handle highlighted nodes from chat
  useEffect(() => {
    if (!cyRef.current) return;

    const cy = cyRef.current;

    // Clear previous highlighting
    cy.nodes().removeClass('highlighted dimmed');
    cy.edges().removeClass('dimmed');

    if (highlightedNodes && highlightedNodes.length > 0) {
      // Apply highlighting to specified nodes
      const highlightedSet = new Set(highlightedNodes);

      cy.nodes().forEach((node) => {
        if (highlightedSet.has(node.id())) {
          node.addClass('highlighted');
        } else {
          node.addClass('dimmed');
        }
      });

      // Dim edges that don't connect to highlighted nodes
      cy.edges().forEach((edge) => {
        const sourceHighlighted = highlightedSet.has(edge.source().id());
        const targetHighlighted = highlightedSet.has(edge.target().id());
        if (!sourceHighlighted && !targetHighlighted) {
          edge.addClass('dimmed');
        }
      });

      // Center view on highlighted nodes
      const highlightedElements = cy.nodes('.highlighted');
      if (highlightedElements.length > 0) {
        cy.animate({
          fit: {
            eles: highlightedElements,
            padding: 100,
          },
          duration: 500,
        });
      }
    }
  }, [highlightedNodes]);

  const nodeCount = data?.results?.length || data?.entities?.length || 0;

  // Handle context menu close
  const handleContextMenuClose = useCallback(() => {
    setContextMenu({ open: false, position: null, node: null });
  }, []);

  // Handle expand from context menu
  const handleContextMenuExpand = useCallback((node, depth) => {
    if (onExpandNode && node?.id) {
      onExpandNode(node.id, depth);
    }
    handleContextMenuClose();
  }, [onExpandNode, handleContextMenuClose]);

  return (
    <Box sx={{ height: '100%', position: 'relative' }}>
      {/* Graph container - Cytoscape attaches here, NO React children allowed */}
      <Box
        ref={containerRef}
        sx={{
          height: '100%',
          width: '100%',
          position: 'absolute',
          top: 0,
          left: 0,
          backgroundColor: theme === 'dark' ? '#181F2A' : '#fafafa',
        }}
      />
      {/* Overlay elements - positioned over the graph but NOT inside Cytoscape container */}
      {layoutRunning && (
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: 10,
            pointerEvents: 'none',
          }}
        >
          <Paper sx={{ px: 2, py: 1 }}>
            <Typography variant="body2" color="text.secondary">
              Calculating layout...
            </Typography>
          </Paper>
        </Box>
      )}
      {nodeCount === 0 && !layoutRunning && (
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: 10,
            pointerEvents: 'none',
          }}
        >
          <Typography variant="body2" color="text.secondary">
            No data to display. Search for entities to visualize.
          </Typography>
        </Box>
      )}
      {/* Context menu for node actions */}
      {contextMenu.open && (
        <NodeContextMenu
          position={contextMenu.position}
          node={contextMenu.node}
          onExpand={handleContextMenuExpand}
          onClose={handleContextMenuClose}
          theme={theme}
        />
      )}
    </Box>
  );
});

export default GraphView;
