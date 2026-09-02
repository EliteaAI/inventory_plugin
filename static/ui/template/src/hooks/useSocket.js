/**
 * Socket.io hook for Inventory UI
 * Provides socket connection and event subscription utilities
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { io } from 'socket.io-client';

// Socket instance (singleton pattern for this app)
let socketInstance = null;

/**
 * Get or create socket.io connection
 * Uses platform's socket.io server with session auth
 */
function getSocket() {
  if (socketInstance && socketInstance.connected) {
    return socketInstance;
  }

  // Socket.io path on the platform
  const socketPath = '/socket.io';

  // Connect to same origin (platform proxy handles routing)
  const socketServer = window.location.origin;

  const ioOptions = {
    path: socketPath,
    reconnectionDelayMax: 2000,
    withCredentials: true, // Include session cookies
  };

  // Add dev token in development mode
  if (import.meta.env.DEV && import.meta.env.VITE_DEV_TOKEN) {
    ioOptions.extraHeaders = {
      Authorization: `Bearer ${import.meta.env.VITE_DEV_TOKEN}`,
    };
  }

  socketInstance = io(socketServer, ioOptions);

  socketInstance.on('connect', () => {
    console.log('[Socket] Connected to platform');
  });

  socketInstance.on('connect_error', (err) => {
    console.warn('[Socket] Connection error:', err.message);
  });

  socketInstance.on('disconnect', () => {
    console.log('[Socket] Disconnected');
  });

  return socketInstance;
}

/**
 * Socket.io event names
 */
export const sioEvents = {
  chat_predict: 'chat_predict',
  chat_leave_rooms: 'chat_leave_rooms',
  chat_enter_room: 'chat_enter_room',
  socket_validation_error: 'socket_validation_error',
  // Multi-tenant toolkit-tool transport (deepwiki parity). Every emit carries
  // its own per-user llm_settings (provider_worker-injected) so nothing is
  // persisted server-side. `test_toolkit_tool` runs a single tool invocation
  // (e.g. the inventory `ask` tool) inside a `stream_id`-keyed sio room.
  test_toolkit_tool: 'test_toolkit_tool',
  test_toolkit_enter_room: 'test_toolkit_enter_room',
  test_toolkit_leave_room: 'test_toolkit_leave_room',
  application_predict: 'application_predict',
};

/**
 * Socket message types (from platform)
 */
export const SocketMessageType = {
  StartTask: 'start_task',
  Chunk: 'chunk',
  AIMessageChunk: 'AIMessageChunk',
  AgentResponse: 'agent_response',
  AgentStart: 'agent_start',
  AgentToolStart: 'agent_tool_start',
  AgentToolEnd: 'agent_tool_end',
  AgentToolError: 'agent_tool_error',
  AgentOnToolNode: 'agent_on_tool_node',
  AgentLlmStart: 'agent_llm_start',
  AgentLlmChunk: 'agent_llm_chunk',
  AgentLlmEnd: 'agent_llm_end',
  AgentThinkingStep: 'agent_thinking_step',
  AgentThinkingStepUpdate: 'agent_thinking_step_update',
  AgentException: 'agent_exception',
  References: 'references',
  FullMessage: 'full_message',
  PartialMessage: 'partial_message',
  Error: 'error',
  LlmError: 'llm_error',
};

/**
 * Hook to use socket.io connection
 * Automatically subscribes to event on mount and unsubscribes on unmount
 *
 * @param {string} event - Socket event to subscribe to
 * @param {Function} handler - Event handler function
 * @returns {{ emit: Function, connected: boolean }}
 */
export function useSocket(event, handler) {
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);
  const handlerRef = useRef(handler);

  // Keep handler ref up to date
  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    const socket = getSocket();
    socketRef.current = socket;

    // Track connection state
    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    setConnected(socket.connected);

    // Subscribe to event
    const eventHandler = (data) => {
      handlerRef.current?.(data);
    };

    if (event && handler) {
      console.log('[Socket] Subscribing to', event);
      socket.on(event, eventHandler);
    }

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);

      if (event && handler) {
        console.log('[Socket] Unsubscribing from', event);
        socket.off(event, eventHandler);
      }
    };
  }, [event, handler]);

  const emit = useCallback((payload) => {
    const socket = socketRef.current;
    if (socket && socket.connected) {
      return socket.emit(event, payload);
    }

    // Try reconnecting
    const newSocket = getSocket();
    if (newSocket.disconnected) {
      newSocket.connect();
    }

    return newSocket?.emit(event, payload);
  }, [event]);

  return { emit, connected };
}

/**
 * Hook for manual socket event control
 * Does NOT auto-subscribe - call subscribe() manually
 *
 * @param {string} event - Socket event name
 * @param {Function} handler - Optional handler to subscribe
 * @returns {{ subscribe, unsubscribe, emit }}
 */
export function useManualSocket(event, handler) {
  const socketRef = useRef(null);
  const handlerRef = useRef(handler);

  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    socketRef.current = getSocket();
  }, []);

  const subscribe = useCallback(() => {
    const socket = socketRef.current || getSocket();
    if (socket && handlerRef.current) {
      console.log('[Socket] Manually subscribing to', event);
      socket.on(event, handlerRef.current);
    }
  }, [event]);

  const unsubscribe = useCallback(() => {
    const socket = socketRef.current;
    if (socket && handlerRef.current) {
      console.log('[Socket] Manually unsubscribing from', event);
      socket.off(event, handlerRef.current);
    }
  }, [event]);

  const emit = useCallback((payload) => {
    const socket = socketRef.current || getSocket();
    if (socket) {
      if (socket.disconnected) {
        socket.connect();
      }
      return socket.emit(event, payload);
    }
    return false;
  }, [event]);

  return { subscribe, unsubscribe, emit };
}

export default useSocket;
