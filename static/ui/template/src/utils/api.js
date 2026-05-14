/**
 * API utilities for Inventory UI
 * Handles communication with the inventory provider backend via platform APIs
 */

// Cache for toolkit data to avoid re-fetching
let toolkitCache = null;
let toolkitCacheKey = null;

/**
 * Set toolkit data in cache (call this from App after initial fetch)
 * This prevents duplicate API requests
 */
export function setToolkitCache(projectId, toolkitId, toolkit) {
  toolkitCacheKey = `${projectId}-${toolkitId}`;
  toolkitCache = toolkit;
}

/**
 * Get cached toolkit data
 */
export function getCachedToolkit() {
  return toolkitCache;
}

/**
 * Get the base configuration from window or environment
 */
export function getConfig() {
  // Check for injected runtime config first (production mode)
  if (window.inventory_ui_config) {
    return window.inventory_ui_config;
  }

  // Fall back to URL parameters (development mode)
  const urlParams = new URLSearchParams(window.location.search);
  const pathParts = window.location.pathname.split('/');
  const uiHostIndex = pathParts.indexOf('ui_host');

  let projectId = urlParams.get('project_id');
  let toolkitId = urlParams.get('toolkit_id');

  // Extract from ui_host path: /app/ui_host/inventory/ui/{project_id}/{toolkit_id}
  if (uiHostIndex !== -1 && pathParts.length > uiHostIndex + 4) {
    projectId = projectId || pathParts[uiHostIndex + 3];
    toolkitId = toolkitId || pathParts[uiHostIndex + 4];
  }

  return {
    base_uri: window.location.pathname.replace(/\/$/, ''),
    project_id: projectId || import.meta.env.VITE_DEFAULT_PROJECT_ID,
    toolkit_id: toolkitId || import.meta.env.VITE_DEFAULT_TOOLKIT_ID,
    provider_url: import.meta.env.VITE_PROVIDER_URL || 'http://127.0.0.1:8091',
  };
}

/**
 * Get the provider's API base path through the platform proxy
 * The ui_host proxy routes /app/ui_host/inventory/ui/{project_id}/* to the inventory provider
 */
export function getProviderBasePath() {
  const config = getConfig();
  // The ui_host proxy path format is: /app/ui_host/<provider>/<ui_name>/<project_id>/<path>
  // This avoids CORS issues since it's same-origin
  return `/app/ui_host/inventory/ui/${config.project_id}`;
}

/**
 * Make an API request with session authentication
 */
