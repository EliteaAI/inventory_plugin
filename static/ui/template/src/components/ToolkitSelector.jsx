import { useState, useEffect, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  CircularProgress,
  Alert,
  Box,
  Chip,
  TextField,
  InputAdornment,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import GitHubIcon from '@mui/icons-material/GitHub';
import CloudIcon from '@mui/icons-material/Cloud';
import StorageIcon from '@mui/icons-material/Storage';
import ArticleIcon from '@mui/icons-material/Article';
import BugReportIcon from '@mui/icons-material/BugReport';
import FolderIcon from '@mui/icons-material/Folder';
import { listAvailableToolkits } from '../utils/api';

// Icon mapping for toolkit types
const getToolkitIcon = (type) => {
  const typeLower = (type || '').toLowerCase();
  if (typeLower.includes('github')) return <GitHubIcon />;
  if (typeLower.includes('gitlab')) return <GitHubIcon />;
  if (typeLower.includes('bitbucket')) return <GitHubIcon />;
  if (typeLower.includes('ado') || typeLower.includes('azure')) return <CloudIcon />;
  if (typeLower.includes('confluence')) return <ArticleIcon />;
  if (typeLower.includes('jira')) return <BugReportIcon />;
  return <FolderIcon />;
};

// Color mapping for toolkit types
const getToolkitColor = (type) => {
  const typeLower = (type || '').toLowerCase();
  if (typeLower.includes('github')) return '#24292e';
  if (typeLower.includes('gitlab')) return '#fc6d26';
  if (typeLower.includes('bitbucket')) return '#0052cc';
  if (typeLower.includes('ado') || typeLower.includes('azure')) return '#0078d4';
  if (typeLower.includes('confluence')) return '#172b4d';
  if (typeLower.includes('jira')) return '#0052cc';
  return '#666';
};

function ToolkitSelector({
  open,
  onClose,
  projectId,
  onSelect,
  existingSources = [],
}) {
  const [toolkits, setToolkits] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');

  // Load available toolkits
  useEffect(() => {
    if (open && projectId) {
      loadToolkits();
    }
  }, [open, projectId]);

  const loadToolkits = async () => {
    try {
      setLoading(true);
      setError(null);
      const available = await listAvailableToolkits(projectId);
      setToolkits(available);
    } catch (err) {
      console.error('Failed to load toolkits:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSelect = (toolkit) => {
    onSelect?.(toolkit);
    onClose();
  };

  // Filter toolkits based on search
  const filteredToolkits = toolkits.filter(toolkit => {
    const name = (toolkit.toolkit_name || toolkit.name || '').toLowerCase();
    const type = (toolkit.type || '').toLowerCase();
    const query = searchQuery.toLowerCase();
    return name.includes(query) || type.includes(query);
  });

  // Check if toolkit is already added
  const isAlreadyAdded = (toolkitId) => {
    return existingSources.some(source =>
      source.toolkit_id === toolkitId || source.id === toolkitId
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ sx: { maxHeight: '70vh' } }}
    >
      <DialogTitle>
        <Typography variant="h6">Add Data Source</Typography>
        <Typography variant="body2" color="text.secondary">
          Select a toolkit to ingest into the knowledge graph
        </Typography>
      </DialogTitle>

      <DialogContent dividers>
        {/* Search */}
        <TextField
          fullWidth
          size="small"
          placeholder="Search toolkits..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{ mb: 2 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />

        {/* Loading */}
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        )}

        {/* Error */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {/* Empty state */}
        {!loading && !error && filteredToolkits.length === 0 && (
          <Box sx={{ textAlign: 'center', py: 4 }}>
            <StorageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
            <Typography variant="body2" color="text.secondary">
              {toolkits.length === 0
                ? 'No source toolkits found in this project. Create a GitHub, ADO, or other repository toolkit first.'
                : 'No toolkits match your search.'}
            </Typography>
          </Box>
        )}

        {/* Toolkit list */}
        {!loading && filteredToolkits.length > 0 && (
          <List dense>
            {filteredToolkits.map((toolkit) => {
              const added = isAlreadyAdded(toolkit.id);
              return (
                <ListItem
                  key={toolkit.id}
                  disablePadding
                  secondaryAction={
                    added && (
                      <Chip
                        label="Added"
                        size="small"
                        color="success"
                        variant="outlined"
                      />
                    )
                  }
                >
                  <ListItemButton
                    onClick={() => handleSelect(toolkit)}
                    disabled={added}
                  >
                    <ListItemIcon sx={{ color: getToolkitColor(toolkit.type), minWidth: 40 }}>
                      {getToolkitIcon(toolkit.type)}
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Typography variant="body2">
                            {toolkit.toolkit_name || toolkit.name || 'Unnamed'}
                          </Typography>
                          <Chip
                            label={toolkit.type}
                            size="small"
                            sx={{ height: 18, fontSize: 10 }}
                          />
                        </Box>
                      }
                      secondary={
                        toolkit.description ? (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              display: 'block',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                              maxWidth: 300,
                            }}
                          >
                            {toolkit.description}
                          </Typography>
                        ) : null
                      }
                    />
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}

export default ToolkitSelector;
