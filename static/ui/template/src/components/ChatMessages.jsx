/**
 * ChatMessages component
 * Renders list of chat messages with auto-scroll
 */
import React, { useEffect, useRef } from 'react';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import AlitaIcon from './AlitaIcon';
import ChatMessage from './ChatMessage';
import './ChatMessages.css';

/**
 * Loading indicator component
 */
function LoadingIndicator() {
  return (
    <div className="chat-messages-loading">
      <div className="chat-messages-loading-dot"></div>
      <div className="chat-messages-loading-dot"></div>
      <div className="chat-messages-loading-dot"></div>
    </div>
  );
}

/**
 * Empty state component
 */
function EmptyState({ hasConversation }) {
  return (
    <div className="chat-messages-empty">
      <div className="chat-messages-empty-icon">
        <ChatBubbleOutlineIcon sx={{ fontSize: 48, opacity: 0.5 }} />
      </div>
      <div className="chat-messages-empty-title">
        {hasConversation ? 'No messages yet' : 'Start a conversation'}
      </div>
      <div className="chat-messages-empty-subtitle">
        {hasConversation
          ? 'Send a message to start the conversation'
          : 'Create a new conversation to begin chatting with your inventory knowledge graph'}
      </div>
    </div>
  );
}

/**
 * Main ChatMessages component
 *
 * @param {Object} props
 * @param {Array} props.messages - Array of message objects
 * @param {boolean} props.isLoading - Whether messages are loading
 * @param {boolean} props.isStreaming - Whether assistant is currently streaming
 * @param {boolean} props.hasConversation - Whether there's an active conversation
 */
function ChatMessages({ messages = [], isLoading = false, isStreaming = false, hasConversation = false }) {
  const containerRef = useRef(null);
  const bottomRef = useRef(null);

  // Auto-scroll to bottom when new messages arrive or streaming
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isStreaming]);

  // Show loading state
  if (isLoading) {
    return (
      <div className="chat-messages" ref={containerRef}>
        <LoadingIndicator />
      </div>
    );
  }

  // Show empty state
  if (messages.length === 0) {
    return (
      <div className="chat-messages" ref={containerRef}>
        <EmptyState hasConversation={hasConversation} />
      </div>
    );
  }

  return (
    <div className="chat-messages" ref={containerRef}>
      <div className="chat-messages-list">
        {messages.map((message, index) => (
          <ChatMessage
            key={message.id || index}
            message={message}
            isStreaming={message.isStreaming}
          />
        ))}

        {/* Streaming assistant placeholder when waiting for response */}
        {isStreaming && !messages.some((m) => m.isStreaming) && (
          <div className="chat-message assistant">
            <div className="chat-message-avatar">
              <AlitaIcon sx={{ fontSize: 18 }} />
            </div>
            <div className="chat-message-content">
              <div className="chat-message-text">
                <LoadingIndicator />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
}

export default ChatMessages;
