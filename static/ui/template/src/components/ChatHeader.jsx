/**
 * ChatHeader component
 * Provides conversation selection and management controls
 */
import React, { useState, useRef, useEffect } from 'react';
import AddIcon from '@mui/icons-material/Add';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import CloseIcon from '@mui/icons-material/Close';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import './ChatHeader.css';

/**
 * Conversation dropdown menu
 */
function ConversationDropdown({
  conversations,
  activeConversation,
  isOpen,
  onSelect,
  onDelete,
  onClose,
  isLoading,
}) {
  const dropdownRef = useRef(null);

  // Close on click outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        onClose();
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="chat-header-dropdown" ref={dropdownRef}>
      {isLoading ? (
        <div className="chat-header-dropdown-loading">Loading...</div>
      ) : conversations.length === 0 ? (
        <div className="chat-header-dropdown-empty">No conversations yet</div>
      ) : (
        <div className="chat-header-dropdown-list">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`chat-header-dropdown-item ${
                activeConversation?.id === conv.id ? 'active' : ''
              }`}
            >
              <button
                className="chat-header-dropdown-item-content"
                onClick={() => {
                  onSelect(conv);
                  onClose();
                }}
              >
                <span className="chat-header-dropdown-item-name">
                  {conv.name || 'Untitled'}
                </span>
                <span className="chat-header-dropdown-item-date">
                  {conv.updated_at
                    ? new Date(conv.updated_at).toLocaleDateString()
                    : ''}
                </span>
              </button>
              <button
                className="chat-header-dropdown-item-delete"
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm('Delete this conversation?')) {
                    onDelete(conv.id);
                  }
                }}
                title="Delete conversation"
              >
                <DeleteOutlineIcon sx={{ fontSize: 16 }} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Main ChatHeader component
 *
 * @param {Object} props
 * @param {Array} props.conversations - List of conversations
 * @param {Object} props.activeConversation - Currently selected conversation
 * @param {Function} props.onNewConversation - Handler for new conversation
 * @param {Function} props.onSelectConversation - Handler for selecting conversation
 * @param {Function} props.onDeleteConversation - Handler for deleting conversation
 * @param {Function} props.onClose - Handler for closing chat panel
 * @param {boolean} props.isLoading - Loading state
 */
function ChatHeader({
  conversations = [],
  activeConversation,
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
  onClose,
  isLoading = false,
}) {
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const handleNewConversation = () => {
    setDropdownOpen(false);
    onNewConversation?.();
  };

  return (
    <div className="chat-header">
      <div className="chat-header-left">
        {/* New conversation button */}
        <button
          className="chat-header-new-btn"
          onClick={handleNewConversation}
          disabled={isLoading}
          title="New conversation"
        >
          <AddIcon sx={{ fontSize: 18 }} />
        </button>

        {/* Conversation selector */}
        <div className="chat-header-selector">
          <button
            className="chat-header-selector-btn"
            onClick={() => setDropdownOpen(!dropdownOpen)}
          >
            <span className="chat-header-selector-name">
              {activeConversation?.name || 'Select conversation'}
            </span>
            <span className="chat-header-selector-arrow">
              {dropdownOpen ? <KeyboardArrowUpIcon sx={{ fontSize: 18 }} /> : <KeyboardArrowDownIcon sx={{ fontSize: 18 }} />}
            </span>
          </button>

          <ConversationDropdown
            conversations={conversations}
            activeConversation={activeConversation}
            isOpen={dropdownOpen}
            onSelect={onSelectConversation}
            onDelete={onDeleteConversation}
            onClose={() => setDropdownOpen(false)}
            isLoading={isLoading}
          />
        </div>
      </div>

      <div className="chat-header-right">
        {/* Close button */}
        <button
          className="chat-header-close-btn"
          onClick={onClose}
          title="Close chat"
        >
          <CloseIcon sx={{ fontSize: 18 }} />
        </button>
      </div>
    </div>
  );
}

export default ChatHeader;
