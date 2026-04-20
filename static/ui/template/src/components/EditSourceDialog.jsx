// EditSourceDialog.jsx
// Dialog for configuring source whitelist/blacklist patterns

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
  IconButton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Alert,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';

// Language presets with their default patterns
const LANGUAGE_PRESETS = {
  '': { name: 'None', whitelist: '', blacklist: '' },
  python: {
    name: 'Python',
    whitelist: '**/*.py',
    blacklist: '**/test/**,**/tests/**,**/__pycache__/**,**/venv/**,**/.venv/**,**/dist/**,**/build/**',
  },
  typescript: {
    name: 'TypeScript/JavaScript',
    whitelist: '**/*.ts,**/*.tsx,**/*.js,**/*.jsx',
    blacklist: '**/node_modules/**,**/dist/**,**/build/**,**/*.test.*,**/*.spec.*,**/coverage/**',
  },
  java: {
    name: 'Java',
    whitelist: '**/*.java',
    blacklist: '**/target/**,**/build/**,**/*Test.java,**/*Tests.java,**/test/**',
  },
  csharp: {
    name: 'C#/.NET',
    whitelist: '**/*.cs',
    blacklist: '**/bin/**,**/obj/**,**/*Test*.cs,**/Tests/**',
  },
  go: {
    name: 'Go',
    whitelist: '**/*.go',
    blacklist: '**/vendor/**,**/*_test.go,**/testdata/**',
  },
  rust: {
    name: 'Rust',
    whitelist: '**/*.rs',
    blacklist: '**/target/**,**/*_test.rs,**/tests/**',
  },
};

export default function EditSourceDialog({
  open,
  onClose,
  onSave,
  source,
}) {
  const [filePatterns, setFilePatterns] = useState('');
  const [excludePatterns, setExcludePatterns] = useState('');
  const [branch, setBranch] = useState('');
  const [preset, setPreset] = useState('');
  const [showHelp, setShowHelp] = useState(false);

  // Initialize form when dialog opens or source changes
  useEffect(() => {
    if (open && source) {
      setFilePatterns(source.file_patterns || '');
      setExcludePatterns(source.exclude_patterns || '');
      setBranch(source.branch || '');
      setPreset(source.preset || '');
    }
  }, [open, source]);

  const handlePresetChange = (event) => {
    const newPreset = event.target.value;
    setPreset(newPreset);

    // Apply preset patterns (only if current fields are empty or changing from another preset)
    const presetConfig = LANGUAGE_PRESETS[newPreset];
    if (presetConfig) {
      if (!filePatterns || preset) {
        setFilePatterns(presetConfig.whitelist);
      }
      if (!excludePatterns || preset) {
        setExcludePatterns(presetConfig.blacklist);
      }
    }
  };

  const handleSave = () => {
    onSave({
      file_patterns: filePatterns.trim(),
      exclude_patterns: excludePatterns.trim(),
      branch: branch.trim(),
      preset: preset,
    });
  };

  const handleClear = () => {
    setFilePatterns('');
    setExcludePatterns('');
    setBranch('');
    setPreset('');
  };

  const displayName = source?.toolkit_name || `Toolkit ${source?.toolkit_id}`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ sx: { maxHeight: '90vh' } }}
    >
      <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Box>
          <Typography variant="h6">Configure Source</Typography>
          <Typography variant="body2" color="text.secondary">
            {displayName}
          </Typography>
        </Box>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers>
        {/* Language Preset Selector */}
        <FormControl fullWidth variant="standard" sx={{ mb: 3 }}>
          <InputLabel shrink>Language Preset</InputLabel>
          <Select
            value={preset}
            onChange={handlePresetChange}
            displayEmpty
          >
            {Object.entries(LANGUAGE_PRESETS).map(([key, config]) => (
              <MenuItem key={key} value={key}>
                {config.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* File Patterns (Whitelist) */}
        <TextField
          fullWidth
          variant="standard"
          label="Include Patterns (Whitelist)"
          placeholder="**/*.py, **/*.js, src/**"
          value={filePatterns}
          onChange={(e) => setFilePatterns(e.target.value)}
          multiline
          rows={2}
          sx={{ mb: 3 }}
          helperText="Comma-separated glob patterns of files to include"
          InputLabelProps={{ shrink: true }}
        />

        {/* Exclude Patterns (Blacklist) */}
        <TextField
          fullWidth
          variant="standard"
          label="Exclude Patterns (Blacklist)"
          placeholder="**/test/**, **/node_modules/**, **/vendor/**"
          value={excludePatterns}
          onChange={(e) => setExcludePatterns(e.target.value)}
          multiline
          rows={2}
          sx={{ mb: 3 }}
          helperText="Comma-separated glob patterns of files to exclude"
          InputLabelProps={{ shrink: true }}
        />

        {/* Branch Override */}
        <TextField
          fullWidth
          variant="standard"
          label="Branch (Optional)"
          placeholder="main, develop, feature/..."
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          sx={{ mb: 2 }}
          helperText="Override default branch for this source"
          InputLabelProps={{ shrink: true }}
        />

        {/* Help Section */}
        <Accordion
          expanded={showHelp}
          onChange={() => setShowHelp(!showHelp)}
          sx={{ mt: 1 }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <HelpOutlineIcon fontSize="small" color="action" />
              <Typography variant="body2">Pattern Examples</Typography>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <Box>
                <Typography variant="caption" fontWeight={600}>Whitelist examples:</Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                  <Chip label="**/*.py" size="small" variant="outlined" />
                  <Chip label="src/**/*.ts" size="small" variant="outlined" />
                  <Chip label="**/*.{js,jsx}" size="small" variant="outlined" />
                </Box>
              </Box>
              <Box>
                <Typography variant="caption" fontWeight={600}>Blacklist examples:</Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                  <Chip label="**/test/**" size="small" variant="outlined" />
                  <Chip label="**/node_modules/**" size="small" variant="outlined" />
                  <Chip label="**/*.test.js" size="small" variant="outlined" />
                </Box>
              </Box>
              <Alert severity="info" sx={{ mt: 1 }}>
                <Typography variant="caption">
                  <strong>**</strong> matches any directory path<br />
                  <strong>*</strong> matches any filename<br />
                  Patterns are comma-separated
                </Typography>
              </Alert>
            </Box>
          </AccordionDetails>
        </Accordion>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={handleClear} color="inherit" sx={{ mr: 'auto' }}>
          Clear All
        </Button>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained">
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