export async function apiRequest(path, options = {}) {
  const method = options.method || 'GET';

  const headers = {
    ...options.headers,
  };

  // Only add Content-Type for requests with a body (POST, PUT, PATCH)
  if (['POST', 'PUT', 'PATCH'].includes(method.toUpperCase()) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  // Add dev token in development mode
  if (import.meta.env.DEV && import.meta.env.VITE_DEV_TOKEN) {
    headers['Authorization'] = `Bearer ${import.meta.env.VITE_DEV_TOKEN}`;
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: 'include', // Include session cookies
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

/**
 * Make a request to the inventory provider via platform proxy
 * Routes through /app/ui_host/inventory/* to avoid CORS issues
 */
export async function providerRequest(path, options = {}) {
  const basePath = getProviderBasePath();
  const method = options.method || 'GET';

  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  const url = `${basePath}${path}`;
  console.log(`[Provider] ${method} ${url}`);

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: 'include', // Include session cookies for proxy auth
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

/**
 * Invoke a tool directly on the inventory provider
 * Returns invocation_id for async operations
 */
export async function invokeProviderTool(toolName, params) {
  const config = getConfig();

  const requestBody = {
    configuration: {
      project_id: config.project_id,
      application_id: config.toolkit_id,
    },
    parameters: params,
  };

  // Use /tools/... (not /ui/tools) - the ui_host proxy adds /ui prefix
  const response = await providerRequest(`/tools/inventory/${toolName}/invoke`, {
    method: 'POST',
    body: JSON.stringify(requestBody),
  });

  // Provider always returns invocation_id, so always poll for completion
  if (response.invocation_id) {
    return pollProviderInvocation('inventory', toolName, response.invocation_id);
  }

  return response;
}

/**
 * Start a tool invocation and return the invocation_id immediately (no polling)
 * Use this when you need to track/stop the task later
 */
export async function startProviderTool(toolName, params) {
  const config = getConfig();

  const requestBody = {
    configuration: {
      project_id: config.project_id,
      application_id: config.toolkit_id,
    },
    parameters: params,
  };

  const response = await providerRequest(`/tools/inventory/${toolName}/invoke`, {
    method: 'POST',
    body: JSON.stringify(requestBody),
  });

  return response; // Returns { invocation_id: '...' }
}

/**
 * Poll a provider invocation with abort support
 * @param {string} invocationId - The invocation ID to poll
 * @param {AbortSignal} signal - Optional AbortSignal to cancel polling
 */
export async function pollProviderToolStatus(invocationId, signal = null) {
  return pollProviderInvocation('inventory', 'run_ingestion', invocationId, 36000000, signal);
}

/**
 * Stop a running task by invocation_id
 * Calls the provider's stop endpoint
 */
export async function stopProviderTask(invocationId) {
  try {
    await providerRequest(`/tools/inventory/run_ingestion/invocations/${invocationId}`, {
      method: 'DELETE',
    });
    return { success: true };
  } catch (err) {
    console.error('Failed to stop task:', err);
    return { success: false, error: err.message };
  }
}

/**
 * Poll provider invocation status until complete
 * @param {AbortSignal} signal - Optional AbortSignal to cancel polling
 */
async function pollProviderInvocation(toolkitName, toolName, invocationId, timeoutMs = 36000000, signal = null) {
  const startTime = Date.now();
  const pollInterval = 2000; // 2 seconds

  while (Date.now() - startTime < timeoutMs) {
    // Check if aborted
    if (signal?.aborted) {
      throw new Error('Polling aborted');
    }

    const status = await providerRequest(
      `/tools/${toolkitName}/${toolName}/invocations/${invocationId}`
    );

    console.log(`[Provider] Poll status: ${status.status}`);

    if (status.status === 'Completed' || status.status === 'completed') {
      return parseToolResult(status);
    }

    if (status.status === 'Error' || status.status === 'error' ||
        status.status === 'Failed' || status.status === 'failed' ||
        status.status === 'Stopped' || status.status === 'stopped') {
      // Try to parse the error from result
      const errorMsg = status.error || status.result || 'Provider returned error status';
      throw new Error(typeof errorMsg === 'string' ? errorMsg : JSON.stringify(errorMsg));
    }

    // Wait before next poll (with abort support)
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(resolve, pollInterval);
      if (signal) {
        signal.addEventListener('abort', () => {
          clearTimeout(timeout);
          reject(new Error('Polling aborted'));
        }, { once: true });
      }
    });
  }

  throw new Error('Provider invocation timed out after 10 hours');
}

/**
 * Get toolkit details from platform API
 */
export async function getToolkit(projectId, toolkitId) {
  return apiRequest(`/api/v2/elitea_core/tool/prompt_lib/${projectId}/${toolkitId}`);
}

/**
 * Get toolkit config for tool invocation
 * Uses cached toolkit data if available, otherwise fetches it
 */
async function getToolkitConfig(projectId, toolkitId) {
  // Use cached toolkit if available
  let toolkit = toolkitCache;
  const cacheKey = `${projectId}-${toolkitId}`;

  // Only fetch if not cached or different toolkit
  if (!toolkit || toolkitCacheKey !== cacheKey) {
    toolkit = await getToolkit(projectId, toolkitId);
    setToolkitCache(projectId, toolkitId, toolkit);
  }

  // Build toolkit_config from toolkit details
  // The format matches what the platform expects for test_toolkit_tool
  return {
    toolkit_id: toolkit.id,
    type: toolkit.type,  // Important: Include toolkit type for routing
    toolkit_name: toolkit.type || toolkit.toolkit_name,
    settings: toolkit.settings || {},
    // Include provider info for external provider routing
    // Provider can be at toolkit level, in settings, or in meta
    provider: toolkit.provider || toolkit.settings?.provider || toolkit.meta?.provider,
    // Include any additional fields the toolkit needs
    ...toolkit.meta,
  };
}

/**
 * Invoke a tool using the platform's test_toolkit_tool API
 * This is the correct way to invoke external provider tools
 */
export async function invokeTool(projectId, toolkitId, toolName, params, timeout = 300) {
  const toolkitConfig = await getToolkitConfig(projectId, toolkitId);

  // Use the platform's test_toolkit_tool API with await_response=true for sync calls
  // URL pattern: /api/v2/elitea_core/test_toolkit_tool/prompt_lib/{project_id}
  return apiRequest(`/api/v2/elitea_core/test_toolkit_tool/prompt_lib/${projectId}?await_response=true&timeout=${timeout}`, {
    method: 'POST',
    body: JSON.stringify({
      toolkit_config: toolkitConfig,
      tool_name: toolName,
      tool_params: params || {},
    }),
  });
}

/**
 * Invoke a tool asynchronously
 * Returns a task_id that can be polled for status
 */
export async function invokeToolAsync(projectId, toolkitId, toolName, params) {
  const toolkitConfig = await getToolkitConfig(projectId, toolkitId);

  return apiRequest(`/api/v2/elitea_core/test_toolkit_tool/prompt_lib/${projectId}?await_response=false`, {
    method: 'POST',
    body: JSON.stringify({
      toolkit_config: toolkitConfig,
      tool_name: toolName,
      tool_params: params || {},
    }),
  });
}

/**
 * Get async task status
 */
export async function getTaskStatus(projectId, taskId) {
  return apiRequest(`/api/v2/elitea_core/application_task/prompt_lib/${projectId}/${taskId}?result=yes`);
}

/**
 * Search the knowledge graph
 */
export async function searchGraph(projectId, toolkitId, query, options = {}) {
  const params = {
    query,
    output_format: 'json',
    ...options,
  };

  return invokeProviderTool('search_graph', params);
}

/**
 * Get graph statistics
 */
export async function getGraphStats(projectId, toolkitId) {
  return invokeProviderTool('get_stats', { output_format: 'json' });
}

/**
 * Get entity details
 */
export async function getEntity(projectId, toolkitId, entityName) {
  return invokeProviderTool('get_entity', {
    entity_name: entityName,
    include_relations: true,
    output_format: 'json',
  });
}

/**
 * List entities by type
 */
export async function listEntitiesByType(projectId, toolkitId, entityType, limit = 50) {
  return invokeProviderTool('list_entities_by_type', {
    entity_type: entityType,
    limit,
    output_format: 'json',
  });
}

/**
 * List ingested sources
 */
export async function listIngestedSources(projectId, toolkitId) {
  return invokeProviderTool('list_ingested_sources', {
    output_format: 'json',
  });
}

/**
 * Get cache statistics
 */
export async function getCacheStats(projectId, toolkitId) {
  return invokeProviderTool('get_cache_stats', {
    output_format: 'json',
  });
}

/**
 * Get ingestion status for current project/toolkit
 * Returns information about active ingestions and available slots
 */
export async function getIngestionStatus(projectId, toolkitId) {
  return invokeProviderTool('get_ingestion_status', {
    output_format: 'json',
  });
}

/**
 * Get graph info
 */
export async function getGraphInfo(projectId, toolkitId) {
  return invokeProviderTool('get_graph_info', {
    output_format: 'json',
  });
}

/**
 * Get sources status from the knowledge graph
 * Returns status (pending/in_progress/completed/error), last update time, and entity counts
 */
export async function getSourcesStatus(projectId, toolkitId) {
  return invokeProviderTool('get_sources_status', {
    output_format: 'json',
  });
}

/**
 * Get entities by their IDs with connecting edges
 * Used to fetch and display entities that were touched during chat responses
 * @param {string} projectId - Project ID
 * @param {string} toolkitId - Toolkit ID
 * @param {string[]} entityIds - Array of entity IDs to fetch
 * @param {boolean} includeEdges - Whether to include edges connecting these entities (default: true)
 * @returns {Promise<Object>} { results: [...], edges: [...], total_entities, total_edges }
 */
export async function getEntitiesByIds(projectId, toolkitId, entityIds, includeEdges = true) {
  return invokeProviderTool('get_entities_by_ids', {
    entity_ids: entityIds,
    include_edges: includeEdges,
    output_format: 'json',
  });
}

/**
 * Get neighbors of an entity up to a specified depth level
 * Used by graph UI context menu to expand connections 1-3 levels deep
 * @param {string} projectId - Project ID
 * @param {string} toolkitId - Toolkit ID
 * @param {string} entityId - ID of the entity to expand from
 * @param {number} depth - Number of hops to expand (1, 2, or 3)
 * @returns {Promise<Object>} { results: [...], edges: [...], total_entities, total_edges, origin_entity_id, depth }
 */
export async function getEntityNeighbors(projectId, toolkitId, entityId, depth = 1) {
  return invokeProviderTool('get_entity_neighbors', {
    entity_id: entityId,
    depth: depth,
    output_format: 'json',
  });
}

/**
 * Get impact analysis
 */
export async function getImpactAnalysis(projectId, toolkitId, entityName, direction = 'downstream', maxDepth = 3) {
  const result = await invokeTool(projectId, toolkitId, 'impact_analysis', {
    entity_name: entityName,
    direction,
    max_depth: maxDepth,
    output_format: 'json',
  });
  return parseToolResult(result);
}

/**
 * Trigger graph reindex
 */
export async function reindexGraph(projectId, toolkitId, sourceId = null) {
  const params = sourceId
    ? { source_id: sourceId, output_format: 'json' }
    : { full_reindex: true, output_format: 'json' };

  const result = await invokeTool(projectId, toolkitId, 'reindex_graph', params, 600);
  return parseToolResult(result);
}

/**
 * Remove a source from the graph
 */
export async function removeSource(projectId, toolkitId, sourceId) {
  const result = await invokeTool(projectId, toolkitId, 'remove_source', {
    source_id: sourceId,
    output_format: 'json',
  });
  return parseToolResult(result);
}

/**
 * Chat/query the graph with natural language
 */
export async function chatQuery(projectId, toolkitId, query, context = {}) {
  const result = await invokeTool(projectId, toolkitId, 'chat_query', {
    query,
    context,
    output_format: 'json',
  }, 120);
  return parseToolResult(result);
}

/**
 * List available toolkits in the project (for adding to inventory)
 * Filters for repository toolkits that expose loader() and can be used as inventory data sources.
 */
export async function listAvailableToolkits(projectId) {
  const sourceTypes = ['github', 'ado_repos', 'azure_devops_repos', 'gitlab', 'bitbucket'];

  const response = await apiRequest(`/api/v2/elitea_core/tools/prompt_lib/${projectId}?limit=100`);

  // Filter to only include source-compatible toolkits
  const toolkits = Array.isArray(response) ? response : response?.rows || [];

  return toolkits.filter(toolkit => {
    const type = (toolkit.type || '').toLowerCase();
    return sourceTypes.some(sourceType => type.includes(sourceType));
  });
}

/**
 * Run ingestion from a source toolkit
 * Calls the inventory provider directly to avoid SDK routing issues
 */
export async function runIngestion(projectId, toolkitId, sourceToolkitId, options = {}) {
  // Get toolkit config for the ingestion parameters (bucket, graph_name, llm_model, etc.)
  const toolkit = toolkitCache || await getToolkit(projectId, toolkitId);
  const settings = toolkit?.settings || {};

  // Build toolkit configuration for the provider
  const toolkitConfig = {
    bucket: settings.toolkit_configuration_bucket || 'graphs',
    graph_name: settings.toolkit_configuration_graph_name || 'main',
    llm_model: settings.toolkit_configuration_llm_model || '',
    embedding_model: settings.toolkit_configuration_embedding_model || '',
  };

  const params = {
    toolkit_id: sourceToolkitId,
    output_format: 'json',
    ...options,
  };

  console.log('[runIngestion] Calling provider directly:', { toolkitConfig, params });

  // Invoke the tool directly on the provider
  const response = await invokeProviderTool('run_ingestion', params, toolkitConfig);

  // Poll for completion using provider's invocation API
  if (response.invocation_id) {
    return pollProviderInvocation('run_ingestion', response.invocation_id, 600000); // 10 min timeout
  }

  return response;
}

/**
 * Poll platform task status until complete
 */
async function pollTaskStatus(projectId, taskId, timeoutMs = 600000) {
  const startTime = Date.now();
  const pollInterval = 2000; // 2 seconds

  while (Date.now() - startTime < timeoutMs) {
    const status = await getTaskStatus(projectId, taskId);

    if (status.status === 'Completed' || status.status === 'completed') {
      return parseToolResult(status);
    }

    if (status.status === 'Error' || status.status === 'Failed' || status.status === 'error') {
      throw new Error(status.error || status.result || 'Ingestion failed');
    }

    // Wait before next poll
    await new Promise(resolve => setTimeout(resolve, pollInterval));
  }

  throw new Error('Ingestion timed out after 10 minutes');
}

/**
 * Remove entities from a source toolkit
 */
export async function removeSourceEntities(projectId, toolkitId, sourceToolkitId) {
  const result = await invokeTool(projectId, toolkitId, 'remove_source_entities', {
    toolkit_id: sourceToolkitId,
    output_format: 'json',
  });
  return parseToolResult(result);
}

/**
 * Update toolkit settings (add/remove sources)
 */
export async function updateToolkitSettings(projectId, toolkitId, settings) {
  // First get current toolkit data
  const toolkit = await getToolkit(projectId, toolkitId);

  // Merge new settings with existing
  const updatedToolkit = {
    ...toolkit,
    settings: {
      ...toolkit.settings,
      ...settings,
    },
  };

  // PUT the updated toolkit
  return apiRequest(`/api/v2/elitea_core/tool/prompt_lib/${projectId}/${toolkitId}`, {
    method: 'PUT',
    body: JSON.stringify(updatedToolkit),
  });
}

/**
 * Get sources from toolkit configuration
 */
export function getToolkitSources(toolkit) {
  return toolkit?.settings?.toolkit_configuration_sources || [];
}

/**
 * Save ingestion status - currently no-op
 * TODO: Implement proper persistence via ELITEA artifacts API when available
 */
export async function saveIngestionStatus(projectId, bucket, graphName, status) {
  // No-op for now - status is tracked in component state
  return Promise.resolve();
}

// ============================================================================
// CONVERSATION API - Chat functionality using platform conversation endpoints
// ============================================================================

/**
 * Create a new conversation for the inventory toolkit
 * @param {string} projectId - Project ID
 * @param {string} toolkitId - Toolkit ID to associate with conversation
 * @param {string} name - Conversation name (optional, defaults to "New Conversation")
 * @returns {Promise<Object>} Created conversation object
 */
export async function createConversation(projectId, toolkitId, name = 'New Conversation') {
  const conversation = await apiRequest(`/api/v2/elitea_core/conversations/prompt_lib/${projectId}`, {
    method: 'POST',
    body: JSON.stringify({
      name,
      is_private: true,
      meta: {
        toolkit_id: toolkitId,
        toolkit_type: 'inventory',
      },
      participants: [],
    }),
  });

  // Add the inventory toolkit as a participant
  if (conversation?.id) {
    await addParticipant(projectId, conversation.id, {
      type: 'toolkit',
      entity_id: parseInt(toolkitId, 10),
      entity_version_id: null,
      is_active: true,
      entity_meta: {
        project_id: parseInt(projectId, 10),
      },
    });

    // Fetch the updated conversation with participant
    return getConversation(projectId, conversation.id);
  }

  return conversation;
}

/**
 * List all conversations for a project, filtered by toolkit
 * @param {string} projectId - Project ID
 * @param {string} toolkitId - Toolkit ID to filter by
 * @param {number} limit - Max results (default 100)
 * @param {number} offset - Pagination offset (default 0)
 * @returns {Promise<Object>} { rows: [...], total: number }
 */
export async function listConversations(projectId, toolkitId, limit = 100, offset = 0) {
  const response = await apiRequest(
    `/api/v2/elitea_core/conversations/prompt_lib/${projectId}?limit=${limit}&offset=${offset}&sort_by=updated_at&sort_order=desc`
  );

  // Filter conversations by toolkit_id in meta
  const rows = Array.isArray(response) ? response : (response?.rows || []);
  const filteredRows = rows.filter(
    conv => conv.meta?.toolkit_id === toolkitId || conv.meta?.toolkit_id === parseInt(toolkitId, 10)
  );

  return {
    rows: filteredRows,
    total: filteredRows.length,
  };
}

/**
 * Get conversation details by ID
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @returns {Promise<Object>} Conversation object with participants
 */
export async function getConversation(projectId, conversationId) {
  return apiRequest(`/api/v2/elitea_core/conversation/prompt_lib/${projectId}/${conversationId}`);
}

/**
 * Delete a conversation
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @returns {Promise<void>}
 */
export async function deleteConversation(projectId, conversationId) {
  return apiRequest(`/api/v2/elitea_core/conversations/prompt_lib/${projectId}/${conversationId}`, {
    method: 'DELETE',
  });
}

/**
 * Update conversation name
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @param {string} name - New conversation name
 * @returns {Promise<Object>} Updated conversation
 */
export async function updateConversation(projectId, conversationId, { name }) {
  return apiRequest(`/api/v2/elitea_core/conversation/prompt_lib/${projectId}/${conversationId}`, {
    method: 'PUT',
    body: JSON.stringify({ name }),
  });
}

/**
 * Add a participant to a conversation
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @param {Object} participant - Participant object { type, entity_id, entity_version_id, is_active }
 * @returns {Promise<Object>} Updated participants list
 */
export async function addParticipant(projectId, conversationId, participant) {
  return apiRequest(`/api/v2/elitea_core/participants/prompt_lib/${projectId}/${conversationId}`, {
    method: 'POST',
    body: JSON.stringify([participant]),
  });
}

/**
 * Get messages for a conversation
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @param {number} page - Page number (0-indexed)
 * @param {number} pageSize - Messages per page (default 50)
 * @returns {Promise<Object>} { rows: [...], total: number }
 */
export async function getMessages(projectId, conversationId, page = 0, pageSize = 50) {
  return apiRequest(
    `/api/v2/elitea_core/messages/prompt_lib/${projectId}/${conversationId}?limit=${pageSize}&offset=${page * pageSize}`
  );
}

/**
 * Select a conversation (marks it as active for the user session)
 * @param {string} projectId - Project ID
 * @param {number} conversationId - Conversation ID
 * @returns {Promise<void>}
 */
export async function selectConversation(projectId, conversationId) {
  return apiRequest(`/api/v2/elitea_core/select_conversation/prompt_lib/${projectId}/${conversationId}`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/**
 * Unselect current conversation
 * @param {string} projectId - Project ID
 * @returns {Promise<void>}
 */
export async function unselectConversation(projectId) {
  return apiRequest(`/api/v2/elitea_core/select_conversation/prompt_lib/${projectId}`, {
    method: 'DELETE',
  });
}

/**
 * Parse tool result from the test_toolkit_tool API response
 *
 * The response format from test_toolkit_tool contains:
 * - result: The actual tool result (may be a JSON string)
 * - success: Boolean indicating success
 * - error: Error message if unsuccessful
 * - task_id: Task ID for tracking
 */
function parseToolResult(response) {
  console.log('[DEBUG] parseToolResult received:', response);
  
  if (!response) {
    return null;
  }

  // Check for error response
  if (response.error) {
    console.error('Tool invocation error:', response.error);
    throw new Error(response.error);
  }

  // Handle test_toolkit_tool response format
  const result = response.result;
  if (!result) {
    return null;
  }

  console.log('[DEBUG] parseToolResult - result type:', typeof result, 'isArray:', Array.isArray(result));

  try {
    // If result is an array (provider response format)
    if (Array.isArray(result)) {
      console.log('[DEBUG] parseToolResult - processing array, length:', result.length);
      const messageObj = result.find(obj => obj && obj.object_type === 'message');
      
      if (messageObj) {
        console.log('[DEBUG] parseToolResult - found message object:', messageObj);
        
        // Try to get data from various possible fields
        const data = messageObj.data || messageObj.content || messageObj.result;
        
        if (data) {
          console.log('[DEBUG] parseToolResult - data type:', typeof data);
          
          // If data is a string, try to parse it as JSON
          if (typeof data === 'string') {
            try {
              const parsed = JSON.parse(data);
              console.log('[DEBUG] parseToolResult - successfully parsed data as JSON');
              return parsed;
            } catch (e) {
              console.log('[DEBUG] parseToolResult - data is string but not JSON, returning raw');
              return data;
            }
          }
          
          // Data is already an object
          console.log('[DEBUG] parseToolResult - returning data object');
          return data;
        }
      }
      
      console.log('[DEBUG] parseToolResult - no message object found, returning array');
      return result;
    }
    
    // If result is a string, try to parse it as JSON
    if (typeof result === 'string') {
      console.log('[DEBUG] parseToolResult - result is string, attempting parse');
      const parsed = JSON.parse(result);
      console.log('[DEBUG] parseToolResult - parsed string, type:', typeof parsed, 'isArray:', Array.isArray(parsed));

      // Handle result_objects array format
      if (Array.isArray(parsed)) {
        console.log('[DEBUG] parseToolResult - parsed is array, length:', parsed.length);
        const messageObj = parsed.find(obj => obj.object_type === 'message');
        
        if (messageObj) {
          console.log('[DEBUG] parseToolResult - found message object, keys:', Object.keys(messageObj));
          
          if (messageObj.data) {
            console.log('[DEBUG] parseToolResult - messageObj.data type:', typeof messageObj.data);
            try {
              const finalData = JSON.parse(messageObj.data);
              console.log('[DEBUG] parseToolResult - successfully parsed messageObj.data, returning:', finalData);
              return finalData;
            } catch (e) {
              console.log('[DEBUG] parseToolResult - messageObj.data not JSON, returning raw');
              return messageObj.data;
            }
          }
        }
        
        console.log('[DEBUG] parseToolResult - no message object, returning array');
        return parsed;
      }

      console.log('[DEBUG] parseToolResult - parsed is not array, returning parsed');
      return parsed;
    }

    // If result is already an object, return it
    console.log('[DEBUG] parseToolResult - returning result as-is (object)');
    return result;
  } catch (e) {
    // If JSON parsing fails, return the raw result
    console.warn('Could not parse tool result as JSON, returning raw:', e);
    return result;
  }
}

/**
 * Get available chat models from the platform
 * @returns {Promise<Array>} Array of model objects with { id, name, display_name }
 */
export async function getChatModels() {
  const config = getConfig();
  const basePath = getProviderBasePath();

  try {
    const response = await apiRequest(`${basePath}/${config.toolkit_id}/chat/models`);
    return response.models || [];
  } catch (e) {
    console.error('[getChatModels] Error fetching models:', e);
    return [];
  }
}
