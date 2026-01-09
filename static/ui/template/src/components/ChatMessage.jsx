/**
 * ChatMessage component
 * Renders individual chat message with user/assistant styling
 */
import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import CheckIcon from '@mui/icons-material/Check';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import PersonOutlineIcon from '@mui/icons-material/PersonOutline';
import AlitaIcon from './AlitaIcon';
import { ROLES, ToolActionStatus } from '../hooks/useChatPredict';
import './ChatMessage.css';

/**
 * Copy button component for code blocks
 */
function CopyButton({ text }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <button
      className="chat-message-code-copy"
      onClick={handleCopy}
      title={copied ? 'Copied!' : 'Copy code'}
    >
      {copied ? <CheckIcon sx={{ fontSize: 14 }} /> : <ContentCopyIcon sx={{ fontSize: 14 }} />}
    </button>
  );
}

/**
 * Format duration in human-readable format
 */
function formatDuration(ms) {
  if (!ms || ms < 0) return '';
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}

/**
 * Format token count in human-readable format
 */
function formatTokens(count) {
  if (!count || count <= 0) return '';
  if (count < 1000) return `${count}`;
  if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
  return `${Math.round(count / 1000)}k`;
}

/**
 * Copy icon button for assistant messages (icon only)
 */
function CopyIconButton({ text }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <button
      className={`chat-message-copy-icon ${copied ? 'copied' : ''}`}
      onClick={handleCopy}
      title={copied ? 'Copied!' : 'Copy response'}
    >
      {copied ? <CheckIcon sx={{ fontSize: 14 }} /> : <ContentCopyIcon sx={{ fontSize: 14 }} />}
    </button>
  );
}

/**
 * Tool action display component
 */
