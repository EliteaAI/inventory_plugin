/**
 * Conversations management hook for Inventory UI
 * Handles conversation CRUD, selection, and message loading
 */
import { useCallback, useEffect, useState, useRef } from 'react';
import {
  createConversation as apiCreateConversation,
  listConversations as apiListConversations,
  getConversation as apiGetConversation,
  deleteConversation as apiDeleteConversation,
  getMessages as apiGetMessages,
  selectConversation as apiSelectConversation,
} from '../utils/api';
import { ROLES } from './useChatPredict';

/**
 * Convert API message format to chat format
 */
function convertMessage(msg) {
  return {
    id: msg.uuid || msg.id,
    role: msg.role === 'user' ? ROLES.User : ROLES.Assistant,
    content: msg.content || '',
    created_at: msg.created_at ? new Date(msg.created_at).getTime() : Date.now(),
    participant: msg.participant,
    toolActions: msg.tool_actions || [],
    references: msg.references || [],
    name: msg.author_name || '',
    avatar: msg.author_avatar || '',
  };
}

/**
 * Hook for managing conversations
 *
 * @param {Object} options
 * @param {string} options.projectId - Project ID
 * @param {string} options.toolkitId - Toolkit ID
 * @param {Object} options.toolkit - Toolkit object (for participant info)
 * @returns {Object} Conversation state and actions
 */
export function useConversations({ projectId, toolkitId, toolkit }) {
  // State
  const [conversations, setConversations] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [error, setError] = useState(null);

  // Refs
  const isInitialized = useRef(false);

  /**
   * Load conversations list
   */
  const loadConversations = useCallback(async () => {
    if (!projectId || !toolkitId) return;

    setIsLoading(true);
    setError(null);

    try {
      const result = await apiListConversations(projectId, toolkitId);
      setConversations(result.rows || []);
    } catch (err) {
      console.error('[Conversations] Failed to load:', err);
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, [projectId, toolkitId]);

  /**
   * Load messages for a conversation
   */
  const loadMessages = useCallback(async (conversationId) => {
    if (!projectId || !conversationId) return;

    setIsLoadingMessages(true);

    try {
      const result = await apiGetMessages(projectId, conversationId);
      const msgs = Array.isArray(result) ? result : (result?.rows || []);

      // Convert and sort messages by created_at
      const convertedMessages = msgs
        .map(convertMessage)
        .sort((a, b) => a.created_at - b.created_at);

      setMessages(convertedMessages);
    } catch (err) {
      console.error('[Conversations] Failed to load messages:', err);
      setMessages([]);
    } finally {
      setIsLoadingMessages(false);
    }
  }, [projectId]);

  /**
   * Create a new conversation
   */
  const createNewConversation = useCallback(async (name = 'New Conversation') => {
    if (!projectId || !toolkitId) return null;

    setIsLoading(true);
    setError(null);

    try {
      const conversation = await apiCreateConversation(projectId, toolkitId, name);
      console.log('[Conversations] Created:', conversation);

      // Add to list and select
      setConversations((prev) => [conversation, ...prev]);
      setActiveConversation(conversation);
      setMessages([]);

      // Select conversation on platform
      if (conversation?.id) {
        await apiSelectConversation(projectId, conversation.id);
      }

      return conversation;
    } catch (err) {
      console.error('[Conversations] Failed to create:', err);
      setError(err.message);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [projectId, toolkitId]);

  /**
   * Select a conversation
   */
  const selectConversation = useCallback(async (conversation) => {
    if (!conversation) return;

    setActiveConversation(conversation);

    // Load full conversation details if needed
    if (conversation.id && !conversation.participants) {
      try {
        const fullConversation = await apiGetConversation(projectId, conversation.id);
        setActiveConversation(fullConversation);
        conversation = fullConversation;
      } catch (err) {
        console.error('[Conversations] Failed to get details:', err);
      }
    }

    // Load messages
    if (conversation.id) {
      await loadMessages(conversation.id);

      // Select conversation on platform
      try {
        await apiSelectConversation(projectId, conversation.id);
      } catch (err) {
        console.error('[Conversations] Failed to select:', err);
      }
    }
  }, [projectId, loadMessages]);

  /**
   * Delete a conversation
   */
  const deleteConversation = useCallback(async (conversationId) => {
    if (!projectId || !conversationId) return false;

    try {
      await apiDeleteConversation(projectId, conversationId);

      // Remove from list
      setConversations((prev) => prev.filter((c) => c.id !== conversationId));

      // If deleted active conversation, clear selection
      if (activeConversation?.id === conversationId) {
        setActiveConversation(null);
        setMessages([]);
      }

      return true;
    } catch (err) {
      console.error('[Conversations] Failed to delete:', err);
      setError(err.message);
      return false;
    }
  }, [projectId, activeConversation?.id]);

  /**
   * Get participant for chat_predict
   * Returns the inventory toolkit participant from the conversation
   */
  const getActiveParticipant = useCallback(() => {
    if (!activeConversation?.participants?.length) {
      // Return toolkit info as fallback
      return {
        id: null,
        type: 'toolkit',
        entity_id: parseInt(toolkitId, 10),
        entity_name: 'toolkit',
        entity_meta: toolkit || {},
        meta: {},
      };
    }

    // Find the toolkit participant
    const toolkitParticipant = activeConversation.participants.find(
      (p) => p.type === 'toolkit' && (p.entity_id === parseInt(toolkitId, 10) || p.entity_id === toolkitId)
    );

    return toolkitParticipant || activeConversation.participants[0];
  }, [activeConversation, toolkitId, toolkit]);

  /**
   * Add a user message to state (before sending)
   */
  const addUserMessage = useCallback((content) => {
    const id = crypto.randomUUID ? crypto.randomUUID() : `user-${Date.now()}`;
    const userMessage = {
      id,
      role: ROLES.User,
      content,
      created_at: Date.now(),
      isUser: true,
    };

    setMessages((prev) => [...prev, userMessage]);
    return userMessage;
  }, []);

  /**
   * Clear messages
   */
  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  // Initialize on mount - load conversations
  useEffect(() => {
    if (projectId && toolkitId && !isInitialized.current) {
      isInitialized.current = true;
      loadConversations();
    }
  }, [projectId, toolkitId, loadConversations]);

  return {
    // State
    conversations,
    activeConversation,
    messages,
    isLoading,
    isLoadingMessages,
    error,

    // Actions
    loadConversations,
    createNewConversation,
    selectConversation,
    deleteConversation,
    loadMessages,
    getActiveParticipant,
    addUserMessage,
    clearMessages,
    setMessages,
    setActiveConversation,
  };
}

export default useConversations;
