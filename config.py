import json
from pathlib import Path

from tools import (
    read_files,
    search_codebase,
    editor,
    run_command,
    fetch_web,
    memory,
    skills,
    sessions,
    ask_question,
)

# The agent's own home: where this code lives. This is where the API key
# (.env) and default settings (agent_config.json) are found, no matter which
# workspace folder the agent is launched in.
AGENT_HOME = Path(__file__).resolve().parent

# Workspace state (sessions) lives in the folder the agent is launched in.
SESSIONS_DIR = Path(".simpleagent") / "sessions"
EXPORTS_DIR = Path(".simpleagent") / "exports"

CONFIG_PATH = AGENT_HOME / "agent_config.json"

DEFAULT_CONFIG = {
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "deepseek/deepseek-v4-flash",
    "context_window_tokens": 1000000,
    "max_agent_steps": 20,
    "show_tool_calls": True,
    "stream_messages": True,
    "context_warning_percent": 70,
    "approval_mode": "safe_auto",
    "reasoning": "high",
    "qt_streaming": False,
}

# Tool schemas for the LLM live in tools_schema.json so the code stays focused on logic.
TOOLS = json.loads((AGENT_HOME / "tools_schema.json").read_text(encoding="utf-8"))

# Maps each tool name the model can call to the function that runs it.
AVAILABLE_TOOL = {
    "read_files": read_files,
    "search_codebase": search_codebase,
    "editor": editor,
    "run_command": run_command,
    "fetch_web": fetch_web,
    "memory": memory,
    "skills": skills,
    "sessions": sessions,
    "ask_question": ask_question,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"agent_config.json is invalid JSON: {error}") from error

    final_config = DEFAULT_CONFIG.copy()
    final_config.update(config)

    return final_config


def save_config(config: dict) -> None:
    final_config = DEFAULT_CONFIG.copy()
    final_config.update(config)

    CONFIG_PATH.write_text(
        json.dumps(final_config, indent=4) + "\n",
        encoding="utf-8",
    )


def estimate_message_tokens(messages: list[dict]) -> int:
    total_characters = 0

    for message in messages:
        content = message.get("content") or ""
        total_characters += len(str(content))

    return total_characters // 4


def get_context_window_tokens(config: dict) -> int:
    return int(config["context_window_tokens"])


def get_context_usage_percent(messages: list[dict], config: dict) -> float:
    context_window = max(1, get_context_window_tokens(config))
    estimated_tokens = estimate_message_tokens(messages)
    return estimated_tokens / context_window * 100


def get_context_warning_percent(config: dict) -> float:
    try:
        return float(config.get("context_warning_percent", 70))
    except (TypeError, ValueError):
        return 70.0


def context_health_warning(messages: list[dict], config: dict) -> str:
    percent = get_context_usage_percent(messages, config)
    threshold = get_context_warning_percent(config)

    if percent < threshold:
        return ""

    return (
        f"Context is about {percent:.1f}% full. "
        "Consider /compact before starting a large task."
    )
