import { useState } from 'react';
import {
  Box,
  TextField,
  IconButton,
  InputAdornment,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Tooltip,
  CircularProgress,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import FilterListIcon from '@mui/icons-material/FilterList';

const entityTypes = [
  { value: '', label: 'All Types' },
  { value: 'class', label: 'Class' },
  { value: 'function', label: 'Function' },
  { value: 'method', label: 'Method' },
  { value: 'module', label: 'Module' },
  { value: 'file', label: 'File' },
  { value: 'service', label: 'Service' },
  { value: 'api', label: 'API' },
  { value: 'database', label: 'Database' },
  { value: 'table', label: 'Table' },
];

const layers = [
  { value: '', label: 'All Layers' },
  { value: 'code', label: 'Code' },
  { value: 'service', label: 'Service' },
  { value: 'data', label: 'Data' },
  { value: 'product', label: 'Product' },
  { value: 'domain', label: 'Domain' },
];

function SearchBar({ onSearch, loading = false, sources = [] }) {
  const [query, setQuery] = useState('');
  const [entityType, setEntityType] = useState('');
  const [layer, setLayer] = useState('');
  const [sourceToolkit, setSourceToolkit] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  const handleSearch = () => {
    if (!query.trim() && !entityType && !layer && !sourceToolkit) return;

    onSearch({
      query: query.trim(),
      entity_type: entityType || undefined,
      layer: layer || undefined,
      source_toolkit: sourceToolkit || undefined,
      top_k: 50,
    });
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {/* Main search bar */}
      <Box sx={{ display: 'flex', gap: 1 }}>
        <TextField
          fullWidth
          size="small"
          placeholder="Search entities... (e.g., 'UserService', 'auth handler')"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyPress={handleKeyPress}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="action" fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: loading && (
              <InputAdornment position="end">
                <CircularProgress size={20} />
              </InputAdornment>
            ),
          }}
        />
        <Tooltip title="Filters">
          <IconButton
            size="small"
            onClick={() => setShowFilters(!showFilters)}
            color={showFilters ? 'primary' : 'default'}
          >
            <FilterListIcon />
          </IconButton>
        </Tooltip>
        <IconButton
          size="small"
          color="primary"
          onClick={handleSearch}
          disabled={loading}
        >
          <SearchIcon />
        </IconButton>
      </Box>

      {/* Filter row */}
      {showFilters && (
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Type</InputLabel>
            <Select
              value={entityType}
              label="Type"
              onChange={(e) => setEntityType(e.target.value)}
            >
              {entityTypes.map((type) => (
                <MenuItem key={type.value} value={type.value}>
                  {type.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Layer</InputLabel>
            <Select
              value={layer}
              label="Layer"
              onChange={(e) => setLayer(e.target.value)}
            >
              {layers.map((l) => (
                <MenuItem key={l.value} value={l.value}>
                  {l.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {sources.length > 0 && (
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Source</InputLabel>
              <Select
                value={sourceToolkit}
                label="Source"
                onChange={(e) => setSourceToolkit(e.target.value)}
              >
                <MenuItem value="">All Sources</MenuItem>
                {sources.map((source) => (
                  <MenuItem key={source.source_toolkit} value={source.source_toolkit}>
                    {source.source_toolkit}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
        </Box>
      )}
    </Box>
  );
}

export default SearchBar;
