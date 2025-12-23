/**
 * Chat Predict hook for Inventory UI
 * Handles socket.io chat_predict events for streaming responses
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSocket, useManualSocket, sioEvents, SocketMessageType } from './useSocket';

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
 * Tool action type constants
 */
export const TOOL_ACTION_TYPES = {
  Tool: 'tool',
  Llm: 'llm',
};

/**
 * Convert various content types to string
 */
function convertToString(content, preserveNewlines = true) {
  if (typeof content === 'string') {
    return content;
  }
  if (content === null || content === undefined) {
    return '';
  }
  if (typeof content === 'object') {
    try {
      return JSON.stringify(content, null, 2);
    } catch {
      return String(content);
    }
  }
  return String(content);
}

/**
 * Hook for chat prediction with streaming support
 *
 * @param {Object} options
 * @param {Array} options.messages - Current messages array
 * @param {Function} options.setMessages - Setter for messages
 * @param {Object} options.participant - Active participant (toolkit)
 * @param {Function} options.onError - Error handler
 * @returns {Object} { emit, emitLeaveRoom, isStreaming, chatHistoryRef }
 */
export function useChatPredict({
  messages,
  setMessages,
  participant,
  onError,
}) {
  const [isStreaming, setIsStreaming] = useState(false);

  // Refs to avoid stale closures
  const messagesRef = useRef(messages);
  const participantRef = useRef(participant);
  const setMessagesRef = useRef(setMessages);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    participantRef.current = participant;
  }, [participant]);

  useEffect(() => {
    setMessagesRef.current = setMessages;
  }, [setMessages]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  /**
   * Find or create message in history by ID
   */
  const getMessage = useCallback((messageId, questionId) => {
    const history = messagesRef.current || [];
    const msgIdx = history.findIndex(
      (m) => m.id === messageId || (questionId && m.question_id === questionId)
    );

    if (msgIdx < 0) {
      // Create new message
      const msg = {
        id: messageId,
        role: ROLES.Assistant,
        content: '',
        isLoading: false,
        created_at: Date.now(),
        participant: participantRef.current,
      };
      return [-1, msg];
    }

    return [msgIdx, { ...history[msgIdx] }];
  }, []);

  /**
   * Add message to chat history
   */
  const addMessage = useCallback((msgIndex, msg, questionId) => {
    if (msgIndex === -1) {
      // New message - insert after question
      setMessagesRef.current?.((prev) => {
        if (questionId) {
          const questionIndex = prev.findIndex(
            (m) => m.role === ROLES.User && m.id === questionId
          );
          if (questionIndex !== -1) {
            const newState = [...prev];
            newState.splice(questionIndex + 1, 0, { ...msg, participant: participantRef.current });
            return newState;
          }
        }
        return [...prev, { ...msg, participant: participantRef.current }];
      });
    } else {
      // Update existing message
      setMessagesRef.current?.((prev) => {
        const newState = [...prev];
        newState[msgIndex] = { ...msg };
        return newState;
      });
    }
  }, []);

  /**
   * Handle socket events
   */
  const handleSocketEvent = useCallback((message) => {
    const {
      message_id,
      type: socketMessageType,
      response_metadata,
      question_id: questionIdFromMessage,
    } = message;

    const {
      task_id,
      participant_id,
      question_id: questionIdFromContent,
    } = message.content instanceof Object ? message.content : {};

    const question_id = questionIdFromMessage || questionIdFromContent;
    const [msgIndex, msg] = getMessage(message_id, question_id);

    let toolAction;

    switch (socketMessageType) {
      case SocketMessageType.StartTask:
        msg.isLoading = true;
        msg.isStreaming = true;
        msg.content = '';
        msg.toolActions = [];
        msg.task_id = task_id;
        msg.participant_id = participant_id;
        msg.question_id = question_id;
        setIsStreaming(true);
        addMessage(msgIndex, msg, question_id);
        break;

      case SocketMessageType.Chunk:
      case SocketMessageType.AIMessageChunk:
      case SocketMessageType.AgentResponse:
        msg.content += convertToString(message.content, true);
        if (response_metadata?.finish_reason) {
          msg.isLoading = false;
          msg.isStreaming = false;
          setIsStreaming(false);
        }
        break;

      case SocketMessageType.AgentToolStart:
      case SocketMessageType.AgentLlmStart:
        if (!msg.toolActions) {
          msg.toolActions = [];
        }
        if (!msg.toolActions.find((t) => t.id === response_metadata?.tool_run_id)) {
          msg.toolActions.push({
            name: response_metadata?.tool_name,
            id: response_metadata?.tool_run_id,
            status: ToolActionStatus.processing,
            message: '',
            toolInputs: response_metadata?.tool_inputs,
            created_at: message.created_at,
            type: socketMessageType === SocketMessageType.AgentLlmStart
              ? TOOL_ACTION_TYPES.Llm
              : TOOL_ACTION_TYPES.Tool,
          });
        }
        break;

      case SocketMessageType.AgentToolEnd:
        toolAction = msg.toolActions?.find((t) => t.id === response_metadata?.tool_run_id);
        if (toolAction) {
          toolAction.content = convertToString(message?.content ?? '');
          toolAction.status = ToolActionStatus.complete;
          toolAction.ended_at = message.created_at;
          toolAction.toolOutputs = response_metadata?.tool_output;
        }
        break;

      case SocketMessageType.AgentToolError:
        toolAction = msg.toolActions?.find((t) => t.id === response_metadata?.tool_run_id);
        if (toolAction) {
          toolAction.content = convertToString(message?.content ?? '');
          toolAction.status = ToolActionStatus.error;
          toolAction.ended_at = message.created_at;
        }
        break;

      case SocketMessageType.AgentLlmChunk:
        toolAction = msg.toolActions?.find((t) => t.id === response_metadata?.tool_run_id);
        if (toolAction) {
          if (toolAction.content === undefined) {
            toolAction.content = '';
          }
          toolAction.content += convertToString(message.content, true);
        }
        break;

      case SocketMessageType.AgentLlmEnd:
        toolAction = msg.toolActions?.find((t) => t.id === response_metadata?.tool_run_id);
        if (toolAction) {
          toolAction.status = ToolActionStatus.complete;
          toolAction.ended_at = message.created_at;
        }
        break;

      case SocketMessageType.AgentThinkingStep:
      case SocketMessageType.AgentThinkingStepUpdate:
        toolAction = msg.toolActions?.find((t) => t.id === response_metadata?.tool_run_id);
        if (toolAction) {
          toolAction.message = convertToString(response_metadata?.message, true);
        }
        break;

      case SocketMessageType.References:
        msg.references = message.references;
        break;

      case SocketMessageType.Error:
      case SocketMessageType.LlmError:
        msg.isLoading = false;
        msg.isStreaming = false;
        msg.exception = message.content;
        setIsStreaming(false);
        onErrorRef.current?.({ data: message.content || [] });
        break;

      case SocketMessageType.AgentException:
        msg.isLoading = false;
        msg.isStreaming = false;
        msg.exception = message.content;
        setIsStreaming(false);
        break;

      case SocketMessageType.AgentStart:
        // Initialize if needed
        if (msgIndex === -1) {
          msg.isLoading = true;
          msg.isStreaming = true;
          msg.content = '';
          addMessage(msgIndex, msg, question_id);
        }
        break;

      default:
        console.log('[ChatPredict] Unknown message type:', socketMessageType);
        return;
    }

    // Update message in history
    if (msgIndex > -1) {
      setMessagesRef.current?.((prev) => {
        const newState = [...prev];
        newState[msgIndex] = { ...msg };
        return newState;
      });
    }
  }, [getMessage, addMessage]);

  // Subscribe to chat_predict events
  const { emit } = useSocket(sioEvents.chat_predict, handleSocketEvent);

  // Manual emit for leave_rooms
  const { emit: emitLeaveRoom } = useManualSocket(sioEvents.chat_leave_rooms);

  /**
   * Send a chat message
   *
   * @param {Object} payload - Message payload
   * @param {string} payload.conversation_uuid - Conversation UUID
   * @param {string} payload.project_id - Project ID
   * @param {string} payload.participant_id - Participant ID (toolkit)
   * @param {Object} payload.payload - Message content
   * @param {string} payload.payload.user_input - User's message text
   */
  const sendMessage = useCallback((payload) => {
    console.log('[ChatPredict] Sending message:', payload);
    return emit(payload);
  }, [emit]);

  /**
   * Stop streaming by leaving the room
   *
   * @param {Array<string>} roomIds - Room IDs to leave
   */
  const stopStreaming = useCallback((roomIds) => {
    console.log('[ChatPredict] Leaving rooms:', roomIds);
    setIsStreaming(false);
    emitLeaveRoom(roomIds);

    // Update messages to stop streaming
    setMessagesRef.current?.((prev) =>
      prev.map((msg) => ({
        ...msg,
        isStreaming: false,
        isLoading: false,
        task_id: undefined,
      }))
    );
  }, [emitLeaveRoom]);

  return {
    sendMessage,
    stopStreaming,
    isStreaming,
    messagesRef,
    emit,
    emitLeaveRoom,
  };
}

export default useChatPredict;
