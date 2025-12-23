// useSources.js
// Hook for managing sources state in the Inventory UI

import { useState, useEffect, useCallback } from 'react';
import { getToolkit, updateToolkitSettings, apiRequest, getSourcesStatus } from '../utils/api';

/**
 * Hook for managing data sources in the Inventory toolkit
 *
 * Sources are stored as toolkit IDs in settings.sources (List[Integer]).
 * Source configurations (whitelist/blacklist) are stored in settings.source_configs (Dict).
 * This hook expands them to full source objects with metadata for the UI.
 */
export default function useSources() {
  const [settings, setSettings] = useState(null);
  const [sourcesMap, setSourcesMap] = useState({}); // Map of toolkit_id -> source metadata
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Get config from window (injected by server)
  const { project_id, toolkit_id } = window.inventory_ui_config || {};

  // Get source IDs and configs from settings
  const sourceIds = settings?.sources || [];
  const sourceConfigs = settings?.source_configs || {};

  // Build full source objects by combining IDs with metadata and configs
  const sources = sourceIds.map(id => {
    const metadata = sourcesMap[id] || {};
    const config = sourceConfigs[String(id)] || {};
    return {
      toolkit_id: id,
      toolkit_name: metadata.toolkit_name || `Toolkit ${id}`,
      toolkit_type: metadata.toolkit_type || 'other',
      // Configuration from source_configs (persisted)
      branch: config.branch || metadata.branch || '',
      file_patterns: config.file_patterns || metadata.file_patterns || '',
      exclude_patterns: config.exclude_patterns || metadata.exclude_patterns || '',
      preset: config.preset || '',
      // Status from sources_status.json or local state
      status: metadata.status || 'pending',
      last_ingested: metadata.last_ingested || null,
    };
  });

  // Fetch toolkit details for a list of IDs
  const fetchToolkitDetails = useCallback(async (ids, currentMap = {}) => {
    if (!project_id || ids.length === 0) return {};

    const newMap = { ...currentMap };

    // Fetch details for IDs we don't have yet
    const idsToFetch = ids.filter(id => !currentMap[id]?.toolkit_name);

    if (idsToFetch.length > 0) {
      // Build URL with toolkit_type filters - include all source-compatible types
      const toolkitTypes = ['github', 'ado', 'gitlab', 'bitbucket', 'websearch', 'confluence', 'jira'];
      const params = new URLSearchParams();
      params.append('limit', '100');
      toolkitTypes.forEach(type => params.append('toolkit_type', type));

      // Use apiRequest which handles session auth correctly
      const data = await apiRequest(`/api/v2/elitea_core/tools/prompt_lib/${project_id}?${params.toString()}`);

      const rows = Array.isArray(data) ? data : data?.rows || [];
      rows.forEach(toolkit => {
        if (idsToFetch.includes(toolkit.id)) {
          newMap[toolkit.id] = {
            ...newMap[toolkit.id],
            toolkit_name: toolkit.name || toolkit.toolkit_name,
            toolkit_type: toolkit.type,
          };
        }
      });
    }

    return newMap;
  }, [project_id]);

  // Fetch sources status from backend
  const fetchSourcesStatus = useCallback(async (currentMap = {}) => {
    try {
      const statusData = await getSourcesStatus(project_id, toolkit_id);
      console.log('[useSources] Got sources status:', statusData);

      if (statusData && statusData.sources) {
        const newMap = { ...currentMap };

        // Merge status info into sources map
        statusData.sources.forEach(source => {
          const id = source.toolkit_id;
          newMap[id] = {
            ...newMap[id],
            status: source.status || 'pending',
            last_ingested: source.last_updated,
            entities_count: source.entities_count,
            relations_count: source.relations_count,
            toolkit_name: source.toolkit_name || newMap[id]?.toolkit_name,
            toolkit_type: source.toolkit_type || newMap[id]?.toolkit_type,
          };
        });

        return newMap;
      }
    } catch (statusError) {
      console.warn('[useSources] Could not fetch sources status:', statusError);
    }
    return currentMap;
  }, [project_id, toolkit_id]);

  // Fetch settings on mount
  const fetchSettings = useCallback(async () => {
    if (!project_id || !toolkit_id) {
      setError('Missing project_id or toolkit_id in configuration');
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Use getToolkit from api.js which handles session auth correctly
      const data = await getToolkit(project_id, toolkit_id);
      const fetchedSettings = data?.settings || {};
      setSettings(fetchedSettings);

      // Fetch details for source toolkit IDs
      const ids = fetchedSettings.sources || [];
      let detailsMap = {};

      if (ids.length > 0) {
        detailsMap = await fetchToolkitDetails(ids, {});
      }

      // Fetch status from sources_status.json and merge
      detailsMap = await fetchSourcesStatus(detailsMap);

      setSourcesMap(detailsMap);
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setIsLoading(false);
    }
  }, [project_id, toolkit_id, fetchToolkitDetails, fetchSourcesStatus]);

  // Save sources back to toolkit settings
  const saveSources = useCallback(async (newSourceIds) => {
    if (!project_id || !toolkit_id) {
      setError('Missing project_id or toolkit_id');
      return false;
    }

    const newSettings = {
      ...settings,
      sources: newSourceIds,
    };

    return persistSettings(newSettings);
  }, [project_id, toolkit_id, settings]);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // Persist settings to the platform
  const persistSettings = useCallback(async (newSettings) => {
    if (!project_id || !toolkit_id) {
      setError('Missing project_id or toolkit_id');
      return false;
    }

    try {
      // Use updateToolkitSettings from api.js which handles session auth correctly
      await updateToolkitSettings(project_id, toolkit_id, newSettings);
      setSettings(newSettings);
      setError(null);
      return true;
    } catch (updateError) {
      setError(updateError.message);
      return false;
    }
  }, [project_id, toolkit_id]);

  // Add a new source
  const addSource = useCallback(async (sourceObj) => {
    const newId = sourceObj.toolkit_id;

    // Prevent duplicates
    if (sourceIds.includes(newId)) {
      setError('Source already exists');
      return false;
    }

    // Update sources map with metadata (for display)
    setSourcesMap(prev => ({
      ...prev,
      [newId]: {
        toolkit_name: sourceObj.toolkit_name,
        toolkit_type: sourceObj.toolkit_type,
        status: 'pending',
        last_ingested: null,
      },
    }));

    // Build source_configs entry if any config provided
    const newSourceConfigs = { ...sourceConfigs };
    const sourceConfig = {};
    if (sourceObj.branch) sourceConfig.branch = sourceObj.branch;
    if (sourceObj.file_patterns) sourceConfig.file_patterns = sourceObj.file_patterns;
    if (sourceObj.exclude_patterns) sourceConfig.exclude_patterns = sourceObj.exclude_patterns;
    if (sourceObj.preset) sourceConfig.preset = sourceObj.preset;

    if (Object.keys(sourceConfig).length > 0) {
      newSourceConfigs[String(newId)] = sourceConfig;
    }

    // Persist both sources list and source_configs
    const newSettings = {
      ...settings,
      sources: [...sourceIds, newId],
      source_configs: newSourceConfigs,
    };

    return persistSettings(newSettings);
  }, [sourceIds, sourceConfigs, settings, persistSettings]);

  // Update source configuration (whitelist/blacklist/branch/preset)
  const updateSourceConfig = useCallback(async (toolkitId, configUpdates) => {
    const newSourceConfigs = { ...sourceConfigs };
    const existingConfig = newSourceConfigs[String(toolkitId)] || {};

    // Merge updates
    newSourceConfigs[String(toolkitId)] = {
      ...existingConfig,
      ...configUpdates,
    };

    // Clean up empty values
    Object.keys(newSourceConfigs[String(toolkitId)]).forEach(key => {
      if (!newSourceConfigs[String(toolkitId)][key]) {
        delete newSourceConfigs[String(toolkitId)][key];
      }
    });

    // Remove entry if empty
    if (Object.keys(newSourceConfigs[String(toolkitId)]).length === 0) {
      delete newSourceConfigs[String(toolkitId)];
    }

    const newSettings = {
      ...settings,
      source_configs: newSourceConfigs,
    };

    return persistSettings(newSettings);
  }, [sourceConfigs, settings, persistSettings]);

  // Remove a source
  const removeSource = useCallback(async (toolkitId) => {
    // Remove from source_configs as well
    const newSourceConfigs = { ...sourceConfigs };
    delete newSourceConfigs[String(toolkitId)];

    const newSettings = {
      ...settings,
      sources: sourceIds.filter(id => id !== toolkitId),
      source_configs: newSourceConfigs,
    };

    // Remove from metadata map
    setSourcesMap(prev => {
      const { [toolkitId]: removed, ...rest } = prev;
      return rest;
    });

    return persistSettings(newSettings);
  }, [sourceIds, sourceConfigs, settings, persistSettings]);

  // Update source metadata (local only - status, last_ingested, etc.)
  const updateSource = useCallback(async (toolkitId, updates) => {
    setSourcesMap(prev => ({
      ...prev,
      [toolkitId]: {
        ...prev[toolkitId],
        ...updates,
      },
    }));

    // Note: Source metadata like status/last_ingested is stored locally
    // Only the toolkit IDs are persisted to the platform
    return true;
  }, []);

  // Clear error
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    // State
    sources,
    settings,
    isLoading,
    error,

    // Actions
    addSource,
    removeSource,
    updateSource,
    updateSourceConfig,  // New: persist whitelist/blacklist/branch/preset
    refreshSources: fetchSettings,
    clearError,
  };
}
