import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import {
  Box,
  Typography,
  Paper,
  CircularProgress,
  Alert,
  AppBar,
  Toolbar,
  IconButton,
  Tooltip,
  Snackbar,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  TextField,
  InputAdornment,
  Button,
  Tabs,
  Tab,
  Divider,
  Checkbox,
  FormControlLabel,
  FormGroup,
  Slider,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import RefreshIcon from '@mui/icons-material/Refresh';
import ShareIcon from '@mui/icons-material/Share';
import ZoomInIcon from '@mui/icons-material/ZoomIn';
import ZoomOutIcon from '@mui/icons-material/ZoomOut';
import CenterFocusStrongIcon from '@mui/icons-material/CenterFocusStrong';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import MenuIcon from '@mui/icons-material/Menu';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import CloseIcon from '@mui/icons-material/Close';

import GraphView, { typeColors } from './components/GraphView';
import EntityPanel from './components/EntityPanel';
import StatsPanel from './components/StatsPanel';
import ToolkitDrawer from './components/ToolkitDrawer';
import ChatPanel from './components/ChatPanel';
import {
  getConfig,
  getToolkit,
  setToolkitCache,
  searchGraph,
  getGraphStats,
  getCacheStats,
  getEntitiesByIds,
  getEntityNeighbors,
} from './utils/api';

const RIGHT_PANEL_WIDTH = 300;
const DRAWER_WIDTH = 300;
const CHAT_PANEL_MIN_WIDTH = 350;
const CHAT_PANEL_DEFAULT_WIDTH = 450;
const CHAT_PANEL_MAX_WIDTH = 900;

function App() {
  const graphRef = useRef(null);

  // Theme state
  const [mode, setMode] = useState('light');

  // UI state
  const [rightTab, setRightTab] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatWidth, setChatWidth] = useState(CHAT_PANEL_DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef(null);

  // Scope controls state
  const [viewMode, setViewMode] = useState('explore');
  const [maxNodes, setMaxNodes] = useState(500);
  const [depth, setDepth] = useState(2);

  // Filter state
  const [selectedNodeTypes, setSelectedNodeTypes] = useState([]);
  const [selectedEdgeTypes, setSelectedEdgeTypes] = useState([]);
  const [selectedSources, setSelectedSources] = useState([]);

  // Data state
  const [config, setConfig] = useState(null);
  const [toolkit, setToolkit] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [stats, setStats] = useState(null);
  const [cacheStats, setCacheStats] = useState(null);
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [availableNodeTypes, setAvailableNodeTypes] = useState([]); // Dynamic node types from graph
  const [availableEdgeTypes, setAvailableEdgeTypes] = useState([]); // Dynamic edge types from stats

  // Performance stats
  const [queryTime, setQueryTime] = useState(null);
  const [nodeCount, setNodeCount] = useState(0);
  const [edgeCount, setEdgeCount] = useState(0);

  // Loading/error state
  const [loading, setLoading] = useState(true);
  const [searchLoading, setSearchLoading] = useState(false);
  const [error, setError] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'info' });

  // Chat-related state
  const [highlightedNodes, setHighlightedNodes] = useState([]);

  // Accumulated graph from chat exploration - persists and grows with each search
  const [exploredGraph, setExploredGraph] = useState(() => {
    // Try to restore from sessionStorage
    try {
      const saved = sessionStorage.getItem('inventory_explored_graph');
      if (saved) {
        const parsed = JSON.parse(saved);
        console.log('[App] Restored explored graph from session:', parsed.results?.length || 0, 'entities');
        return parsed;
      }
    } catch (e) {
      console.warn('[App] Failed to restore explored graph from session:', e);
    }
    return { results: [], edges: [] };
  });
  
  // Persist explored graph to sessionStorage
  useEffect(() => {
    if (exploredGraph.results?.length > 0) {
      try {
        sessionStorage.setItem('inventory_explored_graph', JSON.stringify(exploredGraph));
        console.log('[App] Saved explored graph to session:', exploredGraph.results.length, 'entities');
      } catch (e) {
        console.warn('[App] Failed to save explored graph to session:', e);
      }
    }
  }, [exploredGraph]);

  // Display restored explored graph on initial load (after loading is done)
  useEffect(() => {
    if (!loading && exploredGraph.results?.length > 0 && !graphData) {
      console.log('[App] Displaying restored explored graph:', exploredGraph.results.length, 'entities');
      setGraphData(exploredGraph);
      setNodeCount(exploredGraph.results.length);
      setEdgeCount(exploredGraph.edges?.length || 0);
    }
  }, [loading, exploredGraph, graphData]);

  // Extract unique node types from graph data (like visualize.py does)
  useEffect(() => {
    if (graphData?.results) {
      const types = new Set();
      graphData.results.forEach(r => {
        if (r.entity?.type) {
          types.add(r.entity.type);
        }
      });
      const sortedTypes = Array.from(types).sort();
      setAvailableNodeTypes(sortedTypes);
      console.log('[DEBUG] Extracted node types from graph:', sortedTypes);
    }
  }, [graphData]);

  // Computed filters for GraphView
  const filters = useMemo(() => ({
    nodeTypes: selectedNodeTypes,
    edgeTypes: selectedEdgeTypes,
  }), [selectedNodeTypes, selectedEdgeTypes]);

  // Initialize app
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const themeParam = urlParams.get('theme');
    if (themeParam === 'dark' || themeParam === 'light') {
      setMode(themeParam);
    }

    const cfg = getConfig();
    setConfig(cfg);

    if (cfg.project_id && cfg.toolkit_id) {
      loadInitialData(cfg.project_id, cfg.toolkit_id);
    } else {
      setLoading(false);
      setError('Missing project_id or toolkit_id. Check URL parameters.');
    }
  }, []);

  const loadInitialData = async (projectId, toolkitId) => {
    try {
      setLoading(true);
      setError(null);

      // Try to get toolkit data (optional - only needed for some UI features)
      try {
        const toolkitData = await getToolkit(projectId, toolkitId);
        setToolkit(toolkitData);
        // Cache toolkit to prevent duplicate API requests
        setToolkitCache(projectId, toolkitId, toolkitData);
      } catch (tkErr) {
        console.warn('Could not load toolkit data from platform:', tkErr.message);
        // Not critical - continue loading
      }

      // Only fetch essential data on initial load
      // Sources and cache stats are fetched on demand (drawer open, stats tab)
      const statsData = await getGraphStats(projectId, toolkitId);
      console.log('[DEBUG App.jsx] statsData received:', statsData);
      console.log('[DEBUG App.jsx] node_count value:', statsData?.node_count);
      if (statsData) {
        console.log('[DEBUG App.jsx] About to call setNodeCount with:', statsData.node_count || 0);
        console.log('[DEBUG App.jsx] About to call setEdgeCount with:', statsData.edge_count || 0);
        setStats(statsData);
        setNodeCount(statsData.node_count || 0);
        setEdgeCount(statsData.edge_count || 0);
        // Set dynamic edge types from stats
        if (statsData.edge_types && statsData.edge_types.length > 0) {
          setAvailableEdgeTypes(statsData.edge_types);
        }
      } else {
        console.log('[DEBUG App.jsx] statsData is null/undefined, NOT setting counts');
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = useCallback(async (e) => {
    e?.preventDefault();
    if (!config?.project_id || !config?.toolkit_id || !searchQuery.trim()) return;

    try {
      setSearchLoading(true);
      const startTime = performance.now();

      const results = await searchGraph(config.project_id, config.toolkit_id, searchQuery, {
        top_k: maxNodes,
        max_depth: depth,
      });

      const endTime = performance.now();
      setQueryTime(Math.round(endTime - startTime));

      setGraphData(results);
      setNodeCount(results?.results?.length || 0);
      setEdgeCount(results?.edges?.length || 0);

      setSnackbar({
        open: true,
        message: `Found ${results?.results?.length || 0} entities`,
        severity: 'success',
      });
    } catch (err) {
      console.error('Search failed:', err);
      setSnackbar({
        open: true,
        message: `Search failed: ${err.message}`,
        severity: 'error',
      });
    } finally {
      setSearchLoading(false);
    }
  }, [config, searchQuery, maxNodes, depth]);

  const handleNodeSelect = useCallback((nodeData) => {
    setSelectedEntity(nodeData);
    if (nodeData) {
      setRightTab(0); // Switch to Details tab
    }
  }, []);

  const handleRefresh = useCallback(() => {
    if (config?.project_id && config?.toolkit_id) {
      loadInitialData(config.project_id, config.toolkit_id);
    }
  }, [config]);

  const handleShare = useCallback(() => {
    const url = new URL(window.location.href);
    if (searchQuery) url.searchParams.set('q', searchQuery);
    url.searchParams.set('depth', depth);
    url.searchParams.set('maxNodes', maxNodes);
    navigator.clipboard.writeText(url.toString());
    setSnackbar({
      open: true,
      message: 'Link copied to clipboard',
      severity: 'success',
    });
  }, [searchQuery, depth, maxNodes]);

  // Lazy load cache stats when Stats tab is clicked
  const handleTabChange = useCallback(async (event, newValue) => {
    setRightTab(newValue);
    // Load cache stats on first visit to Stats tab
    if (newValue === 2 && !cacheStats && config?.project_id && config?.toolkit_id) {
      try {
        const data = await getCacheStats(config.project_id, config.toolkit_id);
        setCacheStats(data);
      } catch (err) {
        console.error('Failed to load cache stats:', err);
      }
    }
  }, [cacheStats, config]);

  const handleNodeTypeToggle = (type) => {
    setSelectedNodeTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  const handleEdgeTypeToggle = (type) => {
    setSelectedEdgeTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  const handleSelectAllNodeTypes = () => setSelectedNodeTypes([...availableNodeTypes]);
  const handleClearNodeTypes = () => setSelectedNodeTypes([]);
  const handleSelectAllEdgeTypes = () => setSelectedEdgeTypes([...availableEdgeTypes]);
  const handleClearEdgeTypes = () => setSelectedEdgeTypes([]);

  const handleSourceToggle = (source) => {
    setSelectedSources(prev =>
      prev.includes(source) ? prev.filter(s => s !== source) : [...prev, source]
    );
  };
  const handleSelectAllSources = () => setSelectedSources([...(stats?.source_toolkits || [])]);
  const handleClearSources = () => setSelectedSources([]);

  const handleReindexComplete = useCallback(async () => {
    // Refresh stats and re-run search if there was a query
    if (config?.project_id && config?.toolkit_id) {
      const statsData = await getGraphStats(config.project_id, config.toolkit_id);
      if (statsData) {
        setStats(statsData);
        setNodeCount(statsData.node_count || 0);
        setEdgeCount(statsData.edge_count || 0);
        // Update dynamic edge types
        if (statsData.edge_types && statsData.edge_types.length > 0) {
          setAvailableEdgeTypes(statsData.edge_types);
        }
      }

      // Re-run search if there was a previous query
      if (searchQuery.trim()) {
        handleSearch();
      }
    }

    setSnackbar({
      open: true,
      message: 'Reindex completed successfully',
      severity: 'success',
    });
  }, [config, searchQuery, handleSearch]);

  const handleChatQueryResult = useCallback((result) => {
    if (result?.results || result?.entities) {
      setGraphData(result);
      setNodeCount(result?.results?.length || result?.entities?.length || 0);
      setEdgeCount(result?.edges?.length || 0);
    }
  }, []);

  // Chat panel resize handlers
  const handleResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    const handleResizeMouseMove = (e) => {
      if (!isResizing) return;

      // Calculate new width based on mouse position from right edge
      const containerRect = document.body.getBoundingClientRect();
      const newWidth = containerRect.right - e.clientX - RIGHT_PANEL_WIDTH - 28; // 28 is chat tab width

      // Clamp to min/max bounds
      const clampedWidth = Math.max(CHAT_PANEL_MIN_WIDTH, Math.min(CHAT_PANEL_MAX_WIDTH, newWidth));
      setChatWidth(clampedWidth);
    };

    const handleResizeMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener('mousemove', handleResizeMouseMove);
      document.addEventListener('mouseup', handleResizeMouseUp);
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleResizeMouseMove);
      document.removeEventListener('mouseup', handleResizeMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // Clear explored graph (also clears sessionStorage)
  const handleClearExploredGraph = useCallback(() => {
    graphRef.current?.reset();
    setExploredGraph({ results: [], edges: [] });
    setGraphData(null);
    setNodeCount(0);
    setEdgeCount(0);
    setHighlightedNodes([]);
    try {
      sessionStorage.removeItem('inventory_explored_graph');
    } catch (e) {
      console.warn('[App] Failed to clear explored graph from session:', e);
    }
    setSnackbar({
      open: true,
      message: 'Explored graph cleared',
      severity: 'info',
    });
  }, []);

  /**
   * Handle touched entities from chat - fetch graph data and MERGE with existing explored graph
   * This creates a growing visualization as the user continues chatting
   */
  const handleTouchedEntities = useCallback(async (entities) => {
    console.log('[App] Touched entities from chat:', entities);
    if (!entities || entities.length === 0) return;

    // Separate file access entities (direct add) from graph entities (need to fetch)
    const fileAccessEntities = entities.filter(e => e.is_file_access);
    const graphEntities = entities.filter(e => !e.is_file_access);

    // Extract entity IDs for graph entities only
    const graphEntityIds = graphEntities.map(e => e.id).filter(Boolean);
    const allEntityIds = entities.map(e => e.id).filter(Boolean);

    // Convert file access entities to graph result format
    const fileAccessResults = fileAccessEntities.map(e => ({
      entity: {
        id: e.id,
        name: e.name,
        type: e.type || 'file',
        layer: e.layer || 'source',
        file_path: e.file_path,
        source_toolkit: e.source_toolkit,
        description: `File: ${e.file_path}`,
      },
      score: 1.0,
    }));

    console.log(`[App] File access entities: ${fileAccessResults.length}, Graph entities to fetch: ${graphEntityIds.length}`);

    try {
      let graphResult = { results: [], edges: [] };

      // Only fetch from backend if there are graph entities
      if (graphEntityIds.length > 0) {
        graphResult = await getEntitiesByIds(
          config?.project_id,
          config?.toolkit_id,
          graphEntityIds,
          true // include edges
        );
        console.log('[App] Fetched touched entities graph data:', graphResult);
      }

      // Combine file access results with fetched graph results
      const newResults = [
        ...fileAccessResults,
        ...(graphResult.results || graphResult.entities || []),
      ];
      const newEdges = graphResult.edges || [];

      if (newResults.length > 0) {
        // Use functional update pattern that also updates graphData synchronously
        setExploredGraph(prev => {
          const existingIds = new Set(prev.results?.map(r => r.entity?.id || r.id) || []);
          const existingEdgeKeys = new Set(
            (prev.edges || []).map(e => `${e.source}--${e.type}-->${e.target}`)
          );

          // Add new entities that don't already exist
          const mergedResults = [...(prev.results || [])];
          let addedCount = 0;
          for (const result of newResults) {
            const entityId = result.entity?.id || result.id;
            if (entityId && !existingIds.has(entityId)) {
              mergedResults.push(result);
              existingIds.add(entityId);
              addedCount++;
            }
          }

          // Add new edges that don't already exist
          const mergedEdges = [...(prev.edges || [])];
          let addedEdges = 0;
          for (const edge of newEdges) {
            const edgeKey = `${edge.source}--${edge.type}-->${edge.target}`;
            if (!existingEdgeKeys.has(edgeKey)) {
              mergedEdges.push(edge);
              existingEdgeKeys.add(edgeKey);
              addedEdges++;
            }
          }

          console.log(`[App] Merged graph: +${addedCount} entities, +${addedEdges} edges (total: ${mergedResults.length} entities, ${mergedEdges.length} edges)`);

          const mergedGraph = {
            results: mergedResults,
            edges: mergedEdges,
            total_entities: mergedResults.length,
            total_edges: mergedEdges.length,
          };

          // Update graphData and counts in the same render cycle
          // React 18 batches these updates together
          setGraphData(mergedGraph);
          setNodeCount(mergedResults.length);
          setEdgeCount(mergedEdges.length);

          return mergedGraph;
        });

        // Highlight only the newly touched entities
        setHighlightedNodes(allEntityIds);

        // Show notification
        setSnackbar({
          open: true,
          message: `Added ${allEntityIds.length} entities to exploration`,
          severity: 'info',
        });
      } else {
        // Entities not found in graph - just highlight what we have
        setHighlightedNodes(allEntityIds);
        setSnackbar({
          open: true,
          message: `Referenced ${allEntityIds.length} entities (some may not exist in graph)`,
          severity: 'warning',
        });
      }
    } catch (err) {
      console.error('[App] Failed to fetch touched entities:', err);
      // Still try to highlight them in case they're already in the graph
      setHighlightedNodes(allEntityIds);
      setSnackbar({
        open: true,
        message: `Error loading entity graph: ${err.message}`,
        severity: 'error',
      });
    }
  }, [config]);

  /**
   * Handle node expansion from context menu - fetch neighbors at specified depth and MERGE with graph
   */
  const handleExpandNode = useCallback(async (entityId, depth) => {
    console.log(`[App] Expanding node ${entityId} to depth ${depth}`);
    if (!entityId || !config?.project_id || !config?.toolkit_id) return;

    try {
      setSearchLoading(true);
      const startTime = performance.now();

      const result = await getEntityNeighbors(
        config.project_id,
        config.toolkit_id,
        entityId,
        depth
      );

      const endTime = performance.now();
      setQueryTime(Math.round(endTime - startTime));

      console.log('[App] Expand result:', result);

      if (result && (result.results?.length > 0 || result.entities?.length > 0)) {
        const newResults = result.results || result.entities || [];
        const newEdges = result.edges || [];

        // Merge with existing graph (same pattern as handleTouchedEntities)
        setExploredGraph(prev => {
          const existingIds = new Set(prev.results?.map(r => r.entity?.id || r.id) || []);
          const existingEdgeKeys = new Set(
            (prev.edges || []).map(e => `${e.source}--${e.type}-->${e.target}`)
          );

          // Add new entities that don't already exist
          const mergedResults = [...(prev.results || [])];
          let addedCount = 0;
          for (const result of newResults) {
            const entityId = result.entity?.id || result.id;
            if (entityId && !existingIds.has(entityId)) {
              mergedResults.push(result);
              existingIds.add(entityId);
              addedCount++;
            }
          }

          // Add new edges that don't already exist
          const mergedEdges = [...(prev.edges || [])];
          let addedEdges = 0;
          for (const edge of newEdges) {
            const edgeKey = `${edge.source}--${edge.type}-->${edge.target}`;
            if (!existingEdgeKeys.has(edgeKey)) {
              mergedEdges.push(edge);
              existingEdgeKeys.add(edgeKey);
              addedEdges++;
            }
          }

          console.log(`[App] Expand merged: +${addedCount} entities, +${addedEdges} edges (total: ${mergedResults.length} entities, ${mergedEdges.length} edges)`);

          const mergedGraph = {
            results: mergedResults,
            edges: mergedEdges,
            total_entities: mergedResults.length,
            total_edges: mergedEdges.length,
          };

          // Update graphData and counts in the same render cycle
          setGraphData(mergedGraph);
          setNodeCount(mergedResults.length);
          setEdgeCount(mergedEdges.length);

          return mergedGraph;
        });

        // Highlight the expanded nodes
        const newEntityIds = newResults.map(r => r.entity?.id || r.id).filter(Boolean);
        setHighlightedNodes(newEntityIds);

        setSnackbar({
          open: true,
          message: `Expanded to ${result.total_entities} entities at depth ${depth}`,
          severity: 'success',
        });
      } else {
        setSnackbar({
          open: true,
          message: 'No additional connections found',
          severity: 'info',
        });
      }
    } catch (err) {
      console.error('[App] Node expansion failed:', err);
      setSnackbar({
        open: true,
        message: `Expansion failed: ${err.message}`,
        severity: 'error',
      });
    } finally {
      setSearchLoading(false);
    }
  }, [config]);

  // Graph context for chat
  const graphContext = useMemo(() => ({
    selectedEntity: selectedEntity,
    nodeCount: nodeCount,
    edgeCount: edgeCount,
    lastQuery: searchQuery,
  }), [selectedEntity, nodeCount, edgeCount, searchQuery]);

  // AlitaUI-inspired theme
  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode,
          primary: {
            main: mode === 'dark' ? '#6ae8fa' : '#C428DD',
            contrastText: mode === 'dark' ? '#0E131D' : '#FFFFFF',
          },
          secondary: {
            main: mode === 'dark' ? '#777A83' : '#777A83',
          },
          background: {
            default: mode === 'dark' ? '#0E131D' : '#F8FCFF',
            paper: mode === 'dark' ? '#181F2A' : '#FFFFFF',
            secondary: mode === 'dark' ? '#181F2A' : '#FAFAFA',
          },
          text: {
            primary: mode === 'dark' ? '#FFFFFF' : '#0E131D',
            secondary: mode === 'dark' ? '#ADAFB7' : '#777A83',
            disabled: mode === 'dark' ? '#5B5E69' : '#CBCED6',
          },
          divider: mode === 'dark' ? 'rgba(255,255,255,0.1)' : '#CBCED6',
          success: { main: '#2BD48D' },
          error: { main: '#D71616' },
          warning: { main: '#E97912' },
          info: { main: '#006DD1' },
        },
        typography: {
          fontFamily: '"Montserrat", "Roboto", "Arial", sans-serif',
          fontSize: 12,
          headingMedium: {
            fontWeight: 600,
            fontSize: '16px',
            lineHeight: '24px',
          },
          headingSmall: {
            fontWeight: 600,
            fontSize: '14px',
            lineHeight: '24px',
          },
          labelMedium: {
            fontWeight: 500,
            fontSize: '14px',
            lineHeight: '24px',
          },
          labelSmall: {
            fontWeight: 500,
            fontSize: '12px',
            lineHeight: '16px',
          },
          bodyMedium: {
            fontWeight: 400,
            fontSize: '14px',
            lineHeight: '24px',
          },
          bodySmall: {
            fontWeight: 400,
            fontSize: '12px',
            lineHeight: '16px',
          },
        },
        shape: {
          borderRadius: 0,
        },
        components: {
          MuiButton: {
            defaultProps: {
              disableRipple: true,
            },
            styleOverrides: {
              root: {
                textTransform: 'none',
                fontWeight: 500,
                borderRadius: '28px',
                fontSize: '12px',
                lineHeight: '16px',
                padding: '6px 16px',
              },
              sizeSmall: {
                fontSize: '10px',
                padding: '4px 12px',
              },
            },
          },
          MuiPaper: {
            styleOverrides: {
              root: ({ theme }) => ({
                backgroundImage: 'none',
              }),
            },
          },
          MuiDrawer: {
            styleOverrides: {
              paper: ({ theme }) => ({
                background: theme.palette.background.paper,
                borderRight: `1px solid ${theme.palette.divider}`,
              }),
            },
          },
          MuiDialog: {
            styleOverrides: {
              paper: ({ theme }) => ({
                background: theme.palette.background.paper,
                borderRadius: '16px',
                border: `1px solid ${theme.palette.divider}`,
              }),
            },
          },
          MuiChip: {
            styleOverrides: {
              root: {
                fontWeight: 500,
                fontSize: '10px',
              },
              sizeSmall: {
                height: '20px',
              },
            },
          },
          MuiTab: {
            styleOverrides: {
              root: {
                textTransform: 'none',
                fontWeight: 500,
                fontSize: '12px',
              },
            },
          },
          MuiTooltip: {
            styleOverrides: {
              tooltip: {
                backgroundColor: mode === 'dark' ? '#CAD0D8' : '#3B3E46',
                color: mode === 'dark' ? '#0E131D' : '#FFFFFF',
                fontSize: '12px',
                fontWeight: 500,
              },
            },
          },
          MuiIconButton: {
            defaultProps: {
              disableRipple: true,
            },
          },
          MuiTextField: {
            styleOverrides: {
              root: {
                '& .MuiOutlinedInput-root': {
                  borderRadius: 0,
                },
              },
            },
          },
        },
      }),
    [mode]
  );

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>

        {/* Left Drawer for Toolkit Management - Full Height */}
        <ToolkitDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          projectId={config?.project_id}
          toolkitId={config?.toolkit_id}
          toolkit={toolkit}
          onToolkitChange={setToolkit}
          onReindexComplete={handleReindexComplete}
        />

        {/* Main Content Wrapper */}
        <Box sx={{ display: 'flex', flexDirection: 'column', flexGrow: 1, overflow: 'hidden' }}>

          {/* Single Header Bar */}
          <AppBar position="static" elevation={0} sx={{
            backgroundColor: 'background.paper',
            color: 'text.primary',
            borderBottom: 1,
            borderColor: 'divider',
          }}>
            <Toolbar variant="dense" sx={{ gap: 1.5, height: 48, minHeight: 48 }}>
              {/* Menu toggle - hidden when drawer is open */}
              {!drawerOpen && (
                <Tooltip title="Data Sources">
                  <IconButton
                    size="small"
                    onClick={() => setDrawerOpen(true)}
                  >
                    <MenuIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}

              <Box sx={{ flexGrow: 1 }} />

            {/* Search */}
            <Box component="form" onSubmit={handleSearch} sx={{ width: 280 }}>
              <TextField
                size="small"
                fullWidth
                placeholder="Search entities..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                sx={{ '& .MuiInputBase-root': { height: 32 } }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon fontSize="small" />
                    </InputAdornment>
                  ),
                }}
              />
            </Box>

            <Tooltip title="Share">
              <IconButton size="small" onClick={handleShare}>
                <ShareIcon fontSize="small" />
              </IconButton>
            </Tooltip>

            <Tooltip title="Refresh">
              <IconButton size="small" onClick={handleRefresh} disabled={loading}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>

          {/* Main Content Area */}
          <Box sx={{
            display: 'flex',
            flexGrow: 1,
            minHeight: 0, // Required for flexGrow to work in column layout
            overflow: 'hidden',
            // Consistent background across graph and chat tab area
            backgroundColor: mode === 'dark' ? '#181F2A' : '#fafafa',
          }}>

            {/* Graph Canvas with Tools */}
            <Box sx={{
              flexGrow: 1,
              position: 'relative',
              overflow: 'hidden',
              minWidth: 0,
            }}>

            {/* Loading Overlay */}
            {(loading || searchLoading) && (
              <Box sx={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: mode === 'dark' ? 'rgba(24, 31, 42, 0.9)' : 'rgba(250, 250, 250, 0.9)',
                zIndex: 10,
              }}>
                <CircularProgress />
              </Box>
            )}

            {/* Error Display */}
            {error && !loading && (
              <Box sx={{ p: 3 }}>
                <Alert severity="error">{error}</Alert>
              </Box>
            )}

            {/* Graph View */}
            {!error && (
              <>
                <GraphView
                  ref={graphRef}
                  data={graphData}
                  onNodeSelect={handleNodeSelect}
                  onExpandNode={handleExpandNode}
                  selectedNode={selectedEntity}
                  highlightedNodes={highlightedNodes}
                  theme={mode}
                  filters={filters}
                />

                {/* Floating Tools Panel */}
                <Paper
                  elevation={2}
                  sx={{
                    position: 'absolute',
                    top: 12,
                    left: 12,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 0.25,
                    p: 0.5,
                    zIndex: 5,
                  }}
                >
                  <Tooltip title="Zoom In" placement="right">
                    <IconButton size="small" onClick={() => graphRef.current?.zoomIn()}>
                      <ZoomInIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Zoom Out" placement="right">
                    <IconButton size="small" onClick={() => graphRef.current?.zoomOut()}>
                      <ZoomOutIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Fit to View" placement="right">
                    <IconButton size="small" onClick={() => graphRef.current?.fit()}>
                      <CenterFocusStrongIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Divider sx={{ my: 0.25 }} />
                  <Tooltip title="Re-layout" placement="right">
                    <IconButton size="small" onClick={() => graphRef.current?.relayout()}>
                      <AccountTreeIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Clear" placement="right">
                    <IconButton size="small" onClick={handleClearExploredGraph}>
                      <RestartAltIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Paper>

                {/* Status Overlay */}
                <Paper
                  elevation={1}
                  sx={{
                    position: 'absolute',
                    bottom: 12,
                    left: '50%',
                    transform: 'translateX(-50%)',
                    px: 1.5,
                    py: 0.25,
                    backgroundColor: 'rgba(0,0,0,0.75)',
                    color: 'white',
                    borderRadius: 1,
                    zIndex: 5,
                  }}
                >
                  <Typography variant="caption">
                    {nodeCount} nodes / {edgeCount} edges | Limit: {maxNodes}
                    {queryTime && ` | ${queryTime}ms`}
                  </Typography>
                </Paper>
              </>
            )}
          </Box>

          {/* Chat Side Panel - Expandable & Resizable */}
          <Box
            sx={{
              display: 'flex',
              flexShrink: 0,
              height: '100%',
              position: 'relative',
              zIndex: chatOpen ? 10 : 1, // Higher z-index when open to overlay graph
              // Match the graph background color for seamless look
              backgroundColor: mode === 'dark' ? '#181F2A' : '#fafafa',
            }}
          >
            {/* Vertical Chat Tab Button */}
            <Box
              onClick={() => setChatOpen(!chatOpen)}
              sx={{
                width: 28,
                minHeight: 120,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                backgroundColor: mode === 'dark'
                  ? (chatOpen ? '#6ae8fa' : 'rgba(106, 232, 250, 0.15)')
                  : (chatOpen ? '#C428DD' : 'rgba(196, 40, 221, 0.1)'),
                borderTopLeftRadius: 8,
                borderBottomLeftRadius: 8,
                marginTop: 'auto',
                marginBottom: 'auto',
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: mode === 'dark'
                    ? (chatOpen ? '#83EFFF' : 'rgba(106, 232, 250, 0.25)')
                    : (chatOpen ? '#9B1FB0' : 'rgba(196, 40, 221, 0.2)'),
                },
              }}
            >
              <Typography
                sx={{
                  writingMode: 'vertical-rl',
                  textOrientation: 'mixed',
                  transform: 'rotate(180deg)',
                  fontSize: '12px',
                  fontWeight: 600,
                  letterSpacing: '1px',
                  color: mode === 'dark'
                    ? (chatOpen ? '#0E131D' : '#6ae8fa')
                    : (chatOpen ? '#FFFFFF' : '#C428DD'),
                  userSelect: 'none',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <ChatBubbleOutlineIcon sx={{ fontSize: 14, transform: 'rotate(90deg)' }} />
                Chat
              </Typography>
            </Box>

            {/* Expandable Chat Panel with Resize Handle */}
            <Box
              sx={{
                position: 'relative',
                width: chatOpen ? chatWidth : 0,
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
                borderLeft: chatOpen ? 1 : 0,
                borderColor: 'divider',
                backgroundColor: 'background.paper',
                transition: isResizing ? 'none' : 'width 0.2s ease',
              }}
            >
              {/* Resize Handle - on the left edge of chat content */}
              {chatOpen && (
                <Box
                  ref={resizeRef}
                  onMouseDown={handleResizeMouseDown}
                  sx={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    zIndex: 20,
                    backgroundColor: isResizing
                      ? (mode === 'dark' ? 'rgba(106, 232, 250, 0.5)' : 'rgba(196, 40, 221, 0.5)')
                      : 'transparent',
                    transition: isResizing ? 'none' : 'background-color 0.2s',
                    '&:hover': {
                      backgroundColor: mode === 'dark'
                        ? 'rgba(106, 232, 250, 0.3)'
                        : 'rgba(196, 40, 221, 0.3)',
                    },
                    '&::after': {
                      content: '""',
                      position: 'absolute',
                      left: 2,
                      top: '50%',
                      transform: 'translateY(-50%)',
                      width: 2,
                      height: 40,
                      borderRadius: 1,
                      backgroundColor: mode === 'dark'
                        ? 'rgba(106, 232, 250, 0.4)'
                        : 'rgba(196, 40, 221, 0.4)',
                    },
                  }}
                />
              )}
              {chatOpen && (
                <ChatPanel
                  projectId={config?.project_id}
                  toolkitId={config?.toolkit_id}
                  toolkit={toolkit}
                  filters={{
                    entity_types: selectedNodeTypes,
                    sources: selectedSources,
                    depth: depth,
                    max_nodes: maxNodes,
                  }}
                  onClose={() => setChatOpen(false)}
                  onTouchedEntities={handleTouchedEntities}
                  onClearGraph={handleClearExploredGraph}
                  theme={mode}
                />
              )}
            </Box>
          </Box>

          {/* Right Panel */}
          <Paper
            elevation={0}
            sx={{
              width: RIGHT_PANEL_WIDTH,
              borderLeft: 1,
              borderColor: 'divider',
              display: 'flex',
              flexDirection: 'column',
              flexShrink: 0,
            }}
          >
            <Tabs
              value={rightTab}
              onChange={handleTabChange}
              variant="fullWidth"
              sx={{ borderBottom: 1, borderColor: 'divider', minHeight: 36 }}
            >
              <Tab label="Details" sx={{ minWidth: 0, fontSize: 11, minHeight: 36, py: 0 }} />
              <Tab label="Filters" sx={{ minWidth: 0, fontSize: 11, minHeight: 36, py: 0 }} />
              <Tab label="Stats" sx={{ minWidth: 0, fontSize: 11, minHeight: 36, py: 0 }} />
            </Tabs>

            <Box sx={{ flexGrow: 1, overflow: 'auto', p: 1.5 }}>
              {/* Details Tab */}
              {rightTab === 0 && (
                selectedEntity ? (
                  <EntityPanel
                    entity={selectedEntity}
                    projectId={config?.project_id}
                    toolkitId={config?.toolkit_id}
                    onClose={() => setSelectedEntity(null)}
                    theme={mode}
                    compact
                  />
                ) : (
                  <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                    <Typography variant="body2">Select a node to view details</Typography>
                  </Box>
                )
              )}

              {/* Filters Tab */}
              {rightTab === 1 && (
                <Box>
                  {/* Search Scope */}
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>Search Scope</Typography>
                  <Box sx={{ mb: 2, px: 1 }}>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
                      Depth: {depth}
                    </Typography>
                    <Slider
                      size="small"
                      value={depth}
                      onChange={(e, val) => setDepth(val)}
                      min={1}
                      max={5}
                      marks
                      valueLabelDisplay="auto"
                    />
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5, mt: 1 }}>
                      Max Nodes: {maxNodes}
                    </Typography>
                    <Slider
                      size="small"
                      value={maxNodes}
                      onChange={(e, val) => setMaxNodes(val)}
                      min={50}
                      max={1000}
                      step={50}
                      valueLabelDisplay="auto"
                    />
                  </Box>

                  <Divider sx={{ my: 1.5 }} />

                  {/* Sources Filter */}
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="subtitle2">Sources</Typography>
                    <Box>
                      <Button size="small" onClick={handleSelectAllSources} sx={{ minWidth: 0, px: 0.5 }}>All</Button>
                      <Button size="small" onClick={handleClearSources} sx={{ minWidth: 0, px: 0.5 }}>Clear</Button>
                    </Box>
                  </Box>
                  {stats?.source_toolkits?.length > 0 ? (
                    <FormGroup sx={{ mb: 2 }}>
                      {stats.source_toolkits.map((source) => (
                        <FormControlLabel
                          key={source}
                          control={
                            <Checkbox
                              size="small"
                              checked={selectedSources.length === 0 || selectedSources.includes(source)}
                              onChange={() => handleSourceToggle(source)}
                              sx={{ py: 0.25 }}
                            />
                          }
                          label={<Typography variant="body2">{source}</Typography>}
                          sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
                        />
                      ))}
                    </FormGroup>
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2, fontStyle: 'italic' }}>
                      No sources ingested yet
                    </Typography>
                  )}

                  <Divider sx={{ my: 1.5 }} />

                  {/* Node Types */}
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="subtitle2">Node Types ({availableNodeTypes.length})</Typography>
                    <Box>
                      <Button size="small" onClick={handleSelectAllNodeTypes} sx={{ minWidth: 0, px: 0.5 }}>All</Button>
                      <Button size="small" onClick={handleClearNodeTypes} sx={{ minWidth: 0, px: 0.5 }}>Clear</Button>
                    </Box>
                  </Box>
                  {availableNodeTypes.length > 0 ? (
                    <FormGroup sx={{ mb: 2 }}>
                      {availableNodeTypes.map((type) => (
                        <FormControlLabel
                          key={type}
                          control={
                            <Checkbox
                              size="small"
                              checked={selectedNodeTypes.includes(type)}
                              onChange={() => handleNodeTypeToggle(type)}
                              sx={{ py: 0.25, '& .MuiSvgIcon-root': { color: typeColors[type] } }}
                            />
                          }
                          label={<Typography variant="body2">{type}</Typography>}
                          sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
                        />
                      ))}
                    </FormGroup>
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2, fontStyle: 'italic' }}>
                      Run a search to see node types
                    </Typography>
                  )}

                  <Divider sx={{ my: 1.5 }} />

                  {/* Edge Types */}
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="subtitle2">Edge Types ({availableEdgeTypes.length})</Typography>
                    <Box>
                      <Button size="small" onClick={handleSelectAllEdgeTypes} sx={{ minWidth: 0, px: 0.5 }}>All</Button>
                      <Button size="small" onClick={handleClearEdgeTypes} sx={{ minWidth: 0, px: 0.5 }}>Clear</Button>
                    </Box>
                  </Box>
                  {availableEdgeTypes.length > 0 ? (
                    <FormGroup>
                      {availableEdgeTypes.map((type) => (
                        <FormControlLabel
                          key={type}
                          control={
                            <Checkbox
                              size="small"
                              checked={selectedEdgeTypes.includes(type)}
                              onChange={() => handleEdgeTypeToggle(type)}
                              sx={{ py: 0.25 }}
                            />
                          }
                          label={<Typography variant="body2">{type}</Typography>}
                          sx={{ ml: 0, '& .MuiFormControlLabel-label': { ml: 0.5 } }}
                        />
                      ))}
                    </FormGroup>
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
                      No edge types available yet
                    </Typography>
                  )}
                </Box>
              )}

              {/* Stats Tab */}
              {rightTab === 2 && (
                <StatsPanel stats={stats} cacheStats={cacheStats} />
              )}
            </Box>
          </Paper>
          </Box>
        </Box>

        {/* Snackbar */}
        <Snackbar
          open={snackbar.open}
          autoHideDuration={3000}
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        >
          <Alert
            onClose={() => setSnackbar({ ...snackbar, open: false })}
            severity={snackbar.severity}
            sx={{ width: '100%' }}
          >
            {snackbar.message}
          </Alert>
        </Snackbar>
      </Box>
    </ThemeProvider>
  );
}

export default App;
