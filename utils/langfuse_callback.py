#!/usr/bin/python3
# coding=utf-8

#   Copyright 2025 EPAM Systems
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Langfuse tracing callback helper for inventory chat"""

from typing import Optional, Dict, Any
from contextlib import contextmanager
from pylon.core.tools import log
import requests


def fetch_langfuse_config(elitea_client) -> Optional[Dict[str, Any]]:
    """
    Fetch langfuse configuration from project credentials.

    Args:
        elitea_client: EliteAClient instance

    Returns:
        Dict with langfuse config (base_url, public_key, secret_key) or None
    """
    try:
        # Fetch configurations with type=langfuse and section=credentials
        url = f'{elitea_client.base_url}/api/v2/configurations/configurations/{elitea_client.project_id}?type=langfuse&section=credentials'
        response = requests.get(url, headers=elitea_client.headers, verify=False, timeout=10)
        response.raise_for_status()

        result = response.json()

        # Extract items from the response
        items = result.get('items', [])
        if not items:
            log.debug("No langfuse configuration found in project")
            return None

        # Return the data from the first langfuse configuration found
        config = items[0]
        data = config.get('data', {})
        if data:
            log.debug("Langfuse configuration found in project credentials")
            return data

        log.debug("Langfuse configuration data is empty")
        return None
    except Exception as e:
        log.warning(f"Failed to fetch langfuse config: {e}")
        return None


def create_langfuse_callback(
    langfuse_config: Optional[Dict[str, Any]],
    trace_name: str = "inventory-chat",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
):
    """
    Create a Langfuse CallbackHandler for LangChain integration.

    The Langfuse SDK 3.x requires initializing a Langfuse client first
    (which registers globally), then the CallbackHandler uses that client.

    Args:
        langfuse_config: Dict with base_url, public_key, secret_key
        trace_name: Name for the trace (e.g., "inventory-chat")
        session_id: Session/thread ID for grouping traces
        user_id: User ID for attribution
        metadata: Additional metadata dict (values should be strings)

    Returns:
        Tuple of (Langfuse client, CallbackHandler, trace_attrs) or (None, None, None)
        trace_attrs is a dict with session_id, user_id, metadata, trace_name for use with propagate_attributes
    """
    if not langfuse_config:
        log.debug("Langfuse config not provided, skipping tracing")
        return None, None, None

    base_url = langfuse_config.get('base_url')
    public_key = langfuse_config.get('public_key')
    secret_key = langfuse_config.get('secret_key')

    if not all([base_url, public_key, secret_key]):
        log.warning("Langfuse config incomplete, skipping tracing")
        return None, None, None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # Initialize Langfuse client first - this registers it globally
        # so the CallbackHandler can use it
        langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
        )

        # Create callback handler - it will use the globally registered client
        handler = CallbackHandler(
            public_key=public_key,
        )

        # Store trace attributes for use with propagate_attributes
        trace_attrs = {
            "trace_name": trace_name,
            "session_id": session_id,
            "user_id": user_id,
            "metadata": metadata,
        }

        log.info(f"Langfuse callback handler created for tracing: {trace_name}")
        return langfuse_client, handler, trace_attrs
    except ImportError:
        log.warning("langfuse package not installed, skipping tracing")
        return None, None, None
    except Exception as e:
        log.warning(f"Failed to create Langfuse callback: {e}")
        return None, None, None


@contextmanager
def langfuse_trace_context(trace_attrs: Optional[Dict[str, Any]]):
    """
    Context manager that wraps agent execution with Langfuse propagate_attributes.

    This sets trace-level attributes (user_id, session_id, metadata, trace_name)
    on all spans created within the context.

    Args:
        trace_attrs: Dict with session_id, user_id, metadata, trace_name
    """
    if trace_attrs is None:
        yield
        return

    try:
        from langfuse import propagate_attributes

        with propagate_attributes(
            trace_name=trace_attrs.get("trace_name"),
            session_id=trace_attrs.get("session_id"),
            user_id=trace_attrs.get("user_id"),
            metadata=trace_attrs.get("metadata"),
        ):
            yield
    except ImportError:
        yield
    except Exception as e:
        log.warning(f"Failed to set Langfuse trace context: {e}")
        yield


def flush_langfuse_callback(langfuse_client, handler):
    """
    Flush any pending traces from Langfuse.

    Args:
        langfuse_client: The Langfuse client instance to flush
        handler: The CallbackHandler instance (for future use)
    """
    if langfuse_client is None:
        return

    try:
        langfuse_client.flush()
        log.debug("Langfuse client flushed")
    except Exception as e:
        log.warning(f"Failed to flush Langfuse: {e}")