function ToolAction({ action }) {
  const [expanded, setExpanded] = React.useState(false);

  const statusIcon = {
    [ToolActionStatus.processing]: <HourglassEmptyIcon sx={{ fontSize: 14 }} />,
    [ToolActionStatus.complete]: <CheckIcon sx={{ fontSize: 14 }} />,
    [ToolActionStatus.error]: <ErrorOutlineIcon sx={{ fontSize: 14 }} />,
  }[action.status] || <HourglassEmptyIcon sx={{ fontSize: 14 }} />;

  const statusClass = {
    [ToolActionStatus.processing]: 'processing',
    [ToolActionStatus.complete]: 'complete',
    [ToolActionStatus.error]: 'error',
  }[action.status] || '';

  // Check if there's expandable content (input, output, or error)
  const hasExpandableContent = action.input || action.output || action.error;

  return (
    <div className={`chat-message-tool-action ${statusClass}`}>
      <div
        className="chat-message-tool-action-header"
        onClick={() => hasExpandableContent && setExpanded(!expanded)}
        style={{ cursor: hasExpandableContent ? 'pointer' : 'default' }}
      >
        <span className="chat-message-tool-action-icon">{statusIcon}</span>
        <span className="chat-message-tool-action-name">{action.name || 'Tool'}</span>
        {action.duration_ms > 0 && (
          <span className="chat-message-tool-action-message">
            {action.duration_ms}ms
          </span>
        )}
        {hasExpandableContent && (
          <span className="chat-message-tool-action-expand">
            {expanded ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ChevronRightIcon sx={{ fontSize: 16 }} />}
          </span>
        )}
      </div>
      {expanded && hasExpandableContent && (
        <div className="chat-message-tool-action-content">
          {action.input && (
            <div style={{ marginBottom: action.output ? 12 : 0 }}>
              <strong style={{ fontSize: 11, opacity: 0.7 }}>Input:</strong>
              <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {action.input}
              </pre>
            </div>
          )}
          {action.output && (
            <div>
              <strong style={{ fontSize: 11, opacity: 0.7 }}>Output:</strong>
              <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {action.output}
              </pre>
            </div>
          )}
          {action.error && (
            <div style={{ color: '#D71616' }}>
              <strong style={{ fontSize: 11 }}>Error:</strong>
              <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {action.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Thinking steps display component
 */
function ThinkingSteps({ steps }) {
  const [expanded, setExpanded] = React.useState(false);

  if (!steps || steps.length === 0) return null;

  return (
    <div className="chat-message-thinking">
      <div
        className="chat-message-thinking-header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="chat-message-thinking-icon">💭</span>
        <span className="chat-message-thinking-label">
          Thinking ({steps.length} step{steps.length > 1 ? 's' : ''})
        </span>
        <span className="chat-message-thinking-expand">
          {expanded ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ChevronRightIcon sx={{ fontSize: 16 }} />}
        </span>
      </div>
      {expanded && (
        <div className="chat-message-thinking-content">
          {steps.map((step, idx) => (
            <div key={step.id || idx} className="chat-message-thinking-step">
              {step.tool_name && (
                <span className="chat-message-thinking-tool">[{step.tool_name}]</span>
              )}
              <span className="chat-message-thinking-message">{step.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Main ChatMessage component
 */
function ChatMessage({ message, isStreaming }) {
  const isUser = message.role === ROLES.User;
  const hasError = !!message.exception;

  return (
    <div className={`chat-message ${isUser ? 'user' : 'assistant'} ${hasError ? 'error' : ''}`}>
      <div className="chat-message-avatar">
        {isUser ? <PersonOutlineIcon sx={{ fontSize: 18 }} /> : <AlitaIcon sx={{ fontSize: 18 }} />}
      </div>
      <div className="chat-message-content">
        {/* Tool actions (for assistant messages) */}
        {!isUser && message.toolActions?.length > 0 && (
          <div className="chat-message-tool-actions">
            {message.toolActions.map((action, idx) => (
              <ToolAction key={action.id || idx} action={action} />
            ))}
          </div>
        )}

        {/* Thinking steps (for assistant messages) */}
        {!isUser && message.thinkingSteps?.length > 0 && (
          <ThinkingSteps steps={message.thinkingSteps} />
        )}

        {/* Main content */}
        <div className="chat-message-text">
          {isUser ? (
            // User messages: plain text
            <p>{message.content}</p>
          ) : (
            // Assistant messages: markdown
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code({ node, inline, className, children, ...props }) {
                  const match = /language-(\w+)/.exec(className || '');
                  const codeString = String(children).replace(/\n$/, '');

                  if (!inline && match) {
                    return (
                      <div className="chat-message-code-block">
                        <div className="chat-message-code-header">
                          <span>{match[1]}</span>
                          <CopyButton text={codeString} />
                        </div>
                        <SyntaxHighlighter
                          style={oneDark}
                          language={match[1]}
                          PreTag="div"
                          {...props}
                        >
                          {codeString}
                        </SyntaxHighlighter>
                      </div>
                    );
                  }

                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {message.content || ''}
            </ReactMarkdown>
          )}

          {/* Streaming indicator */}
          {message.isStreaming && (
            <span className="chat-message-streaming-indicator">▊</span>
          )}
        </div>

        {/* Error message */}
        {hasError && (
          <div className="chat-message-error">
            {typeof message.exception === 'string'
              ? message.exception
              : JSON.stringify(message.exception)}
          </div>
        )}

        {/* References */}
        {message.references?.length > 0 && (
          <div className="chat-message-references">
            <span className="chat-message-references-label">Sources:</span>
            {message.references.map((ref, idx) => (
              <span key={idx} className="chat-message-reference">
                {ref.title || ref.source || `[${idx + 1}]`}
              </span>
            ))}
          </div>
        )}

        {/* Footer: Tokens, Duration (left) and Copy icon (right) for assistant messages */}
        {!isUser && !message.isStreaming && message.content && (
          <div className="chat-message-footer">
            <span className="chat-message-stats">
              {(message.tokens_in > 0 || message.tokens_out > 0) && (
                <span className="chat-message-tokens" title={`Input: ${message.tokens_in || 0} tokens | Output: ${message.tokens_out || 0} tokens`}>
                  {formatTokens(message.tokens_in || 0)} in / {formatTokens(message.tokens_out || 0)} out
                </span>
              )}
              {(message.tokens_in > 0 || message.tokens_out > 0) && message.duration_ms > 0 && (
                <span className="chat-message-separator">·</span>
              )}
              {message.duration_ms > 0 && (
                <span className="chat-message-duration">
                  {formatDuration(message.duration_ms)}
                </span>
              )}
            </span>
            <CopyIconButton text={message.content} />
          </div>
        )}
      </div>
    </div>
  );
}

export default ChatMessage;
