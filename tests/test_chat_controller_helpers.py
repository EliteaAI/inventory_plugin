"""Focused tests for inventory chat controller helper logic."""

import json
import sys
import time
import types


class _DummyLog:
    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


class _DummyWeb:
    def method(self, *_args, **_kwargs):
        return lambda func: func


if "pylon.core.tools" not in sys.modules:
    pylon_module = types.ModuleType("pylon")
    core_module = types.ModuleType("pylon.core")
    tools_module = types.ModuleType("pylon.core.tools")
    tools_module.log = _DummyLog()
    tools_module.web = _DummyWeb()
    sys.modules["pylon"] = pylon_module
    sys.modules["pylon.core"] = core_module
    sys.modules["pylon.core.tools"] = tools_module

from plugins.inventory_plugin.methods import invoke as invoke_module
from plugins.inventory_plugin.methods.inventory_chat import InventoryChatCallback, Method as ChatMethod


def test_platform_settings_from_llm_settings_strips_llm_suffix():
    settings = invoke_module._platform_settings_from_llm_settings({
        "api_base": "http://platform.example/llm/api/v1/",
        "api_key": "token",
        "organization": "42",
    })

    assert settings == {
        "base_url": "http://platform.example",
        "api_key": "token",
        "project_id": "42",
    }


def test_chat_filters_accept_comma_strings_and_lists():
    filters = invoke_module._chat_filters_from_params({
        "entity_types": [" class ", "", "function"],
        "sources": "github, confluence, ",
        "layers": "code",
        "depth": 3,
        "max_nodes": 25,
    })

    assert filters == {
        "entity_types": ["class", "function"],
        "sources": ["github", "confluence"],
        "layers": ["code"],
        "depth": 3,
        "max_nodes": 25,
    }


def test_chat_history_from_json_array():
    history = [{"role": "user", "content": "hello"}]

    assert invoke_module._chat_history_from_param(json.dumps(history)) == history
    assert invoke_module._chat_history_from_param("not-json") == []
    assert invoke_module._chat_history_from_param({"role": "user"}) == []


def test_tool_start_maps_to_tool_node_event():
    event = invoke_module._chat_callback_custom_event(
        "tool_start",
        {"run_id": "run-1", "tool_name": "search_knowledge_graph", "input": {"q": "auth"}},
    )

    assert event["name"] == "on_tool_node"
    assert event["data"]["input_variables"] == {
        "tool": "search_knowledge_graph",
        "kind": "tool",
        "args": {"q": "auth"},
    }
    assert event["data"]["state"] == {
        "run_id": "run-1",
        "status": "processing",
        "duration_ms": None,
    }


def test_llm_start_does_not_forward_prompt_as_chip_input():
    event = invoke_module._chat_callback_custom_event(
        "llm_start",
        {"run_id": "llm-1", "model": "ChatOpenAI", "input": "system prompt"},
    )

    assert event["name"] == "on_tool_node"
    assert event["data"]["input_variables"] == {
        "tool": "ChatOpenAI",
        "kind": "llm",
        "args": None,
    }
    assert event["data"]["tool_result"] is None


def test_llm_end_maps_label_output_and_duration():
    event = invoke_module._chat_callback_custom_event(
        "llm_end",
        {"run_id": "llm-1", "model": "ChatAnthropic", "output": "done", "duration_ms": 17},
    )

    assert event["name"] == "on_tool_node"
    assert event["data"]["input_variables"]["tool"] == "ChatAnthropic"
    assert event["data"]["input_variables"]["kind"] == "llm"
    assert event["data"]["tool_result"] == "done"
    assert event["data"]["state"]["duration_ms"] == 17


def test_reasoning_thinking_step_is_not_forwarded():
    assert invoke_module._chat_callback_custom_event(
        "thinking_step",
        {"message": "private reasoning", "is_reasoning_token": True},
    ) is None

    assert invoke_module._chat_callback_custom_event(
        "thinking_step",
        {"message": "Searching graph", "is_reasoning_token": False},
    ) == {"name": "thinking_step_update", "data": {"message": "Searching graph"}}


def test_llm_display_label_prefers_anthropic_model_hint():
    method = ChatMethod()

    assert method._llm_display_label(
        "company-claude-alias",
        {"provider": "openai"},
        {},
    ) == "ChatAnthropic"
    assert method._llm_display_label(
        "gpt-5.4-mini",
        {"provider": "openai"},
        {},
    ) == "ChatOpenAI"


def test_llm_end_carries_stored_display_model():
    emitted = []
    callback = InventoryChatCallback(lambda event_type, data: emitted.append((event_type, data)), "session-1")
    callback.tool_runs["run-1"] = {
        "name": "ChatAnthropic",
        "type": "llm",
        "start_time": time.time(),
    }

    callback.on_llm_end(None, run_id="run-1")

    assert emitted[-1][0] == "llm_end"
    assert emitted[-1][1]["model"] == "ChatAnthropic"
    assert emitted[-1][1]["duration_ms"] >= 0