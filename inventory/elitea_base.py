"""Minimal base classes for standalone inventory module"""

import logging
import re
from typing import Any, Optional, Dict, Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, ToolException
from langchain_core.callbacks import CallbackManagerForToolRun

logger = logging.getLogger(__name__)


class BaseToolApiWrapper(BaseModel):
    """
    Base class for tool API wrappers.
    Simplified version for standalone inventory module.
    """
    
    # Optional RunnableConfig for CLI/standalone usage
    _runnable_config: Optional[Dict[str, Any]] = None
    # toolkit id propagated from backend
    toolkit_id: int = 0

    def get_available_tools(self):
        raise NotImplementedError("Subclasses should implement this method")

    def set_runnable_config(self, config: Optional[Dict[str, Any]]) -> None:
        """
        Set the RunnableConfig for dispatching custom events.
        
        Args:
            config: A RunnableConfig dict with at least {'run_id': uuid}
        """
        self._runnable_config = config

    def _log_tool_event(self, message: str, tool_name: str = None, config: Optional[Dict[str, Any]] = None):
        """Log data and dispatch custom event for the tool.
        
        Args:
            message: The message to log
            tool_name: Name of the tool (defaults to 'tool_progress')
            config: Optional RunnableConfig
        """
        try:
            from langchain_core.callbacks import dispatch_custom_event

            if tool_name is None:
                tool_name = 'tool_progress'

            logger.info(message)
            
            # Use provided config, fall back to instance config
            effective_config = config or self._runnable_config
            
            dispatch_custom_event(
                name="thinking_step",
                data={
                    "message": message,
                    "tool_name": tool_name,
                    "toolkit": self.__class__.__name__,
                },
                config=effective_config,
            )
        except Exception as e:
            logger.warning(f"Failed to dispatch progress event: {str(e)}")


class BaseAction(BaseTool):
    """Tool for interacting with API wrappers."""

    api_wrapper: BaseModel = Field(default_factory=BaseModel)
    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def _run(
        self,
        *args: Any,
        run_manager: Optional[CallbackManagerForToolRun] = None,
        **kwargs: Any,
    ) -> ToolException | str:
        """Use the API wrapper to run an operation."""
        try:
            # Strip numeric suffix added for deduplication (_2, _3, etc.)
            tool_name = re.sub(r'_\d+$', '', self.name)
            return self.api_wrapper.run(tool_name, *args, **kwargs)
        except Exception as e:
            return ToolException(f"An exception occurred: {e}")


# Utility functions

def clean_string(s: str, max_length: int = 0):
    """Clean string by removing non-alphanumeric characters."""
    pattern = '[^a-zA-Z0-9_.-]'
    cleaned_string = re.sub(pattern, '', s).replace('.', '_')
    return cleaned_string[:max_length] if max_length > 0 else cleaned_string


def get_max_toolkit_length(selected_tools: Any):
    """Get maximum toolkit length (deprecated, returns fixed value)."""
    return 50
