import { useEffect, useRef, useCallback, useState, useImperativeHandle, forwardRef } from 'react';
import { Box, Typography, Paper } from '@mui/material';
import cytoscape from 'cytoscape';
import coseBilkent from 'cytoscape-cose-bilkent';

// Register the layout extension
cytoscape.use(coseBilkent);

// Entity type colors - exported for use in filters
export const typeColors = {
  class: '#4CAF50',
  function: '#2196F3',
  method: '#03A9F4',
  module: '#9C27B0',
  file: '#FF9800',
  package: '#795548',
  service: '#E91E63',
  api: '#00BCD4',
  database: '#607D8B',
  table: '#8BC34A',
  default: '#9E9E9E',
};

// Layer colors
const layerColors = {
  code: '#2196F3',
  service: '#E91E63',
  data: '#4CAF50',
  product: '#FF9800',
  domain: '#9C27B0',
};

const GraphView = forwardRef(function GraphView({ data, onNodeSelect, selectedNode, theme = 'light', filters = {} }, ref) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [layoutRunning, setLayoutRunning] = useState(false);

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
    if (cy.nodes().length === 0) return;

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
    layout.run();
    layout.promiseOn('layoutstop').then(() => {
      setLayoutRunning(false);
      cy.fit(undefined, 50);
    });
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
            color: isDark ? '#fff' : '#333',
            'text-background-color': isDark ? '#333' : '#fff',
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
            'line-color': isDark ? '#666' : '#ccc',
            'target-arrow-color': isDark ? '#666' : '#ccc',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': '8px',
            color: isDark ? '#aaa' : '#666',
            'text-rotation': 'autorotate',
            'text-background-color': isDark ? '#333' : '#fff',
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
    });

    // Handle background tap to deselect
    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        if (onNodeSelect) {
          onNodeSelect(null);
        }
      }
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
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

  const nodeCount = data?.results?.length || data?.entities?.length || 0;

  return (
    <Box sx={{ height: '100%', position: 'relative' }}>
      {/* Graph container */}
      <Paper
        ref={containerRef}
        sx={{
          height: '100%',
          width: '100%',
          position: 'relative',
          backgroundColor: theme === 'dark' ? '#1e1e1e' : '#fafafa',
        }}
      >
        {layoutRunning && (
          <Box
            sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              zIndex: 10,
            }}
          >
            <Typography variant="body2" color="text.secondary">
              Calculating layout...
            </Typography>
          </Box>
        )}
        {nodeCount === 0 && !layoutRunning && (
          <Box
            sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
            }}
          >
            <Typography variant="body2" color="text.secondary">
              No data to display. Search for entities to visualize.
            </Typography>
          </Box>
        )}
      </Paper>
    </Box>
  );
});

export default GraphView;
