// AddSourceDialog.jsx
// Modal dialog for searching and selecting toolkits to add as sources

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Box,
  Typography,
  Checkbox,
  FormControlLabel,
  FormGroup,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemSecondaryAction,
  CircularProgress,
  Alert,
  Chip,
  IconButton,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import GitHubIcon from '@mui/icons-material/GitHub';
import CloudIcon from '@mui/icons-material/Cloud';
import StorageIcon from '@mui/icons-material/Storage';
import AddIcon from '@mui/icons-material/Add';

import useToolkitSearch from '../hooks/useToolkitSearch';

// Toolkit type options for filtering
const TOOLKIT_TYPE_OPTIONS = [
  { value: 'github', label: 'GitHub' },
  { value: 'ado_repos', label: 'Azure DevOps Repos' },
  { value: 'gitlab', label: 'GitLab' },
  { value: 'bitbucket', label: 'Bitbucket' },
];

// Toolkit type icons and colors
const TOOLKIT_CONFIG = {
  github: { icon: GitHubIcon, color: '#333' },
  ado_repos: { icon: CloudIcon, color: '#0078d4' },
  azure_devops_repos: { icon: CloudIcon, color: '#0078d4' },
  ado: { icon: CloudIcon, color: '#0078d4' },
  azure_devops: { icon: CloudIcon, color: '#0078d4' },
  gitlab: { icon: StorageIcon, color: '#fc6d26' },
  bitbucket: { icon: StorageIcon, color: '#0052cc' },
};

const DEFAULT_TOOLKIT_CONFIG = { icon: StorageIcon, color: '#666' };

export default function AddSourceDialog({
  isOpen,
  onClose,
  onAdd,
  existingSourceIds = [],
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTypes, setSelectedTypes] = useState(['github', 'ado_repos', 'gitlab', 'bitbucket']);
  const { toolkits, isLoading, error, hasMore, search, loadMore, clearResults } = useToolkitSearch();

  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setSearchQuery('');
      setSelectedTypes(['github', 'ado_repos', 'gitlab', 'bitbucket']);
      clearResults();
    }
  }, [isOpen, clearResults]);

  // Debounced search when query or types change
  useEffect(() => {
    if (isOpen && selectedTypes.length > 0) {
      const timeoutId = setTimeout(() => {
        search(searchQuery, selectedTypes);
      }, 300);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen, searchQuery, selectedTypes, search]);

  const handleTypeToggle = (type) => {
    setSelectedTypes(prev =>
      prev.includes(type)
        ? prev.filter(t => t !== type)
        : [...prev, type]
    );
  };

  const handleAdd = (toolkit) => {
    onAdd({
      toolkit_id: toolkit.id,
      toolkit_name: toolkit.name || toolkit.toolkit_name,
      toolkit_type: toolkit.type,
      status: 'pending',
    });
    onClose();
  };

  const isAlreadyAdded = (toolkitId) => existingSourceIds.includes(toolkitId);

  const getToolkitIcon = (type) => {
    const config = TOOLKIT_CONFIG[type?.toLowerCase()] || DEFAULT_TOOLKIT_CONFIG;
    const IconComponent = config.icon;
    return <IconComponent sx={{ color: config.color }} />;
  };

  return (
    <Dialog
      open={isOpen}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ sx: { maxHeight: '80vh' } }}
    >
      <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography variant="h6">Add Source</Typography>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers>
        {/* Search Input */}
        <TextField
          fullWidth
          placeholder="Search toolkits..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          size="small"
          autoFocus
          sx={{ mb: 2 }}
        />

        {/* Type Filters */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Filter by type:
          </Typography>
          <FormGroup row>
            {TOOLKIT_TYPE_OPTIONS.map(option => (
              <FormControlLabel
                key={option.value}
                control={
                  <Checkbox
                    checked={selectedTypes.includes(option.value)}
                    onChange={() => handleTypeToggle(option.value)}
                    size="small"
                  />
                }
                label={option.label}
              />
            ))}
          </FormGroup>
        </Box>

        {/* Error Message */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {/* Loading State */}
        {isLoading && toolkits.length === 0 && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress size={32} />
          </Box>
        )}

        {/* Empty State */}
        {!isLoading && !error && (
          selectedTypes.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
              Select at least one toolkit type
            </Typography>
          ) : toolkits.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
              No toolkits found
            </Typography>
          ) : toolkits.filter(t => !isAlreadyAdded(t.id)).length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
              All available toolkits have already been added
            </Typography>
          ) : null
        )}

        {/* Results List - filter out already added sources */}
        {toolkits.filter(t => !isAlreadyAdded(t.id)).length > 0 && (
          <List sx={{ maxHeight: 300, overflow: 'auto' }}>
            {toolkits
              .filter(toolkit => !isAlreadyAdded(toolkit.id))
              .map(toolkit => (
                <ListItem
                  key={toolkit.id}
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    mb: 1,
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 40 }}>
                    {getToolkitIcon(toolkit.type)}
                  </ListItemIcon>
                  <ListItemText
                    primary={toolkit.name || toolkit.toolkit_name || `Toolkit ${toolkit.id}`}
                    secondary={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
                        <Chip label={toolkit.type} size="small" variant="outlined" />
                        <Typography variant="caption" color="text.secondary">
                          ID: {toolkit.id}
                        </Typography>
                      </Box>
                    }
                  />
                  <ListItemSecondaryAction>
                    <Button
                      variant="contained"
                      size="small"
                      onClick={() => handleAdd(toolkit)}
                      startIcon={<AddIcon />}
                    >
                      Add
                    </Button>
                  </ListItemSecondaryAction>
                </ListItem>
              ))}
          </List>
        )}

        {/* Load More Button */}
        {hasMore && (
          <Button
            fullWidth
            variant="outlined"
            onClick={loadMore}
            disabled={isLoading}
            sx={{ mt: 1 }}
          >
            {isLoading ? 'Loading...' : 'Load More'}
          </Button>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  );
}
