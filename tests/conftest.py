"""Pytest configuration — ensure plugin imports resolve."""
import os
import sys

# Add pylon_inventory/ to sys.path so `plugins.inventory_plugin.*` resolves
_pylon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _pylon_root not in sys.path:
    sys.path.insert(0, _pylon_root)
