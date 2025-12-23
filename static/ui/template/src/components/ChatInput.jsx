import { useState, useRef, useCallback } from 'react';
import {
  Box,
  Paper,
  TextField,
  IconButton,
  Typography,
  Collapse,
  CircularProgress,
  Fab,
  Zoom,
} from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import CloseIcon from '@mui/icons-material/Close';
import { invokeTool } from '../utils/api';

function ChatInput({
  projectId,
  toolkitId,
  toolkit = null,
  onQueryResult,
  theme = 'light',
  graphContext = null,
}) {
  // Get LLM model from toolkit configuration
  const llmModel = toolkit?.settings?.toolkit_configuration_llm_model || null;
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const handleSend = useCallback(async () => {
    if (!message.trim() || !projectId || !toolkitId || loading) return;

    const query = message.trim();
    setMessage('');
    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      // Build context from current graph state
      const context = graphContext ? {
        selected_entity: graphContext.selectedEntity?.name || null,
        visible_entities: graphContext.nodeCount || 0,
        current_query: graphContext.lastQuery || null,
      } : {};

      // Call the chat/query tool with model from toolkit config
      const toolParams = {
        query,
        context,
        output_format: 'json',
      };
      // Pass LLM model from toolkit configuration if available
      if (llmModel) {
        toolParams.model = llmModel;
      }
      const result = await invokeTool(projectId, toolkitId, 'chat_query', toolParams, 120);

      setResponse(result);

      // If the response contains graph data, pass it up
      if (result?.results || result?.entities) {
        onQueryResult?.(result);
      }
    } catch (err) {
      console.error('Chat query failed:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [message, projectId, toolkitId, loading, graphContext, onQueryResult, llmModel]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClearResponse = () => {
    setResponse(null);
    setError(null);
  };

  const handleToggle = () => {
    setExpanded(!expanded);
    if (!expanded) {
      // Focus input when opening
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  return (
    <>
      {/* Floating toggle button */}
      <Zoom in={!expanded}>
        <Fab
          color="primary"
          size="medium"
          onClick={handleToggle}
          sx={{
            position: 'absolute',
            bottom: 60,
            right: 16,
            zIndex: 10,
          }}
        >
          <SmartToyIcon />
        </Fab>
      </Zoom>

      {/* Chat panel */}
      <Collapse in={expanded} unmountOnExit>
        <Paper
          elevation={4}
          sx={{
            position: 'absolute',
            top: 16,
            bottom: 60,
            right: 16,
            width: '40%',
            minWidth: 320,
            maxWidth: 600,
            display: 'flex',
            flexDirection: 'column',
            zIndex: 10,
            overflow: 'hidden',
            backgroundColor: theme === 'dark' ? 'rgba(30, 30, 30, 0.98)' : 'rgba(255, 255, 255, 0.98)',
            backdropFilter: 'blur(8px)',
            borderRadius: 2,
          }}
        >
          {/* Header */}
          <Box sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 1.5,
            py: 1,
            borderBottom: 1,
            borderColor: 'divider',
            backgroundColor: 'primary.main',
            color: 'primary.contrastText',
          }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <SmartToyIcon fontSize="small" />
              <Typography variant="subtitle2" fontWeight={600}>
                Ask About Graph
              </Typography>
            </Box>
            <IconButton size="small" onClick={handleToggle} sx={{ color: 'inherit' }}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>

          {/* Response Area */}
          <Box sx={{
            flexGrow: 1,
            overflow: 'auto',
            p: 1.5,
            borderBottom: 1,
            borderColor: 'divider',
            backgroundColor: theme === 'dark' ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.02)',
          }}>
            {(response || error) ? (
              <>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    Response
                  </Typography>
                  <IconButton size="small" onClick={handleClearResponse} sx={{ p: 0 }}>
                    <CloseIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </Box>

                {error ? (
                  <Typography variant="body2" color="error">
                    {error}
                  </Typography>
                ) : (
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                    {typeof response === 'string'
                      ? response
                      : response?.message || response?.answer || JSON.stringify(response, null, 2)}
                  </Typography>
                )}
              </>
            ) : (
              <Box sx={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'text.secondary',
                textAlign: 'center',
                py: 4,
              }}>
                <SmartToyIcon sx={{ fontSize: 48, mb: 2, opacity: 0.3 }} />
                <Typography variant="body2" gutterBottom>
                  Ask questions about your knowledge graph
                </Typography>
                <Typography variant="caption" color="text.disabled">
                  Examples:
                </Typography>
                <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5 }}>
                  "What depends on UserService?"
                </Typography>
                <Typography variant="caption" color="text.disabled">
                  "Show me all API endpoints"
                </Typography>
                <Typography variant="caption" color="text.disabled">
                  "Find classes related to authentication"
                </Typography>
              </Box>
            )}
          </Box>

          {/* Input Area */}
          <Box sx={{ p: 1.5 }}>
            <TextField
              inputRef={inputRef}
              fullWidth
              size="small"
              multiline
              maxRows={3}
              placeholder="Ask about entities, dependencies..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              sx={{
                '& .MuiInputBase-root': {
                  pr: 0.5,
                },
              }}
              InputProps={{
                endAdornment: (
                  <IconButton
                    size="small"
                    onClick={handleSend}
                    disabled={!message.trim() || loading}
                    color="primary"
                  >
                    {loading ? (
                      <CircularProgress size={18} />
                    ) : (
                      <SendIcon fontSize="small" />
                    )}
                  </IconButton>
                ),
              }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              Try: "What depends on UserService?"
            </Typography>
          </Box>
        </Paper>
      </Collapse>
    </>
  );
}

export default ChatInput;
