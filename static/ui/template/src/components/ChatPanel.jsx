/**
 * ChatPanel component
 * Main chat panel for inventory using inventory_chat endpoint
 *
 * This is a simplified chat that connects directly to the inventory plugin,
 * which auto-compiles tools and uses the toolkit's LLM configuration.
 */
import React, { useState, useCallback, useRef, useEffect } from 'react';
import SendIcon from '@mui/icons-material/Send';
import StopIcon from '@mui/icons-material/Stop';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import CloseIcon from '@mui/icons-material/Close';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import ChatMessages from './ChatMessages';
import { useInventoryChat, ROLES, ToolActionStatus } from '../hooks/useInventoryChat';
import { getChatModels } from '../utils/api';
import './ChatPanel.css';
import './ChatHeader.css';

/**
 * Confirmation Modal component
 * Used instead of window.confirm() which is blocked in iframes
 */
function ConfirmationModal({ isOpen, onConfirm, onCancel, title, message, theme }) {
  if (!isOpen) return null;

  return (
    <div className="confirmation-modal-overlay" onClick={onCancel}>
      <div
        className={`confirmation-modal ${theme === 'light' ? 'confirmation-modal--light' : 'confirmation-modal--dark'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirmation-modal-icon">
          <WarningAmberIcon sx={{ fontSize: 32, color: '#E97912' }} />
        </div>
        <div className="confirmation-modal-title">{title}</div>
        <div className="confirmation-modal-message">{message}</div>
        <div className="confirmation-modal-actions">
          <button
            className="confirmation-modal-btn confirmation-modal-btn--cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="confirmation-modal-btn confirmation-modal-btn--confirm"
            onClick={onConfirm}
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Chat input component
 */
function ChatInput({ onSend, disabled, isStreaming, onStop }) {
  const [input, setInput] = useState('');
  const textareaRef = useRef(null);

  // Auto-resize textarea - but keep it small when empty
  useEffect(() => {
    if (textareaRef.current) {
      if (!input) {
        // Reset to single line when empty
        textareaRef.current.style.height = '40px';
      } else {
        textareaRef.current.style.height = 'auto';
        textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 150)}px`;
      }
    }
  }, [input]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !disabled && !isStreaming) {
      onSend(input.trim());
      setInput('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form className="chat-panel-input" onSubmit={handleSubmit}>
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about your inventory..."
        disabled={disabled}
        rows={1}
      />
      {isStreaming ? (
        <button
          type="button"
          className="chat-panel-stop-btn"
          onClick={onStop}
          title="Stop generation"
        >
          <StopIcon sx={{ fontSize: 18 }} />
        </button>
      ) : (
        <button
          type="submit"
          className="chat-panel-send-btn"
          disabled={disabled || !input.trim()}
          title="Send message"
        >
          <SendIcon sx={{ fontSize: 18 }} />
        </button>
      )}
    </form>
  );
}

/**
 * Model selector dropdown
 */
