import json
from pathlib import Path

from tools import (
    apply_patch,
    compile_python,
    delete_file,
    delete_skill,
    execute_terminal_command,
    get_file_info,
    git_diff,
    git_log,
    git_status,
    glob_files,
    list_files,
    list_project_tree,
    list_skills,
    move_file,
    read_file,
    read_file_range,
    read_many_files,
    read_session,
    read_skill,
    run_python_tests,
    save_skill,
    search_files,
    search_sessions,
    update_global_memory,
    update_project_memory,
    web_fetch,
    write_file,
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
    "show_tool_calls": False,
    "stream_messages": True,
    "context_warning_percent": 70,
    "tool_display": "summary",
    # Off by default to keep each turn to one LLM call. Enable for extra hand-holding.
    "plan_before_tools": False,
    "summarize_tool_results": False,
    "approval_mode": "safe_auto",
}

# Tool schemas for the LLM live in tools_schema.json so the code stays focused on logic.
TOOLS = json.loads((AGENT_HOME / "tools_schema.json").read_text(encoding="utf-8"))

# Maps each tool name the model can call to the function that runs it.
AVAILABLE_TOOL = {
    "list_files": list_files,
    "list_project_tree": list_project_tree,
    "glob_files": glob_files,
    "get_file_info": get_file_info,
    "read_file": read_file,
    "read_file_range": read_file_range,
    "read_many_files": read_many_files,
    "write_file": write_file,
    "apply_patch": apply_patch,
    "delete_file": delete_file,
    "move_file": move_file,
    "search_files": search_files,
    "run_python_tests": run_python_tests,
    "compile_python": compile_python,
    "git_status": git_status,
    "git_diff": git_diff,
    "git_log": git_log,
    "execute_terminal_command": execute_terminal_command,
    "web_fetch": web_fetch,
    "update_global_memory": update_global_memory,
    "update_project_memory": update_project_memory,
    "search_sessions": search_sessions,
    "read_session": read_session,
    "list_skills": list_skills,
    "read_skill": read_skill,
    "save_skill": save_skill,
    "delete_skill": delete_skill,
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


def get_tool_display(config: dict) -> str:
    tool_display = str(config.get("tool_display", "")).strip().lower()

    if tool_display in ("hidden", "summary", "verbose"):
        return tool_display

    if config.get("show_tool_calls"):
        return "verbose"

    return "summary"
