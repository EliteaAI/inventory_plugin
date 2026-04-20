import { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Paper,
  Chip,
  Divider,
  List,
  ListItem,
  ListItemText,
  CircularProgress,
  Tabs,
  Tab,
  IconButton,
  Tooltip,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import CodeIcon from '@mui/icons-material/Code';
import { getEntity, getImpactAnalysis } from '../utils/api';

function EntityPanel({ entity, projectId, toolkitId, onClose, theme = 'light' }) {
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!entity || !projectId || !toolkitId) return;

    const fetchDetails = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getEntity(projectId, toolkitId, entity.name || entity.label);
        setDetails(data);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchDetails();
  }, [entity, projectId, toolkitId]);

  if (!entity) return null;

  const entityData = details?.entity || entity;

  return (
    <Paper
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="h6" sx={{ flexGrow: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {entityData.name || entityData.label || 'Entity'}
        </Typography>
        <Tooltip title="Close">
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Type and Layer chips */}
      <Box sx={{ px: 2, pb: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
        {entityData.type && (
          <Chip label={entityData.type} size="small" color="primary" />
        )}
        {entityData.layer && (
          <Chip label={entityData.layer} size="small" color="secondary" variant="outlined" />
        )}
      </Box>

      <Divider />

      {/* Content */}
      <Box sx={{ flexGrow: 1, overflow: 'auto', p: 2 }}>
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}

        {error && (
          <Typography color="error" variant="body2">
            {error}
          </Typography>
        )}

        {!loading && (
          <Box>
            {/* Source */}
            {entityData.source_toolkit && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}
                >
                  ID
                </Typography>
                <Typography 
                  variant="body2" 
                  sx={{ 
                    fontFamily: 'monospace',
                    fontSize: '11px',
                    mt: 0.5,
                    p: 1,
                    backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                    borderRadius: 1,
                    wordBreak: 'break-all',
                  }}
                >
                  {entityData.id}
                </Typography>
              </Box>
            )}

            {/* Description */}
            {entityData.description && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}
                >
                  Description
                </Typography>
                <Typography variant="body2" sx={{ mt: 0.5 }}>
                  {entityData.description}
                </Typography>
              </Box>
            )}

            {/* File Path and Line Numbers */}
            {(entityData.file_path || (entityData.citations && entityData.citations.length > 0)) && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}
                >
                  File Location
                </Typography>
                {entityData.file_path && (
                  <Typography 
                    variant="body2" 
                    sx={{ 
                      fontFamily: 'monospace',
                      fontSize: '11px',
                      mt: 0.5,
                      p: 1,
                      backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                      borderRadius: 1,
                      wordBreak: 'break-word',
                    }}
                  >
                    {entityData.file_path}
                    {entityData.line_number && `:${entityData.line_number}`}
                    {entityData.line_start && `:${entityData.line_start}${entityData.line_end ? `-${entityData.line_end}` : ''}`}
                  </Typography>
                )}
                {entityData.citations && entityData.citations.length > 0 && entityData.citations.map((citation, idx) => (
                  <Typography 
                    key={idx}
                    variant="body2" 
                    sx={{ 
                      fontFamily: 'monospace',
                      fontSize: '11px',
                      mt: 0.5,
                      p: 1,
                      backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                      borderRadius: 1,
                      wordBreak: 'break-word',
                    }}
                  >
                    {citation.file_path}
                    {citation.line_start && `:${citation.line_start}${citation.line_end ? `-${citation.line_end}` : ''}`}
                  </Typography>
                ))}
              </Box>
            )}

            {/* Subject, Predicate, Object */}
            {(entityData.subject || entityData.predicate || entityData.object) && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    mb: 1,
                    display: 'block',
                  }}
                >
                  Knowledge Triple
                </Typography>
                <Paper
                  variant="outlined"
                  sx={{
                    p: 0,
                    mt: 1,
                    backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                    overflow: 'hidden',
                  }}
                >
                  {entityData.subject && (
                    <Box sx={{ p: 1.5, borderBottom: `1px solid ${theme === 'dark' ? '#3B3E46' : '#e0e0e0'}` }}>
                      <Typography variant="caption" sx={{ fontWeight: 600, color: theme === 'dark' ? '#6ae8fa' : '#29B8F5', display: 'block', mb: 0.5, fontFamily: 'monospace' }}>
                        subject
                      </Typography>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '11px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: theme === 'dark' ? '#e0e0e0' : '#424242' }}>
                        {typeof entityData.subject === 'object' ? JSON.stringify(entityData.subject, null, 2) : String(entityData.subject)}
                      </Typography>
                    </Box>
                  )}
                  {entityData.predicate && (
                    <Box sx={{ p: 1.5, borderBottom: entityData.object ? `1px solid ${theme === 'dark' ? '#3B3E46' : '#e0e0e0'}` : 'none' }}>
                      <Typography variant="caption" sx={{ fontWeight: 600, color: theme === 'dark' ? '#6ae8fa' : '#29B8F5', display: 'block', mb: 0.5, fontFamily: 'monospace' }}>
                        predicate
                      </Typography>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '11px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: theme === 'dark' ? '#e0e0e0' : '#424242' }}>
                        {typeof entityData.predicate === 'object' ? JSON.stringify(entityData.predicate, null, 2) : String(entityData.predicate)}
                      </Typography>
                    </Box>
                  )}
                  {entityData.object && (
                    <Box sx={{ p: 1.5 }}>
                      <Typography variant="caption" sx={{ fontWeight: 600, color: theme === 'dark' ? '#6ae8fa' : '#29B8F5', display: 'block', mb: 0.5, fontFamily: 'monospace' }}>
                        object
                      </Typography>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '11px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: theme === 'dark' ? '#e0e0e0' : '#424242' }}>
                        {typeof entityData.object === 'object' ? JSON.stringify(entityData.object, null, 2) : String(entityData.object)}
                      </Typography>
                    </Box>
                  )}
                </Paper>
              </Box>
            )}

            {/* Description */}
            {entityData.description && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}
                >
                  Description
                </Typography>
                <Typography variant="body2" sx={{ mt: 0.5 }}>
                  {entityData.description}
                </Typography>
              </Box>
            )}
            
            {/* Properties breakdown */}
            {entityData.properties && Object.keys(entityData.properties).length > 0 && (
              <Box sx={{ mb: 2.5 }}>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontWeight: 600,
                    color: 'text.secondary',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    mb: 1,
                    display: 'block',
                  }}
                >
                  Properties ({Object.keys(entityData.properties).length})
                </Typography>
                <Paper
                  variant="outlined"
                  sx={{
                    p: 0,
                    mt: 1,
                    backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                    overflow: 'hidden',
                  }}
                >
                  {Object.entries(entityData.properties).map(([key, value], idx, arr) => (
                    <Box 
                      key={key} 
                      sx={{ 
                        p: 1.5,
                        borderBottom: idx < arr.length - 1 ? `1px solid ${theme === 'dark' ? '#3B3E46' : '#e0e0e0'}` : 'none',
                      }}
                    >
                      <Typography 
                        variant="caption" 
                        sx={{ 
                          fontWeight: 600,
                          color: theme === 'dark' ? '#6ae8fa' : '#29B8F5',
                          display: 'block',
                          mb: 0.5,
                          fontFamily: 'monospace',
                        }}
                      >
                        {key}
                      </Typography>
                      <Typography 
                        variant="body2" 
                        sx={{ 
                          fontFamily: 'monospace',
                          fontSize: '11px',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          color: theme === 'dark' ? '#e0e0e0' : '#424242',
                        }}
                      >
                        {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                      </Typography>
                    </Box>
                  ))}
                </Paper>
              </Box>
            )}

            {/* Raw data - with maxHeight scroll */}
            <Box sx={{ mt: 3 }}>
              <Typography 
                variant="caption" 
                sx={{ 
                  fontWeight: 600,
                  color: 'text.secondary',
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  mb: 1,
                  display: 'block',
                }}
              >
                Raw Data
              </Typography>
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  mt: 1,
                  backgroundColor: theme === 'dark' ? '#181F2A' : '#f5f5f5',
                  overflow: 'auto',
                  border: theme === 'dark' ? '1px solid #3B3E46' : '1px solid #ddd',
                  maxHeight: '300px',
                }}
              >
                <pre style={{ 
                  margin: 0, 
                  fontSize: '11px', 
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  lineHeight: '1.5',
                  fontFamily: 'Consolas, Monaco, "Courier New", monospace',
                  color: theme === 'dark' ? '#d4d4d4' : '#333',
                }}>
                  {JSON.stringify(entityData, null, 2)}
                </pre>
              </Paper>
            </Box>
          </Box>
        )}
      </Box>
    </Paper>
  );
}

export default EntityPanel;
