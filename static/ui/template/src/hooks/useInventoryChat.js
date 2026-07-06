/**
 * useInventoryChat - Hook for inventory-specific chat functionality
 *
 * This hook connects to the inventory plugin's chat endpoint, which:
 * - Uses the inventory toolkit's LLM configuration
 * - Auto-compiles tools (graph search, entity query, source hybrid search)
 * - Returns structured responses with citations
 * - Streams intermediate steps (tool calls, LLM progress)
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { getToolkitConfig, stopPlatformTask } from '../utils/api';
import { useManualSocket, sioEvents, SocketMessageType } from './useSocket';

/**
 * Role constants for messages
 */
export const ROLES = {
  User: 'user',
  Assistant: 'assistant',
};

/**
 * Tool action status constants
 */
export const ToolActionStatus = {
  processing: 'processing',
  complete: 'complete',
  error: 'error',
};

/**
 * Robustly extract the structured `ask` result from a platform agent_response /
 * full_message payload. The backend `_tool_ask` returns a JSON string
 * {answer, citations, tool_calls, touched_entities, tokens_in, tokens_out, error};
 * the platform may deliver it as that JSON string, as an array of result objects
 * ([{ object_type, result_target, data }]), or as an already-parsed object.
 *
 * @param {*} content
 * @returns {{answer, citations, toolCalls, touchedEntities, tokensIn, tokensOut, error}|null}
 */
function parseAskResult(content) {
  if (content == null) return null;

  // Unwrap platform result-object arrays: [{ object_type, data }]
  if (Array.isArray(content)) {
    const resObj =
      content.find((o) => o && (o.object_type === 'answer' || o.object_type === 'message')) ||
      content[0];
    if (resObj && resObj.data != null) {
      return parseAskResult(resObj.data);
    }
    return null;
  }

  let obj = null;
  if (typeof content === 'string') {
    const trimmed = content.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) return parseAskResult(parsed);
        if (parsed && typeof parsed === 'object') obj = parsed;
      } catch (e) {
        // not JSON
      }
    }
    if (!obj) {
      // Plain text answer.
      return {
        answer: content,
        citations: [],
        toolCalls: [],
        touchedEntities: [],
        tokensIn: 0,
        tokensOut: 0,
        error: null,
      };
    }
  } else if (typeof content === 'object') {
    obj = content;
  }

  if (!obj) return null;

  return {
    answer: obj.answer ?? obj.result ?? obj.message ?? obj.data ?? '',
    citations: obj.citations ?? [],
    toolCalls: obj.tool_calls ?? obj.toolCalls ?? [],
    touchedEntities: obj.touched_entities ?? obj.touchedEntities ?? [],
    tokensIn: obj.tokens_in ?? 0,
    tokensOut: obj.tokens_out ?? 0,
    error: obj.error ?? null,
  };
}

