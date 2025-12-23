import { useEffect, useRef } from 'react';
import { Paper, MenuItem, ListItemIcon, ListItemText, Divider, Typography } from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import Filter1Icon from '@mui/icons-material/Filter1';
import Filter2Icon from '@mui/icons-material/Filter2';
import Filter3Icon from '@mui/icons-material/Filter3';

/**
 * Context menu for graph nodes - shows options to expand connections
 */
function NodeContextMenu({ position, node, onExpand, onClose, theme = 'light' }) {
  const menuRef = useRef(null);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        onClose();
      }
    };

    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  if (!position || !node) return null;

  const handleExpand = (depth) => {
    onExpand(node, depth);
    onClose();
  };

  const nodeName = node.label || node.name || node.id || 'Unknown';
  const truncatedName = nodeName.length > 25 ? nodeName.slice(0, 22) + '...' : nodeName;

  return (
    <Paper
      ref={menuRef}
      elevation={8}
      sx={{
        position: 'fixed',
        top: position.y,
        left: position.x,
        zIndex: 1000,
        minWidth: 200,
        maxWidth: 280,
        py: 0.5,
        backgroundColor: theme === 'dark' ? '#1E2530' : '#fff',
        border: theme === 'dark' ? '1px solid rgba(255,255,255,0.1)' : '1px solid rgba(0,0,0,0.1)',
        borderRadius: 1,
      }}
    >
      {/* Header with node name */}
      <Typography
        variant="caption"
        sx={{
          display: 'block',
          px: 2,
          py: 0.5,
          color: 'text.secondary',
          fontWeight: 500,
          borderBottom: 1,
          borderColor: 'divider',
          mb: 0.5,
        }}
      >
        {truncatedName}
      </Typography>

      {/* Expand options */}
      <Typography
        variant="caption"
        sx={{
          display: 'flex',
          alignItems: 'center',
          px: 2,
          py: 0.5,
          color: 'text.secondary',
          gap: 1,
        }}
      >
        <AccountTreeIcon sx={{ fontSize: 14 }} />
        Expand Connections
      </Typography>

      <MenuItem onClick={() => handleExpand(1)} dense>
        <ListItemIcon sx={{ minWidth: 32 }}>
          <Filter1Icon fontSize="small" sx={{ color: 'primary.main' }} />
        </ListItemIcon>
        <ListItemText
          primary="1 Level"
          secondary="Direct neighbors"
          primaryTypographyProps={{ variant: 'body2' }}
          secondaryTypographyProps={{ variant: 'caption' }}
        />
      </MenuItem>

      <MenuItem onClick={() => handleExpand(2)} dense>
        <ListItemIcon sx={{ minWidth: 32 }}>
          <Filter2Icon fontSize="small" sx={{ color: 'primary.main' }} />
        </ListItemIcon>
        <ListItemText
          primary="2 Levels"
          secondary="Neighbors of neighbors"
          primaryTypographyProps={{ variant: 'body2' }}
          secondaryTypographyProps={{ variant: 'caption' }}
        />
      </MenuItem>

      <MenuItem onClick={() => handleExpand(3)} dense>
        <ListItemIcon sx={{ minWidth: 32 }}>
          <Filter3Icon fontSize="small" sx={{ color: 'primary.main' }} />
        </ListItemIcon>
        <ListItemText
          primary="3 Levels"
          secondary="Extended neighborhood"
          primaryTypographyProps={{ variant: 'body2' }}
          secondaryTypographyProps={{ variant: 'caption' }}
        />
      </MenuItem>
    </Paper>
  );
}

export default NodeContextMenu;
