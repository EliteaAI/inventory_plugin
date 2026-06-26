import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Drawer,
  Box,
  IconButton,
  Alert,
  LinearProgress,
  Typography,
} from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import {
  invokeToolAsync,
  pollTaskStatus,
  stopPlatformTask,
  getToolkitSources,
  getIngestionStatus,
  saveIngestionStatus,
} from '../utils/api';
import SourcesList from './SourcesList';

const DRAWER_WIDTH = 320;

function ToolkitDrawer({
  open,
  onClose,
  projectId,
  toolkitId,
  toolkit,
  onToolkitChange,
  onReindexComplete,
}) {
  const [error, setError] = useState(null);
  const [isIngesting, setIsIngesting] = useState(false);
  const [activeIngestion, setActiveIngestion] = useState(null);
  const [ingestionInfo, setIngestionInfo] = useState(null);
  const ingestionAbortRef = useRef(false);
  const currentInvocationRef = useRef(null);
  const abortControllerRef = useRef(null);
  const statusPollIntervalRef = useRef(null);

  // Get configured sources from toolkit settings
  const configuredSources = getToolkitSources(toolkit);

  // Check for active ingestion - returns true if there's an active ingestion
  const checkIngestionStatus = useCallback(async () => {
    if (!projectId || !toolkitId) return false;

    try {
      const status = await getIngestionStatus(projectId, toolkitId);
      console.log('[ToolkitDrawer] Ingestion status:', status);

      if (status?.has_active_ingestion && status?.current_ingestion) {
        setActiveIngestion(status.current_ingestion);
        setIsIngesting(true);

        // Store the task_id for potential stop operation
        if (status.current_ingestion.task_id) {
          currentInvocationRef.current = status.current_ingestion.task_id;
        }

        setIngestionInfo({
          activeCount: status?.active_count || 0,
          maxParallel: status?.max_parallel || 2,
          availableSlots: status?.available_slots || 0,
        });

        return true; // Has active ingestion
      } else {
        setActiveIngestion(null);
        // Only set isIngesting to false if we're not currently running our own ingestion
        if (!ingestionAbortRef.current && !abortControllerRef.current) {
          setIsIngesting(false);
        }
        setIngestionInfo(null);
        return false; // No active ingestion
      }
    } catch (err) {
      // Silently handle errors - this is just a status check
      // Don't spam console with errors when graph doesn't exist yet
      console.debug('[ToolkitDrawer] Ingestion status check skipped:', err.message);
      setActiveIngestion(null);
      setIngestionInfo(null);
      return false;
    }
  }, [projectId, toolkitId]);

  // Check for active ingestion once when drawer opens
  // Only poll if there's an active ingestion
  useEffect(() => {
    if (!open) {
      // Clear polling when drawer closes
      if (statusPollIntervalRef.current) {
        clearInterval(statusPollIntervalRef.current);
        statusPollIntervalRef.current = null;
      }
      return;
    }

    // Check once when drawer opens
    const initCheck = async () => {
      const hasActive = await checkIngestionStatus();

      // Only start polling if there's an active ingestion
      if (hasActive && !statusPollIntervalRef.current) {
        console.log('[ToolkitDrawer] Active ingestion found, starting status polling');
        statusPollIntervalRef.current = setInterval(async () => {
          const stillActive = await checkIngestionStatus();
          // Stop polling when ingestion completes
          if (!stillActive && statusPollIntervalRef.current) {
            console.log('[ToolkitDrawer] Ingestion completed, stopping polling');
            clearInterval(statusPollIntervalRef.current);
            statusPollIntervalRef.current = null;
          }
        }, 5000);
      }
    };

    initCheck();

    return () => {
      if (statusPollIntervalRef.current) {
        clearInterval(statusPollIntervalRef.current);
        statusPollIntervalRef.current = null;
      }
    };
  }, [open, checkIngestionStatus]);
  const bucket = toolkit?.settings?.toolkit_configuration_bucket || 'graphs';
  const graphName = toolkit?.settings?.toolkit_configuration_graph_name || 'main';

  // Parse error to check for specific error types
  const parseIngestionError = useCallback((err) => {
    const message = err.message || String(err);

    // Check if this is an ingestion slots busy error
    if (message.includes('ingestion_slots_busy') ||
        message.includes('workers are currently busy') ||
        message.includes('wait 10-15 minutes')) {
      return {
        type: 'slots_busy',
        message: 'All ingestion workers are currently busy. Please wait 10-15 minutes and try again.',
        details: message,
      };
    }

    // Try to parse JSON error response
    try {
      if (message.includes('{')) {
        const jsonStart = message.indexOf('{');
        const jsonStr = message.substring(jsonStart);
        const parsed = JSON.parse(jsonStr);
        if (parsed.error === 'ingestion_slots_busy') {
          return {
            type: 'slots_busy',
            message: parsed.message || 'All ingestion workers are currently busy. Please wait 10-15 minutes and try again.',
            details: parsed.message,
          };
        }
      }
    } catch (e) {
      // Not JSON, use original message
    }

    return {
      type: 'general',
      message: `Ingestion failed: ${message}`,
      details: message,
    };
  }, []);

  // Start status polling (call when ingestion starts)
  const startStatusPolling = useCallback(() => {
    if (statusPollIntervalRef.current) return; // Already polling

    console.log('[ToolkitDrawer] Starting status polling');
    statusPollIntervalRef.current = setInterval(async () => {
      const stillActive = await checkIngestionStatus();
      if (!stillActive && statusPollIntervalRef.current) {
        console.log('[ToolkitDrawer] Ingestion completed, stopping polling');
        clearInterval(statusPollIntervalRef.current);
        statusPollIntervalRef.current = null;
      }
    }, 5000);
  }, [checkIngestionStatus]);

  // Run ingestion for a single source with tracking.
  // Routes through the platform's test_toolkit_tool path (invokeToolAsync) so pylon_main
  // mints a per-project auth token and injects llm_settings into the invocation. This makes
  // ingestion work for private projects without deployment-time credentials, matching how
  // chat_query / reindex_graph already run. Tracks the platform task_id for stop support.
  const runIngestionWithTracking = useCallback(async (sourceToolkitId) => {
    // Create abort controller for this ingestion
    abortControllerRef.current = new AbortController();

    // Start the ingestion via the platform and get the task_id.
    // The platform path delivers llm_settings (credentials) but NOT the inventory
    // toolkit's project/application id (its request shape is configuration.parameters,
    // with no project_id/application_id keys). Pass them explicitly so the provider can
    // build the graph path and resolve per-request platform credentials from llm_settings.
    const response = await invokeToolAsync(projectId, toolkitId, 'run_ingestion', {
      toolkit_id: sourceToolkitId,
      project_id: projectId,
      application_id: toolkitId,
      output_format: 'json',
    });

    const taskId = response.task_id || response.id;
    if (!taskId) {
      throw new Error('No task_id returned from server');
    }

    // Track the current task
    currentInvocationRef.current = taskId;
    console.log(`[Ingestion] Started task: ${taskId}`);

    // Poll for completion (with abort support)
    try {
      const result = await pollTaskStatus(
        projectId,
        taskId,
        36000000,
        abortControllerRef.current.signal
      );
      return result;
    } finally {
      currentInvocationRef.current = null;
      abortControllerRef.current = null;
    }
  }, [projectId, toolkitId]);

  // Handle triggering ingestion for a single source
  // Uses runIngestionWithTracking to properly track invocation_id for stop support
  const handleTriggerIngestion = useCallback(async (sourceToolkitId) => {
    if (!projectId || !toolkitId) return;

    setError(null);
    setIsIngesting(true);
    ingestionAbortRef.current = false;

    // Start polling to show progress
    startStatusPolling();

    try {
      // Use runIngestionWithTracking to properly set currentInvocationRef for stop support
      await runIngestionWithTracking(sourceToolkitId);

      if (onReindexComplete) {
        onReindexComplete();
      }
    } catch (err) {
      // Don't show error if it was intentionally stopped
      if (err.message === 'Polling aborted' || ingestionAbortRef.current) {
        console.log(`[Ingestion] Stopped for source ${sourceToolkitId}`);
      } else {
        console.error(`Ingestion failed for source ${sourceToolkitId}:`, err);
        const parsedError = parseIngestionError(err);
        setError(parsedError.message);
        // Re-throw to let the caller know it failed
        throw err;
      }
    } finally {
      // Check status one more time to update UI
      await checkIngestionStatus();
    }
  }, [projectId, toolkitId, onReindexComplete, parseIngestionError, startStatusPolling, checkIngestionStatus, runIngestionWithTracking]);

  // Handle Update All - sequential ingestion of all sources
  const handleUpdateAll = useCallback(async () => {
    if (!projectId || !toolkitId || configuredSources.length === 0) return;

    ingestionAbortRef.current = false;
    setIsIngesting(true);
    setError(null);

    // Start polling to show progress
    startStatusPolling();

    // Initialize status
    const statusMap = {};
    configuredSources.forEach(sourceId => {
      statusMap[sourceId] = 'waiting';
    });

    await saveIngestionStatus(projectId, bucket, graphName, {
      sources: statusMap,
      lastUpdated: new Date().toISOString(),
    });

    // Process sources sequentially
    for (const sourceId of configuredSources) {
      if (ingestionAbortRef.current) break;

      statusMap[sourceId] = 'ingesting';
      await saveIngestionStatus(projectId, bucket, graphName, {
        sources: statusMap,
        lastUpdated: new Date().toISOString(),
      });

      try {
        await runIngestionWithTracking(sourceId);
        statusMap[sourceId] = 'done';
      } catch (err) {
        console.error(`Ingestion failed for source ${sourceId}:`, err);
        // Don't mark as error if it was stopped intentionally
        if (err.message === 'Polling aborted' || ingestionAbortRef.current) {
          statusMap[sourceId] = 'stopped';
        } else {
          statusMap[sourceId] = 'error';
          // Show user-friendly error for slots busy case
          const parsedError = parseIngestionError(err);
          setError(parsedError.message);
          // If slots are busy, stop the batch process
          if (parsedError.type === 'slots_busy') {
            ingestionAbortRef.current = true;
            break;
          }
        }
      }

      await saveIngestionStatus(projectId, bucket, graphName, {
        sources: statusMap,
        lastUpdated: new Date().toISOString(),
      });

      // Check again after processing in case stop was requested
      if (ingestionAbortRef.current) break;
    }

    setIsIngesting(false);
    currentInvocationRef.current = null;
    abortControllerRef.current = null;

    if (onReindexComplete) {
      onReindexComplete();
    }
  }, [projectId, toolkitId, configuredSources, bucket, graphName, onReindexComplete, runIngestionWithTracking, startStatusPolling, parseIngestionError]);

  // Stop ingestion - abort polling and cancel the platform task
  const handleStopIngestion = useCallback(async () => {
    console.log('[Ingestion] Stop requested');
    ingestionAbortRef.current = true;

    // Abort the polling
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    // Cancel the platform task if we have a task_id
    if (currentInvocationRef.current) {
      console.log(`[Ingestion] Stopping task: ${currentInvocationRef.current}`);
      try {
        await stopPlatformTask(projectId, currentInvocationRef.current);
        console.log('[Ingestion] Task stopped successfully');
      } catch (err) {
        console.error('[Ingestion] Failed to stop task:', err);
      }
    }
  }, [projectId]);

  return (
    <Drawer
      variant="persistent"
      anchor="left"
      open={open}
      sx={{
        width: open ? DRAWER_WIDTH : 0,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: DRAWER_WIDTH,
          boxSizing: 'border-box',
          position: 'relative',
          height: '100%',
        },
      }}
    >
      {/* Header wrapper with border - matches AppBar structure */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        {/* Header content - matches Toolbar height */}
        <Box sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          height: 48,
          minHeight: 48,
          px: 1,
        }}>
          <IconButton size="small" onClick={onClose}>
            <ChevronLeftIcon />
          </IconButton>
        </Box>
      </Box>

      {/* Error Display */}
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ borderRadius: 0 }}>
          {error}
        </Alert>
      )}

      {/* Active Ingestion Progress */}
      {activeIngestion && (
        <Box sx={{ px: 2, py: 1.5, bgcolor: 'action.hover', borderBottom: 1, borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography variant="body2" fontWeight={500} color="primary">
              Ingestion in progress
            </Typography>
            {ingestionInfo && (
              <Typography variant="caption" color="text.secondary">
                {ingestionInfo.activeCount}/{ingestionInfo.maxParallel} slots
              </Typography>
            )}
          </Box>
          <LinearProgress sx={{ mb: 1 }} />
          <Typography variant="caption" color="text.secondary">
            Source: {activeIngestion.toolkit_name || `toolkit ${activeIngestion.toolkit_id}`}
          </Typography>
          {activeIngestion.started_at && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              Started: {new Date(activeIngestion.started_at).toLocaleTimeString()}
            </Typography>
          )}
          {activeIngestion.progress_message && (
            <Typography
              variant="caption"
              sx={{
                display: 'block',
                mt: 0.5,
                color: 'text.secondary',
                fontFamily: 'monospace',
                fontSize: '0.65rem',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {activeIngestion.progress_message}
            </Typography>
          )}
        </Box>
      )}

      {/* SourcesList Component */}
      <Box sx={{ flexGrow: 1, overflow: 'hidden' }}>
        <SourcesList
          onTriggerIngestion={handleTriggerIngestion}
          onUpdateAll={handleUpdateAll}
          onStopIngestion={handleStopIngestion}
          isIngesting={isIngesting}
        />
      </Box>
    </Drawer>
  );
}

export default ToolkitDrawer;