function formatToolValue(value) {
  if (value == null || value === '') return undefined;
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function mapToolActionStatus(status) {
  const normalized = String(status || '').toLowerCase();
  if (['complete', 'completed', 'success', 'succeeded', 'done'].includes(normalized)) {
    return ToolActionStatus.complete;
  }
  if (['error', 'failed', 'failure'].includes(normalized)) {
    return ToolActionStatus.error;
  }
  return ToolActionStatus.processing;
}

function isAskWrapperTool(responseMetadata = {}) {
  const rawName = responseMetadata.tool_name || responseMetadata.name || '';
  const cleanName = String(rawName).split('___').pop();
  return cleanName === 'ask';
}

function normalizeDurationMs(value, startedAt) {
  if (value !== undefined && value !== null && value !== '') {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return Math.max(0, Math.round(numeric));
  }
  if (startedAt) return Math.max(0, Date.now() - startedAt);
  return undefined;
}

function toolCallsToActions(toolCalls = []) {
  if (!Array.isArray(toolCalls)) return [];
  return toolCalls.map((toolCall, index) => ({
    id: toolCall.id || toolCall.run_id || `result-tool-${index}`,
    name: toolCall.tool || toolCall.name || toolCall.tool_name || 'tool',
    status: ToolActionStatus.complete,
    input: formatToolValue(toolCall.input ?? toolCall.args ?? toolCall.tool_input),
    output: formatToolValue(toolCall.output_preview ?? toolCall.output ?? toolCall.result),
    error: formatToolValue(toolCall.error),
    duration_ms: normalizeDurationMs(toolCall.duration_ms),
  }));
}

/**
 * useInventoryChat hook
 *
 * @param {Object} options
 * @param {number} options.projectId - Project ID
 * @param {number} options.toolkitId - Inventory toolkit ID
 * @param {Object} options.filters - Current filters (entity_types, sources, layers)
 * @param {Function} options.onTouchedEntities - Callback when entities are accessed during chat
 * @param {string} options.model - Optional model name to use for chat
 * @returns {Object} Chat state and functions
 */
export function useInventoryChat({ projectId, toolkitId, filters = {}, onTouchedEntities, model }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);

  // Stable refs read inside the (intentionally stable) socket handler.
  const messagesRef = useRef(messages);
  const currentStreamIdRef = useRef(null);    // active test_toolkit_tool stream room
  const currentAssistantIdRef = useRef(null); // message currently being streamed into
  const currentTaskIdRef = useRef(null);      // platform task id (for cancellation)
  const requestDoneRef = useRef(false);       // guards double-finalize
  const onTouchedEntitiesRef = useRef(onTouchedEntities);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    onTouchedEntitiesRef.current = onTouchedEntities;
  }, [onTouchedEntities]);

  // localStorage key for chat persistence. Replaces the bespoke /chat/history
  // SSE backend route: the multi-tenant test_toolkit_tool transport carries no
  // server-side session state, so history lives client-side (deepwiki parity).
  const storageKey = `inventory-chat-${projectId}-${toolkitId}`;

  /**
   * Apply an update to the in-flight assistant message (by id).
   */
  const updateAssistant = useCallback((assistantId, updater) => {
    setMessages((prev) => prev.map((m) => (m.id === assistantId ? updater(m) : m)));
  }, []);

  /**
   * Finalize the assistant message with the parsed `ask` result.
   */
  const finalizeAssistant = useCallback((assistantId, result) => {
    if (requestDoneRef.current) return;
    requestDoneRef.current = true;
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== assistantId) return m;
        const streamedActions = (m.toolActions || []).filter((action) => !isAskWrapperTool({ tool_name: action.name }));
        const fallbackActions = toolCallsToActions(result.toolCalls);
        return {
          ...m,
          content: result.answer || m.content || '',
          citations: result.citations || [],
          toolCalls: result.toolCalls || [],
          toolActions: streamedActions.length > 0 ? streamedActions : fallbackActions,
          touchedEntities: result.touchedEntities || [],
          tokens_in: result.tokensIn || 0,
          tokens_out: result.tokensOut || 0,
          isStreaming: false,
          error: result.error || null,
          duration_ms: m.started_at ? Date.now() - m.started_at : m.duration_ms,
        };
      })
    );
    if (result.touchedEntities && result.touchedEntities.length > 0 && onTouchedEntitiesRef.current) {
      onTouchedEntitiesRef.current(result.touchedEntities);
    }
  }, []);

  /**
   * Finalize the assistant message with an error.
   */
  const finalizeAssistantError = useCallback((assistantId, message) => {
    if (requestDoneRef.current) return;
    requestDoneRef.current = true;
    setIsStreaming(false);
    setError(message);
    setMessages((prev) =>
      prev.map((m) => (m.id === assistantId ? { ...m, isStreaming: false, error: message } : m))
    );
  }, []);

  /**
   * Stable handler for streamed `test_toolkit_tool` socket events.
   *
   * Mirrors the deepwiki ChatDrawer pattern: filter by stream_id room, then map
   * the platform SocketMessageType events to the SAME message shape the old SSE
   * handler produced (toolActions / thinkingSteps / citations / content /
   * touchedEntities) so ChatPanel / ChatMessage stay unchanged.
   */
  const handleSocketMessage = useCallback((data) => {
    const { content, type: socketMessageType, response_metadata } = data || {};
    const rm = response_metadata || {};

    // Room isolation: ignore events from other streams.
    const messageStreamId = rm.stream_id;
    if (messageStreamId && currentStreamIdRef.current && messageStreamId !== currentStreamIdRef.current) {
      return;
    }

    const assistantId = currentAssistantIdRef.current;
    if (!assistantId) return;

    switch (socketMessageType) {
      case SocketMessageType.StartTask:
        currentTaskIdRef.current = rm.task_id || data?.task_id || (content && content.task_id) || null;
        break;

      case SocketMessageType.AgentToolStart: {
        // Cross-process, the `agent_tool_start` event the platform sees here is the outer
        // inventory `ask` wrapper that test_toolkit_tool invokes; it's noise. The
        // real inner steps arrive as `agent_on_tool_node` events (below). Suppress the
        // wrapper so the chat shows only meaningful tool / LLM chips.
        if (isAskWrapperTool(rm)) break;
        updateAssistant(assistantId, (m) => {
          const runId = rm.tool_run_id || rm.run_id;
          const toolActions = m.toolActions ? [...m.toolActions] : [];
          // Guard against duplicate start events for the same tool run.
          if (runId && toolActions.some((a) => a.id === runId)) return m;
          toolActions.push({
            id: runId || `tool-${Date.now()}`,
            name: (rm.tool_name || 'tool').split('___').pop(),
            status: ToolActionStatus.processing,
            input: rm.tool_input || rm.input,
            started_at: Date.now(),
          });
          return { ...m, toolActions };
        });
        break;
      }

      case SocketMessageType.AgentToolEnd: {
        if (isAskWrapperTool(rm)) break;
        const runId = rm.tool_run_id || rm.run_id;
        updateAssistant(assistantId, (m) => {
          if (!m.toolActions) return m;
          const toolActions = m.toolActions.map((a) =>
            a.id === runId
              ? {
                  ...a,
                  status: ToolActionStatus.complete,
                  output: rm.tool_result || rm.output,
                  duration_ms: normalizeDurationMs(rm.duration_ms ?? rm.execution_time_ms, a.started_at),
                }
              : a
          );
          return { ...m, toolActions };
        });
        break;
      }

      case SocketMessageType.AgentToolError: {
        if (isAskWrapperTool(rm)) break;
        const runId = rm.tool_run_id || rm.run_id;
        updateAssistant(assistantId, (m) => {
          if (!m.toolActions) return m;
          const toolActions = m.toolActions.map((a) =>
            a.id === runId
              ? {
                  ...a,
                  status: ToolActionStatus.error,
                  error: rm.error || rm.tool_result,
                  duration_ms: normalizeDurationMs(rm.duration_ms ?? rm.execution_time_ms, a.started_at),
                }
              : a
          );
          return { ...m, toolActions };
        });
        break;
      }

      case SocketMessageType.AgentOnToolNode: {
        // An inner agent step streamed from the inventory container: a knowledge-graph
        // tool the LLM called, or the LLM node itself. Carries the tool/model name,
        // tool inputs for real tool calls, and optional output (`tool_result`). Start
        // and completion share `state.run_id`, so correlate them into a single chip.
        const eventMetadata = rm.input_variables || rm.state || rm.tool_result != null
          ? rm
          : (content && typeof content === 'object' ? content : {});
        const iv = eventMetadata.input_variables || eventMetadata.inputVariables || {};
        const st = eventMetadata.state || {};
        const isLlmStep = iv.kind === 'llm';
        const runId = st.run_id || eventMetadata.tool_run_id || eventMetadata.run_id || `node-${Date.now()}`;
        const status = mapToolActionStatus(st.status);
        const toolResult = eventMetadata.tool_result ?? eventMetadata.toolResult;
        const now = Date.now();
        updateAssistant(assistantId, (m) => {
          const toolActions = m.toolActions ? [...m.toolActions] : [];
          const idx = toolActions.findIndex((a) => a.id === runId);
          const prev = idx >= 0 ? toolActions[idx] : null;
          const startedAt = prev?.started_at || (status === ToolActionStatus.processing ? now : undefined);
          const entry = {
            id: runId,
            name: iv.tool || prev?.name || (iv.kind === 'llm' ? 'LLM' : 'tool'),
            status,
            input: isLlmStep ? undefined : formatToolValue(iv.args ?? iv.input) ?? prev?.input,
            output: formatToolValue(toolResult) ?? prev?.output,
            started_at: startedAt,
            duration_ms:
              status === ToolActionStatus.processing
                ? prev?.duration_ms
                : normalizeDurationMs(st.duration_ms ?? eventMetadata.duration_ms, startedAt),
          };
          if (idx >= 0) toolActions[idx] = { ...prev, ...entry };
          else toolActions.push(entry);
          return { ...m, toolActions };
        });
        break;
      }

      case SocketMessageType.AgentThinkingStep:
      case SocketMessageType.AgentThinkingStepUpdate: {
        const stepMessage = rm.message || (typeof content === 'object' && content ? content.message : content);
        if (!stepMessage) break;
        updateAssistant(assistantId, (m) => {
          const thinkingSteps = m.thinkingSteps ? [...m.thinkingSteps] : [];
          thinkingSteps.push({
            id: `${Date.now()}-${thinkingSteps.length}`,
            message: stepMessage,
            tool_name: rm.tool_name,
            toolkit: rm.toolkit,
          });
          return { ...m, thinkingSteps };
        });
        break;
      }

      case SocketMessageType.AgentResponse: {
        const result = parseAskResult(content);
        if (result) finalizeAssistant(assistantId, result);
        break;
      }

      case SocketMessageType.FullMessage: {
        // Carries the full structured SDK result in response_metadata.test_result.
        const result = parseAskResult(rm.test_result != null ? rm.test_result : content);
        if (result) finalizeAssistant(assistantId, result);
        break;
      }

      case SocketMessageType.AgentException:
      case SocketMessageType.Error:
      case SocketMessageType.LlmError: {
        const errMessage =
          (typeof content === 'string' ? content : content && content.message) ||
          rm.error ||
          'An error occurred while processing your request';
        finalizeAssistantError(assistantId, errMessage);
        break;
      }

      default:
        break;
    }
  }, [updateAssistant, finalizeAssistant, finalizeAssistantError]);

  const {
    subscribe: subscribeTestToolkit,
    unsubscribe: unsubscribeTestToolkit,
    emit: emitTestToolkit,
  } = useManualSocket(sioEvents.test_toolkit_tool, handleSocketMessage);

  const { emit: emitLeaveRoom } = useManualSocket(sioEvents.test_toolkit_leave_room);

  // Subscribe to the streamed tool events for this hook's lifetime.
  useEffect(() => {
    subscribeTestToolkit();
    return () => unsubscribeTestToolkit();
  }, [subscribeTestToolkit, unsubscribeTestToolkit]);

  // Persist completed (non-streaming) messages to localStorage.
  useEffect(() => {
    if (!projectId || !toolkitId) return;
    try {
      const toPersist = messages.filter((m) => !m.isStreaming);
      if (toPersist.length > 0) {
        localStorage.setItem(storageKey, JSON.stringify(toPersist));
      }
    } catch (e) {
      // Ignore quota / serialization errors.
    }
  }, [messages, storageKey, projectId, toolkitId]);

  /**
   * Load chat history from localStorage.
   */
  const loadHistory = useCallback(async () => {
    if (!projectId || !toolkitId) return;
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          setMessages(parsed.map((msg) => ({ ...msg, id: msg.id || uuidv4(), isStreaming: false })));
        }
      }
    } catch (err) {
      console.warn('[useInventoryChat] Failed to load history:', err);
    }
  }, [projectId, toolkitId, storageKey]);

  /**
   * Clear chat history.
   */
  const clearHistory = useCallback(async () => {
    try {
      localStorage.removeItem(storageKey);
    } catch (err) {
      console.warn('[useInventoryChat] Failed to clear history:', err);
    }
    setMessages([]);
  }, [storageKey]);

  /**
   * Send a message and stream the answer via the platform `test_toolkit_tool`
   * socket transport. Multi-tenant: each request carries its own per-user
   * llm_settings (provider_worker-injected by the platform); nothing is
   * persisted server-side.
   */
  const sendMessage = useCallback(async (userInput) => {
    if (!projectId || !toolkitId || !userInput.trim()) return;

    const streamId = uuidv4();
    const messageId = uuidv4();
    currentStreamIdRef.current = streamId;
    currentTaskIdRef.current = null;
    requestDoneRef.current = false;

    const userMessage = {
      id: uuidv4(),
      role: ROLES.User,
      content: userInput.trim(),
      created_at: new Date().toISOString(),
    };

    const startTime = Date.now();
    const assistantMessage = {
      id: uuidv4(),
      role: ROLES.Assistant,
      content: '',
      isStreaming: true,
      toolActions: [],
      citations: [],
      created_at: new Date().toISOString(),
      started_at: startTime,
    };
    currentAssistantIdRef.current = assistantMessage.id;

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    setError(null);

    // Build prior-turn context (last 10 completed messages). JSON-encoded so it
    // survives the descriptor String arg-schema; parsed back in `_tool_ask`.
    const history = messagesRef.current
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }))
      .filter((m) => m.content);

    try {
      const toolkitConfig = await getToolkitConfig(projectId, toolkitId);
      const configuredModel =
        model ||
        toolkitConfig?.settings?.llm_model ||
        toolkitConfig?.settings?.toolkit_configuration_llm_model;

      const toolParams = {
        question: userInput.trim(),
        chat_history: JSON.stringify(history),
        project_id: Number(projectId),
        application_id: Number(toolkitId),
      };
      if (filters?.entity_types?.length) {
        toolParams.entity_types = filters.entity_types.join(',');
      }
      if (filters?.sources?.length) {
        toolParams.sources = filters.sources.join(',');
      }
      if (filters?.layers?.length) {
        toolParams.layers = filters.layers.join(',');
      }
      if (filters?.depth != null) {
        toolParams.depth = filters.depth;
      }
      if (filters?.max_nodes != null) {
        toolParams.max_nodes = filters.max_nodes;
      }

      const payload = {
        project_id: Number(projectId),
        stream_id: streamId,
        message_id: messageId,
        toolkit_config: {
          type: toolkitConfig.type,
          toolkit_name: toolkitConfig.toolkit_name,
          toolkit_id: Number(toolkitId),
          settings: toolkitConfig.settings || {},
        },
        tool_name: 'ask',
        tool_params: toolParams,
        llm_model: configuredModel,
        llm_settings: { model_name: configuredModel, max_tokens: 4096 },
      };

      // NOTE: do not subscribe here -- the hook subscribes once on mount. Calling
      // subscribe() again would register the same handler a second time on the
      // shared socket, so every streamed event would fire the handler twice
      // (duplicate tool chips, double finalize, etc.).
      emitTestToolkit(payload);
    } catch (err) {
      console.error('[useInventoryChat] Error:', err);
      finalizeAssistantError(assistantMessage.id, err.message || 'Failed to send message');
    }
  }, [projectId, toolkitId, filters, model, emitTestToolkit, finalizeAssistantError]);

  /**
   * Cancel the in-flight request: leave the stream room and stop the platform
   * task, then mark the streaming message as cancelled.
   */
  const cancelRequest = useCallback(() => {
    const streamId = currentStreamIdRef.current;
    if (streamId) {
      try {
        emitLeaveRoom({ stream_id: streamId, event_name: 'test_toolkit_tool' });
      } catch (e) {
        // best-effort
      }
    }
    const taskId = currentTaskIdRef.current;
    if (taskId && projectId) {
      stopPlatformTask(projectId, taskId).catch(() => {});
    }
    requestDoneRef.current = true;
    currentStreamIdRef.current = null;
    setIsStreaming(false);

    // Mark streaming messages as cancelled
    setMessages((prev) =>
      prev.map((m) =>
        m.isStreaming ? { ...m, isStreaming: false, content: m.content || '(Cancelled)' } : m
      )
    );
  }, [projectId, emitLeaveRoom]);

  /**
   * Add a message directly (for initial messages, etc.)
   */
  const addMessage = useCallback((message) => {
    setMessages((prev) => [...prev, { id: uuidv4(), ...message }]);
  }, []);

  // Load history on mount
  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  return {
    // State
    messages,
    isLoading,
    isStreaming,
    error,

    // Actions
    sendMessage,
    cancelRequest,
    clearHistory,
    addMessage,
    loadHistory,
    setMessages,
  };
}

export default useInventoryChat;
