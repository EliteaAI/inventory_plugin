// SourcesList.jsx
// Component for managing the list of configured data sources

import React, { useState, useCallback } from 'react';
import {
  Box,
  Typography,
  Button,
  Paper,
  CircularProgress,
  Alert,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';
import SyncIcon from '@mui/icons-material/Sync';
import StorageIcon from '@mui/icons-material/Storage';

import useSources from '../hooks/useSources';
import SourceCard from './SourceCard';
import AddSourceDialog from './AddSourceDialog';

export default function SourcesList({
  onTriggerIngestion,
  onUpdateAll,
  onStopIngestion,
  isIngesting = false,
}) {
  const {
    sources,
    isLoading,
    error,
    addSource,
    removeSource,
    updateSource,
    updateSourceConfig,
    refreshSources,
    clearError,
  } = useSources();

  const [isDialogOpen, setIsDialogOpen] = useState(false);

  const handleAddSource = useCallback(async (sourceObj) => {
    const success = await addSource(sourceObj);
    if (success) {
      setIsDialogOpen(false);
    }
  }, [addSource]);

  const handleRemoveSource = useCallback(async (toolkitId) => {
    await removeSource(toolkitId);
  }, [removeSource]);

  const handleConfigChange = useCallback(async (toolkitId, configUpdates) => {
    await updateSourceConfig(toolkitId, configUpdates);
  }, [updateSourceConfig]);

  const handleIngest = useCallback(async (toolkitId) => {
    // Update status to ingesting (optimistic update)
    updateSource(toolkitId, { status: 'ingesting' });

    // Trigger external ingestion handler and await completion
    if (onTriggerIngestion) {
      try {
        await onTriggerIngestion(toolkitId);
        // Update status on success
        updateSource(toolkitId, { status: 'done', last_ingested: new Date().toISOString() });
      } catch (err) {
        // Update status on error
        console.error('Ingestion failed:', err);
        updateSource(toolkitId, { status: 'error' });
      }
    }
  }, [updateSource, onTriggerIngestion]);

  const existingSourceIds = sources.map(s => s.toolkit_id);

  // Loading state (initial load only)
  if (isLoading && sources.length === 0) {
    return (
      <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <CircularProgress size={32} />
        </Box>
      </Paper>
    );
  }

  // Error state (no sources loaded)
  if (error && sources.length === 0) {
    return (
      <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', p: 2 }}>
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
          <Button
            variant="outlined"
            onClick={refreshSources}
            startIcon={<RefreshIcon />}
          >
            Retry
          </Button>
        </Box>
      </Paper>
    );
  }

  return (
    <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header with stacked buttons */}
      <Box
        sx={{
          p: 2,
          borderBottom: 1,
          borderColor: 'divider',
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
        }}
      >
        <Button
          fullWidth
          variant="contained"
          size="small"
          startIcon={isIngesting ? <CircularProgress size={14} color="inherit" /> : <SyncIcon />}
          onClick={isIngesting ? onStopIngestion : onUpdateAll}
          disabled={sources.length === 0}
          color={isIngesting ? 'error' : 'primary'}
        >
          {isIngesting ? 'Stop' : 'Update All'}
        </Button>
        <Button
          fullWidth
          variant="outlined"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => setIsDialogOpen(true)}
        >
          Add Source
        </Button>
      </Box>

      {/* Error Banner (when sources exist but there's an error) */}
      {error && sources.length > 0 && (
        <Alert
          severity="error"
          onClose={clearError}
          sx={{ borderRadius: 0 }}
        >
          {error}
        </Alert>
      )}

      {/* Content */}
      <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
        {sources.length === 0 ? (
          // Empty state
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              textAlign: 'center',
              color: 'text.secondary',
            }}
          >
            <StorageIcon sx={{ fontSize: 48, mb: 2, opacity: 0.5 }} />
            <Typography variant="body1" gutterBottom>
              No sources configured
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Add a repository or data source to start ingesting data into your knowledge graph.
            </Typography>
            <Button
              variant="outlined"
              startIcon={<AddIcon />}
              onClick={() => setIsDialogOpen(true)}
            >
              Add Your First Source
            </Button>
          </Box>
        ) : (
          // Sources list
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            {sources.map(source => (
              <SourceCard
                key={source.toolkit_id}
                source={source}
                onIngest={handleIngest}
                onRemove={handleRemoveSource}
                onConfigChange={handleConfigChange}
              />
            ))}
          </Box>
        )}
      </Box>

      {/* Add Source Dialog */}
      <AddSourceDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
        onAdd={handleAddSource}
        existingSourceIds={existingSourceIds}
      />
    </Paper>
  );
}
