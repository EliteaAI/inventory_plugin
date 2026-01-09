import { useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  Grid,
  Chip,
  LinearProgress,
  Divider,
  Button,
  CircularProgress,
  Alert,
  Collapse,
} from '@mui/material';
import StorageIcon from '@mui/icons-material/Storage';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import DataObjectIcon from '@mui/icons-material/DataObject';
import LayersIcon from '@mui/icons-material/Layers';
import BuildIcon from '@mui/icons-material/Build';
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh';
import RefreshIcon from '@mui/icons-material/Refresh';
import { invokeProviderTool } from '../utils/api';

function StatCard({ icon, label, value, color = 'primary' }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5, textAlign: 'center' }}>
      <Box sx={{ color: `${color}.main`, mb: 0.5 }}>{icon}</Box>
      <Typography variant="h6" component="div">
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
    </Paper>
  );
}

function StatsPanel({ stats, cacheStats, sources, onRefreshStats }) {
  const [normalizing, setNormalizing] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [maintenanceResult, setMaintenanceResult] = useState(null);
  const [showMaintenance, setShowMaintenance] = useState(false);

  const handleNormalizeTypes = async () => {
    setNormalizing(true);
    setMaintenanceResult(null);
    try {
      const result = await invokeProviderTool('normalize_types', { output_format: 'json' });
      setMaintenanceResult({
        type: 'success',
        message: `Normalized types: ${result.types_before} → ${result.types_after} (reduced by ${result.types_reduced})`,
      });
      // Refresh stats after normalization
      if (onRefreshStats) {
        onRefreshStats();
      }
    } catch (err) {
      setMaintenanceResult({ type: 'error', message: err.message });
    } finally {
      setNormalizing(false);
    }
  };

  const handleRebuildIndices = async () => {
    setRebuilding(true);
    setMaintenanceResult(null);
    try {
      const result = await invokeProviderTool('rebuild_indices', { output_format: 'json' });
      setMaintenanceResult({
        type: 'success',
        message: `Indices rebuilt: ${result.entity_count} entities, ${result.unique_types} types, ${result.indexed_files} files`,
      });
      // Refresh stats after rebuild
      if (onRefreshStats) {
        onRefreshStats();
      }
    } catch (err) {
      setMaintenanceResult({ type: 'error', message: err.message });
    } finally {
      setRebuilding(false);
    }
  };

  if (!stats) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          No graph loaded. Configure the toolkit and ingest data to see statistics.
        </Typography>
      </Paper>
    );
  }

  const nodeCount = stats.node_count || 0;
  const edgeCount = stats.edge_count || 0;
  const typeBreakdown = stats.entity_types || {};
  const layerBreakdown = stats.layers || {};
  const uniqueTypes = Object.keys(typeBreakdown).length;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Overview stats */}
      <Grid container spacing={1}>
        <Grid item xs={6}>
          <StatCard
            icon={<DataObjectIcon />}
            label="Entities"
            value={nodeCount.toLocaleString()}
            color="primary"
          />
        </Grid>
        <Grid item xs={6}>
          <StatCard
            icon={<AccountTreeIcon />}
            label="Relations"
            value={edgeCount.toLocaleString()}
            color="secondary"
          />
        </Grid>
      </Grid>

      {/* Sources */}
      {sources && sources.length > 0 && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>
            Sources ({sources.length})
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {sources.map((source) => (
              <Chip
                key={source.source_toolkit}
                label={`${source.source_toolkit} (${source.entity_count})`}
                size="small"
                variant="outlined"
              />
            ))}
          </Box>
        </Paper>
      )}

      {/* Entity types */}
      {Object.keys(typeBreakdown).length > 0 && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>
            Entity Types
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {Object.entries(typeBreakdown)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 10)
              .map(([type, count]) => (
                <Chip
                  key={type}
                  label={`${type}: ${count}`}
                  size="small"
                  variant="outlined"
                />
              ))}
          </Box>
        </Paper>
      )}

      {/* Layers */}
      {Object.keys(layerBreakdown).length > 0 && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>
            <LayersIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Layers
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {Object.entries(layerBreakdown)
              .sort((a, b) => b[1] - a[1])
              .map(([layer, count]) => (
                <Chip
                  key={layer}
                  label={`${layer}: ${count}`}
                  size="small"
                  color="secondary"
                  variant="outlined"
                />
              ))}
          </Box>
        </Paper>
      )}

      {/* Graph Maintenance */}
      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Box
          sx={{ display: 'flex', alignItems: 'center', cursor: 'pointer', mb: showMaintenance ? 1 : 0 }}
          onClick={() => setShowMaintenance(!showMaintenance)}
        >
          <BuildIcon fontSize="small" sx={{ mr: 0.5, color: 'text.secondary' }} />
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            Maintenance
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {uniqueTypes} types
          </Typography>
        </Box>

        <Collapse in={showMaintenance}>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {maintenanceResult && (
              <Alert
                severity={maintenanceResult.type}
                onClose={() => setMaintenanceResult(null)}
                sx={{ py: 0 }}
              >
                <Typography variant="caption">{maintenanceResult.message}</Typography>
              </Alert>
            )}

            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button
                size="small"
                variant="outlined"
                startIcon={normalizing ? <CircularProgress size={14} /> : <AutoFixHighIcon />}
                onClick={handleNormalizeTypes}
                disabled={normalizing || rebuilding}
                sx={{ flex: 1, textTransform: 'none', fontSize: '0.75rem' }}
              >
                Normalize Types
              </Button>
              <Button
                size="small"
                variant="outlined"
                startIcon={rebuilding ? <CircularProgress size={14} /> : <RefreshIcon />}
                onClick={handleRebuildIndices}
                disabled={normalizing || rebuilding}
                sx={{ flex: 1, textTransform: 'none', fontSize: '0.75rem' }}
              >
                Rebuild Indices
              </Button>
            </Box>

            <Typography variant="caption" color="text.secondary">
              Normalize consolidates type variations (Feature → feature).
              Rebuild fixes index inconsistencies.
            </Typography>
          </Box>
        </Collapse>
      </Paper>

      {/* Cache stats */}
      {cacheStats && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>
            <StorageIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Cache
          </Typography>
          <Box sx={{ mb: 1 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
              <Typography variant="caption" color="text.secondary">
                {cacheStats.stats?.total_graphs || 0} graphs cached
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {(cacheStats.stats?.total_size_mb || 0).toFixed(1)} MB / {(cacheStats.stats?.max_size_mb || 0).toFixed(0)} MB
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={Math.min(cacheStats.stats?.usage_percent || 0, 100)}
              sx={{ height: 6, borderRadius: 1 }}
            />
          </Box>
        </Paper>
      )}
    </Box>
  );
}

export default StatsPanel;
