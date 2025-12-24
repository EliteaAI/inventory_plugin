import { useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  IconButton,
  Tooltip,
  Chip,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
} from '@mui/material';
import GitHubIcon from '@mui/icons-material/GitHub';
import CloudIcon from '@mui/icons-material/Cloud';
import StorageIcon from '@mui/icons-material/Storage';
import DeleteIcon from '@mui/icons-material/Delete';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import SettingsIcon from '@mui/icons-material/Settings';
import EditSourceDialog from './EditSourceDialog';

// Toolkit type icons and colors
const TOOLKIT_CONFIG = {
  github: { icon: GitHubIcon, color: '#333' },
  ado: { icon: CloudIcon, color: '#0078d4' },
  azure_devops: { icon: CloudIcon, color: '#0078d4' },
  gitlab: { icon: StorageIcon, color: '#fc6d26' },
  bitbucket: { icon: StorageIcon, color: '#0052cc' },
};

const DEFAULT_TOOLKIT_CONFIG = { icon: StorageIcon, color: '#666' };

// Status display configuration
// Maps backend status values to UI display
const STATUS_CONFIG = {
  // Backend status values (from sources_status.json)
  pending: { color: 'warning', label: 'Pending' },
  in_progress: { color: 'info', label: 'Ingesting...' },
  completed: { color: 'success', label: 'Done' },
  error: { color: 'error', label: 'Error' },
  // Legacy/UI status values for backwards compatibility
  ingesting: { color: 'info', label: 'Ingesting...' },
  ingested: { color: 'success', label: 'Done' },
  done: { color: 'success', label: 'Done' },
};

export default function SourceCard({
  source,
  onIngest,
  onRemove,
  onConfigChange,
}) {
  const {
    toolkit_id,
    toolkit_name,
    toolkit_type = 'other',
    status = 'pending',
    file_patterns = '',
    exclude_patterns = '',
    branch = '',
    preset = '',
    progress_message = null,
  } = source;

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  // Check if source has any configuration
  const hasConfig = file_patterns || exclude_patterns || branch || preset;

  const displayName = toolkit_name || `Toolkit ${toolkit_id}`;
  const toolkitConfig = TOOLKIT_CONFIG[toolkit_type?.toLowerCase()] || DEFAULT_TOOLKIT_CONFIG;
  const statusConfig = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const ToolkitIcon = toolkitConfig.icon;

  const handleRemoveClick = () => {
    setConfirmOpen(true);
  };

  const handleConfirmRemove = () => {
    setConfirmOpen(false);
    onRemove?.(toolkit_id);
  };

  const handleConfigSave = (configUpdates) => {
    onConfigChange?.(toolkit_id, configUpdates);
    setEditOpen(false);
  };

  const isIngesting = status === 'ingesting' || status === 'in_progress';

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 1.5,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
      }}
    >
      {/* Main row with icon, name, status badge, and action buttons */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
        <ToolkitIcon sx={{ color: toolkitConfig.color, fontSize: 24 }} />
        <Typography variant="body2" sx={{ fontWeight: 500, flexGrow: 1 }} noWrap>
          {displayName}
        </Typography>
        <Chip
          label={statusConfig.label}
          size="small"
          color={statusConfig.color}
          sx={{ fontSize: '0.7rem', height: 22 }}
        />
        <Tooltip title="Configure filters">
          <IconButton
            size="small"
            onClick={() => setEditOpen(true)}
            sx={{
              bgcolor: hasConfig ? 'info.main' : 'action.hover',
              color: hasConfig ? 'white' : 'text.secondary',
              '&:hover': { bgcolor: hasConfig ? 'info.dark' : 'action.selected' },
              width: 32,
              height: 32,
            }}
          >
            <SettingsIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={isIngesting ? 'Ingesting...' : 'Ingest'}>
          <span>
            <IconButton
              size="small"
              onClick={() => onIngest?.(toolkit_id)}
              disabled={isIngesting}
              sx={{
                bgcolor: 'primary.main',
                color: 'white',
                '&:hover': { bgcolor: 'primary.dark' },
                '&.Mui-disabled': { bgcolor: 'action.disabledBackground' },
                width: 32,
                height: 32,
              }}
            >
              {isIngesting ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <PlayArrowIcon fontSize="small" />
              )}
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Remove">
          <IconButton
            size="small"
            onClick={handleRemoveClick}
            sx={{
              bgcolor: 'error.main',
              color: 'white',
              '&:hover': { bgcolor: 'error.dark' },
              width: 32,
              height: 32,
            }}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Progress message row - shown during ingestion */}
      {isIngesting && progress_message && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            pl: 4, // Align with text (after icon)
            mt: 0.5,
          }}
        >
          <Typography
            variant="caption"
            sx={{
              color: 'info.main',
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: '100%',
            }}
          >
            {progress_message}
          </Typography>
        </Box>
      )}

      {/* Remove Confirmation Dialog */}
      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
      >
        <DialogTitle>Remove Source</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Remove "{displayName}" from sources? This will not delete any ingested data.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)} color="inherit">
            Cancel
          </Button>
          <Button onClick={handleConfirmRemove} color="error" variant="contained">
            Remove
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Source Configuration Dialog */}
      <EditSourceDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onSave={handleConfigSave}
        source={source}
      />
    </Paper>
  );
}