function ModelSelector({ models, selectedModel, onSelectModel, isLoading, theme }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close on click outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  // Get display name for selected model
  const selectedDisplay = selectedModel
    ? models.find(m => m.name === selectedModel)?.display_name || selectedModel
    : 'Default';

  return (
    <div className="chat-model-selector" ref={dropdownRef}>
      <button
        className="chat-model-selector-btn"
        onClick={() => setIsOpen(!isOpen)}
        disabled={isLoading || models.length === 0}
        title="Select model"
      >
        <span className="chat-model-selector-name">{selectedDisplay}</span>
        <KeyboardArrowDownIcon sx={{ fontSize: 14, opacity: 0.7 }} />
      </button>

      {isOpen && models.length > 0 && (
        <div className={`chat-model-dropdown ${theme === 'light' ? 'chat-model-dropdown--light' : ''}`}>
          <div
            className={`chat-model-dropdown-item ${!selectedModel ? 'active' : ''}`}
            onClick={() => {
              onSelectModel(null);
              setIsOpen(false);
            }}
          >
            Default (from toolkit)
          </div>
          {models.map((model) => (
            <div
              key={model.id || model.name}
              className={`chat-model-dropdown-item ${selectedModel === model.name ? 'active' : ''}`}
              onClick={() => {
                onSelectModel(model.name);
                setIsOpen(false);
              }}
            >
              {model.display_name || model.name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Chat header component - simplified for inventory
 * Height aligned with right panel tabs (36px)
 */
function ChatHeader({ onClearHistory, onClose, isLoading, theme, models, selectedModel, onSelectModel }) {
  return (
    <div className="chat-header">
      <div className="chat-header-left">
        <span className="chat-header-selector-name">Inventory Chat</span>
        <ModelSelector
          models={models}
          selectedModel={selectedModel}
          onSelectModel={onSelectModel}
          isLoading={isLoading}
          theme={theme}
        />
      </div>

      <div className="chat-header-right">
        <button
          className="chat-header-close-btn"
          onClick={onClearHistory}
          disabled={isLoading}
          title="Clear chat history"
        >
          <DeleteOutlineIcon sx={{ fontSize: 16 }} />
        </button>
        <button
          className="chat-header-close-btn"
          onClick={onClose}
          title="Close chat"
        >
          <CloseIcon sx={{ fontSize: 16 }} />
        </button>
      </div>
    </div>
  );
}

/**
 * Main ChatPanel component
 *
 * @param {Object} props
 * @param {string} props.projectId - Project ID
 * @param {string} props.toolkitId - Toolkit ID
 * @param {Object} props.toolkit - Toolkit object
 * @param {Object} props.filters - Current filters from the filter panel
 * @param {Function} props.onClose - Handler for closing panel
 * @param {Function} props.onTouchedEntities - Callback when entities are accessed during chat
 * @param {Function} props.onClearGraph - Callback to clear the graph when chat is cleared
 * @param {string} props.theme - Theme mode ('light' or 'dark')
 */
function ChatPanel({ projectId, toolkitId, toolkit, filters = {}, onClose, onTouchedEntities, onClearGraph, theme = 'dark' }) {
  // Modal state for clear history confirmation
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  // Model selection state
  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState(null);

  // Fetch available models on mount
  useEffect(() => {
    async function fetchModels() {
      try {
        const models = await getChatModels();
        setAvailableModels(models);
      } catch (err) {
        console.error('[ChatPanel] Failed to fetch models:', err);
      }
    }
    fetchModels();
  }, [projectId, toolkitId]);

  // Use the inventory-specific chat hook
  const {
    messages,
    isLoading,
    isStreaming,
    error,
    sendMessage,
    cancelRequest,
    clearHistory,
  } = useInventoryChat({
    projectId: parseInt(projectId, 10),
    toolkitId: parseInt(toolkitId, 10),
    filters,
    onTouchedEntities,
    model: selectedModel,
  });

  /**
   * Handle sending a message
   */
  const handleSendMessage = useCallback((content) => {
    sendMessage(content);
  }, [sendMessage]);

  /**
   * Handle stop streaming
   */
  const handleStopStreaming = useCallback(() => {
    cancelRequest();
  }, [cancelRequest]);

  /**
   * Open clear history confirmation modal
   */
  const handleClearHistoryClick = useCallback(() => {
    setShowClearConfirm(true);
  }, []);

  /**
   * Confirm clear history
   */
  const handleConfirmClear = useCallback(async () => {
    setShowClearConfirm(false);
    await clearHistory();
    // Also clear the graph when chat is cleared
    onClearGraph?.();
  }, [clearHistory, onClearGraph]);

  /**
   * Cancel clear history
   */
  const handleCancelClear = useCallback(() => {
    setShowClearConfirm(false);
  }, []);

  return (
    <div className={`chat-panel ${theme === 'light' ? 'chat-panel--light' : 'chat-panel--dark'}`}>
      <ChatHeader
        onClearHistory={handleClearHistoryClick}
        onClose={onClose}
        isLoading={isLoading}
        theme={theme}
        models={availableModels}
        selectedModel={selectedModel}
        onSelectModel={setSelectedModel}
      />

      <ChatMessages
        messages={messages}
        isLoading={isLoading}
        isStreaming={isStreaming}
        hasConversation={true}
        theme={theme}
      />

      {error && (
        <div className="chat-panel-error">
          {error}
        </div>
      )}

      <ChatInput
        onSend={handleSendMessage}
        disabled={isLoading}
        isStreaming={isStreaming}
        onStop={handleStopStreaming}
      />

      {/* Clear history confirmation modal */}
      <ConfirmationModal
        isOpen={showClearConfirm}
        onConfirm={handleConfirmClear}
        onCancel={handleCancelClear}
        title="Clear Chat History"
        message="Are you sure you want to clear all chat history? This action cannot be undone."
        theme={theme}
      />
    </div>
  );
}

export default ChatPanel;
