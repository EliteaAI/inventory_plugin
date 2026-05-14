// useToolkitSearch.js
// Hook for searching and selecting toolkits to add as sources

import { useState, useCallback } from 'react';
import SandboxClient from '../utils/sandbox_client';

const PAGE_SIZE = 150;

// Default toolkit types that can be used as data sources
const DEFAULT_TOOLKIT_TYPES = [
  'github',
  'ado_repos',
  'gitlab',
  'bitbucket',
];

// Types to exclude from results (can't be used as sources)
const EXCLUDED_TYPES = ['inventory', 'mcp', 'custom', 'application'];

/**
 * Hook for searching toolkits to add as data sources
 *
 * @returns {Object} Hook state and actions
 */
export default function useToolkitSearch() {
  const [toolkits, setToolkits] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [currentOffset, setCurrentOffset] = useState(0);
  const [currentQuery, setCurrentQuery] = useState('');
  const [currentTypes, setCurrentTypes] = useState(DEFAULT_TOOLKIT_TYPES);

  const { project_id } = window.inventory_ui_config || {};

  // Initialize SandboxClient
  const client = new SandboxClient({
    baseUrl: window.location.origin,
    projectId: project_id,
    authToken: '', // Token from session cookies
    withCredentials: true
  });

  /**
   * Search for toolkits matching the query and type filters
   * @param {string} query - Search query string
   * @param {string[]} toolkitTypes - Array of toolkit types to filter by
   */
  const search = useCallback(async (query = '', toolkitTypes = DEFAULT_TOOLKIT_TYPES) => {
    if (!project_id) {
      setError('Missing project_id in configuration');
      return;
    }

    setIsLoading(true);
    setError(null);
    setCurrentQuery(query);
    setCurrentTypes(toolkitTypes);
    setCurrentOffset(0);

    try {
      const data = await client.listToolkits({
        query,
        toolkitTypes,
        limit: PAGE_SIZE,
        offset: 0,
      });

      // Filter out excluded types that can't be used as sources
      const results = data?.rows || data || [];
      const filtered = results.filter(
        t => !EXCLUDED_TYPES.includes(t.type?.toLowerCase())
      );
      setToolkits(filtered);

      // Check if there might be more results
      const total = data?.total ?? filtered.length;
      setHasMore(filtered.length >= PAGE_SIZE && PAGE_SIZE < total);
    } catch (fetchError) {
      setError(fetchError.message);
      setToolkits([]);
      setHasMore(false);
    } finally {
      setIsLoading(false);
    }
  }, [project_id]);

  /**
   * Load the next page of results
   */
  const loadMore = useCallback(async () => {
    if (!project_id || isLoading || !hasMore) return;

    setIsLoading(true);
    setError(null);
    const newOffset = currentOffset + PAGE_SIZE;

    try {
      const data = await client.listToolkits({
        query: currentQuery,
        toolkitTypes: currentTypes,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      const results = data?.rows || data || [];
      const filtered = results.filter(
        t => !EXCLUDED_TYPES.includes(t.type?.toLowerCase())
      );
      setToolkits(prev => [...prev, ...filtered]);
      setCurrentOffset(newOffset);

      const total = data?.total ?? (toolkits.length + filtered.length);
      setHasMore(filtered.length >= PAGE_SIZE && newOffset + PAGE_SIZE < total);
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setIsLoading(false);
    }
  }, [project_id, isLoading, hasMore, currentOffset, currentQuery, currentTypes, toolkits.length]);

  /**
   * Clear all search results and reset state
   */
  const clearResults = useCallback(() => {
    setToolkits([]);
    setError(null);
    setHasMore(false);
    setCurrentOffset(0);
    setCurrentQuery('');
    setCurrentTypes(DEFAULT_TOOLKIT_TYPES);
  }, []);

  return {
    // State
    toolkits,
    isLoading,
    error,
    hasMore,

    // Actions
    search,
    loadMore,
    clearResults,

    // Constants (exposed for consumers)
    DEFAULT_TOOLKIT_TYPES,
  };
}

// Export constants for use by other components
export { DEFAULT_TOOLKIT_TYPES, EXCLUDED_TYPES };
