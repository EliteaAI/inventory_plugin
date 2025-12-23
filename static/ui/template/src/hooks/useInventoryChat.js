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
import { apiRequest, getProviderBasePath } from '../utils/api';

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
 * useInventoryChat hook
 *
 * @param {Object} options
 * @param {number} options.projectId - Project ID
 * @param {number} options.toolkitId - Inventory toolkit ID
 * @param {Object} options.filters - Current filters (entity_types, sources, layers)
 * @param {Function} options.onTouchedEntities - Callback when entities are accessed during chat
 * @returns {Object} Chat state and functions
 */
export function useInventoryChat({ projectId, toolkitId, filters = {}, onTouchedEntities }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);

  // Refs for stable references
  const messagesRef = useRef(messages);
  const abortControllerRef = useRef(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  /**
   * Load chat history from the inventory backend
   */
  const loadHistory = useCallback(async () => {
    if (!projectId || !toolkitId) return;

    setIsLoading(true);
    setError(null);

    try {
      const basePath = getProviderBasePath();
      // Route pattern: /ui/{toolkit_id}/chat/history
      const response = await apiRequest(
        `${basePath}/${toolkitId}/chat/history`,
        { method: 'GET' }
      );

      if (response.history) {
        setMessages(response.history.map((msg) => ({
          ...msg,
          id: msg.id || uuidv4(),
        })));
      }
    } catch (err) {
      console.error('[useInventoryChat] Failed to load history:', err);
      // Don't set error - empty history is fine
    } finally {
      setIsLoading(false);
    }
  }, [projectId, toolkitId]);

  /**
   * Save a message to history
   */
  const saveMessage = useCallback(async (message) => {
    if (!projectId || !toolkitId) return;

    try {
      const basePath = getProviderBasePath();
      // Route pattern: /ui/{toolkit_id}/chat/history
      await apiRequest(`${basePath}/${toolkitId}/chat/history`, {
        method: 'POST',
        body: JSON.stringify({
          role: message.role,
          content: message.content,
          citations: message.citations,
          tool_calls: message.toolCalls,
        }),
      });
    } catch (err) {
      console.error('[useInventoryChat] Failed to save message:', err);
    }
  }, [projectId, toolkitId]);

  /**
   * Clear chat history
   */
  const clearHistory = useCallback(async () => {
    if (!projectId || !toolkitId) return;

    try {
      const basePath = getProviderBasePath();
      // Route pattern: /ui/{toolkit_id}/chat/history
      await apiRequest(`${basePath}/${toolkitId}/chat/history`, {
        method: 'DELETE',
      });
      setMessages([]);
    } catch (err) {
      console.error('[useInventoryChat] Failed to clear history:', err);
      setError('Failed to clear history');
    }
  }, [projectId, toolkitId]);

  /**
   * Send a message and get a response
   *
   * Uses SSE streaming endpoint for real-time updates
   */
  const sendMessage = useCallback(async (userInput) => {
    if (!projectId || !toolkitId || !userInput.trim()) return;

    // Cancel any pending request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

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

    // Add user message immediately
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    setError(null);

    // Save user message to history
    saveMessage(userMessage);

    // Build history for context (last 10 messages)
    const history = messagesRef.current.slice(-10).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    try {
      // Use streaming endpoint
      // Route pattern: /ui/{toolkit_id}/chat/stream
      abortControllerRef.current = new AbortController();
      const basePath = getProviderBasePath();

      const response = await fetch(`${basePath}/${toolkitId}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          project_id: projectId,
          toolkit_id: toolkitId,
          prompt: userInput.trim(),
          filters,
          history,
        }),
        signal: abortControllerRef.current.signal,
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      // Read SSE stream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete events (separated by double newlines)
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep incomplete event in buffer

        for (const eventBlock of events) {
          if (!eventBlock.trim()) continue;

          const lines = eventBlock.split('\n');
          let eventType = '';
          let eventData = '';

          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              eventData = line.slice(5).trim();
            }
          }

          if (eventType && eventData) {
            try {
              const data = JSON.parse(eventData);
              handleStreamEvent(eventType, data, assistantMessage.id);
            } catch (e) {
              console.warn('[useInventoryChat] Failed to parse event:', e);
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        console.log('[useInventoryChat] Request cancelled');
      } else {
        console.error('[useInventoryChat] Error:', err);
        setError(err.message);

        // Update assistant message with error
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessage.id
              ? { ...m, isStreaming: false, error: err.message }
              : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }, [projectId, toolkitId, filters, saveMessage]);

  /**
   * Handle streaming events from SSE
   */
  const handleStreamEvent = useCallback((eventType, data, messageId) => {
    setMessages((prev) => {
      const msgIndex = prev.findIndex((m) => m.id === messageId);
      if (msgIndex === -1) return prev;

      const newMessages = [...prev];
      const msg = { ...newMessages[msgIndex] };

      switch (eventType) {
        case 'tool_start':
          if (!msg.toolActions) msg.toolActions = [];
          msg.toolActions.push({
            id: data.run_id,
            name: data.tool_name,
            status: ToolActionStatus.processing,
            input: data.input,
            created_at: data.timestamp,
          });
          break;

        case 'tool_end':
          if (msg.toolActions) {
            const action = msg.toolActions.find((a) => a.id === data.run_id);
            if (action) {
              action.status = ToolActionStatus.complete;
              action.output = data.output_preview;
              action.duration_ms = data.duration_ms;
            }
          }
          break;

        case 'tool_error':
          if (msg.toolActions) {
            const action = msg.toolActions.find((a) => a.id === data.run_id);
            if (action) {
              action.status = ToolActionStatus.error;
              action.error = data.error;
            }
          }
          break;

        case 'llm_start':
          if (!msg.toolActions) msg.toolActions = [];
          // Use model name directly, or just "LLM" if not provided
          const modelName = data.model && data.model !== 'LLM' ? data.model : 'LLM';
          msg.toolActions.push({
            id: data.run_id,
            name: modelName,
            status: ToolActionStatus.processing,
            type: 'llm',
            created_at: data.timestamp,
          });
          break;

        case 'llm_token':
          // For streaming tokens - could update a progress indicator
          break;

        case 'llm_end':
          if (msg.toolActions) {
            const action = msg.toolActions.find((a) => a.id === data.run_id);
            if (action) {
              action.status = ToolActionStatus.complete;
              action.output = data.output || '';
              action.duration_ms = data.duration_ms || 0;
            }
          }
          break;

        case 'agent_action':
          // Agent decided to use a tool
          console.log('[useInventoryChat] Agent action:', data.tool);
          break;

        case 'thinking_step':
          // Tool is thinking/processing
          if (!msg.thinkingSteps) msg.thinkingSteps = [];
          msg.thinkingSteps.push({
            id: Date.now(),
            message: data.message,
            tool_name: data.tool_name,
            toolkit: data.toolkit,
            timestamp: data.timestamp,
          });
          break;

        case 'chat_result':
          // Final result
          msg.content = data.answer || '';
          msg.citations = data.citations || [];
          msg.toolCalls = data.tool_calls || [];
          msg.touchedEntities = data.touched_entities || [];
          msg.isStreaming = false;
          msg.error = data.error || null;
          // Calculate duration if we have a start time
          if (msg.started_at) {
            msg.duration_ms = Date.now() - msg.started_at;
          }

          // Notify parent about touched entities for graph highlighting
          if (data.touched_entities && data.touched_entities.length > 0 && onTouchedEntities) {
            onTouchedEntities(data.touched_entities);
          }

          // Save assistant message to history
          saveMessage(msg);
          break;

        case 'done':
          msg.isStreaming = false;
          break;

        case 'error':
          msg.isStreaming = false;
          msg.error = data.error;
          break;

        default:
          console.log('[useInventoryChat] Unknown event:', eventType, data);
      }

      newMessages[msgIndex] = msg;
      return newMessages;
    });
  }, [saveMessage, onTouchedEntities]);

  /**
   * Cancel ongoing request
   */
  const cancelRequest = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);

    // Mark streaming messages as cancelled
    setMessages((prev) =>
      prev.map((m) =>
        m.isStreaming ? { ...m, isStreaming: false, content: m.content || '(Cancelled)' } : m
      )
    );
  }, []);

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
