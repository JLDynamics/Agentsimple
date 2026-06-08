"""Everything the user sees: the shared console, printing, and the /show commands."""

import os
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import tools
from config import (
    AVAILABLE_TOOL,
    TOOLS,
    context_health_warning,
    estimate_message_tokens,
    get_context_window_tokens,
    get_tool_display,
    save_config,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

console = Console()


def print_agent_markdown(text: str) -> None:
    if not text.strip():
        return

    console.print()
    console.print("Agent", style="bold cyan")
    console.print(Markdown(text.strip()))
    console.print()


def print_welcome(
    model_name: str,
    config: dict,
    session_name: str,
    messages: list[dict],
) -> None:
    logo = Text()
    logo.append("   ▟██▙\n", style="bright_cyan")
    logo.append("  ▟█  █▙\n", style="bright_cyan")
    logo.append("  ▜█  █▛\n", style="bright_cyan")
    logo.append("   ▜██▛", style="bright_cyan")

    title = Text()
    title.append("Simple Agent", style="bold white")
    title.append("  v0.2.0\n", style="dim")
    title.append("A compact local coding agent\n\n", style="dim")
    title.append("Type ", style="dim")
    title.append("/help", style="cyan")
    title.append(" for commands · ", style="dim")
    title.append("/exit", style="cyan")
    title.append(" to quit", style="dim")

    header = Table.grid(padding=(0, 3))
    header.add_column()
    header.add_column()
    header.add_row(logo, title)

    console.print(Panel(header, border_style="cyan", padding=(1, 2)))

    estimated_tokens = estimate_message_tokens(messages)
    context_window = get_context_window_tokens(config)
    context_percent = round(estimated_tokens / context_window * 100, 2)

    info = Table.grid(padding=(0, 3))
    info.add_column(style="dim", justify="left")
    info.add_column(style="bright_cyan", justify="left")
    info.add_column(style="dim", justify="left")
    info.add_column(style="bright_cyan", justify="left")
    info.add_row("Model", model_name, "Tools", str(len(AVAILABLE_TOOL)))
    info.add_row("Provider", str(config["provider"]), "Approval", str(config["approval_mode"]))
    info.add_row("Session", session_name, "Context", f"{context_percent}%")

    console.print(info)
    console.print()


def show_status(model_name: str, messages: list[dict], config: dict) -> None:
    estimated_tokens = estimate_message_tokens(messages)
    context_window = get_context_window_tokens(config)
    context_percent = round(estimated_tokens / context_window * 100, 2)

    print()
    print("Agent Status")
    print(f"Model: {model_name}")
    print(f"Provider: {config['provider']}")
    print(f"Max agent steps: {config['max_agent_steps']}")
    print(f"Show tool calls: {config['show_tool_calls']}")
    print(f"Stream messages: {config['stream_messages']}")
    print(f"Tool display: {get_tool_display(config)}")
    print(f"Approval mode: {config['approval_mode']}")
    print(f"Project: {os.getcwd()}")
    print(f"Messages in memory: {len(messages)}")
    print(f"Available tools: {len(AVAILABLE_TOOL)}")
    print("Log file: .simpleagent/logs/tool_call.log")
    print(f"Approx tokens: {estimated_tokens} / {context_window}")
    print(f"Approx context used: {context_percent}%")
    warning = context_health_warning(messages, config)
    if warning:
        print(f"Context warning: {warning}")
    print()


def show_context_warning(messages: list[dict], config: dict) -> None:
    warning = context_health_warning(messages, config)

    if not warning:
        return

    print()
    print(warning)
    print()


def show_tools() -> None:
    print()
    print("Available Tools:")

    for tool in TOOLS:
        function_info = tool["function"]
        print(f"- {function_info['name']}: {function_info['description']}")

    print()


def show_memory() -> None:
    global_memory = tools.read_global_memory().strip()
    project_memory = tools.read_project_memory().strip()

    print()
    print("Global memory (about you):")
    print(global_memory if global_memory else "  (empty)")
    print()
    print("Project memory (this project):")
    print(project_memory if project_memory else "  (empty)")
    print()


def show_skills() -> None:
    print()
    print(tools.list_skills())
    print()


def is_skill_question(user_input: str) -> bool:
    text = " ".join(user_input.lower().split())

    direct_patterns = [
        "what skills",
        "which skills",
        "show skills",
        "list skills",
        "list saved skills",
        "saved skills",
    ]

    if any(pattern in text for pattern in direct_patterns):
        return True

    project_skill_patterns = [
        "do you have a project skill",
        "do you have any project skill",
        "is there a project skill",
        "project-specific skill",
        "project level skill",
        "project-level skill",
    ]

    return any(pattern in text for pattern in project_skill_patterns)


def show_help() -> None:
    print()
    print("Slash Commands:")
    print("/compact - Summarize and shrink conversation memory")
    print("/sessions - List, resume, start, delete, name, or export sessions")
    print("/status - Show model, project, memory, tool content, and log file")
    print("/tools - show available agent tools")
    print("/memory - Show what the agent remembers (global and project)")
    print("/skills - List the skills the agent has saved")
    print("/help - Show this help message")
    print("/mode    Switch approval mode (safe_auto / full_auto)")
    print("/rewind [n] - Undo the last n turns (default 1)")
    print("/clear - Clear conversation memory")
    print("exit - Quit the program")
    print()


def clear_conversation(messages: list[dict]) -> None:
    system_message = messages[0]

    messages.clear()
    messages.append(system_message)

    print()
    print("Conversation cleared. Starting fresh!")
    print()


def rewind_conversation(messages: list[dict], count: int) -> int:
    user_indices = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
    ]

    if not user_indices:
        print()
        print("Nothing to rewind yet.")
        print()
        return 0

    count = max(1, count)

    if count >= len(user_indices):
        cut = user_indices[0]
    else:
        cut = user_indices[-count]

    removed_turns = sum(1 for index in user_indices if index >= cut)
    del messages[cut:]

    print()
    print(f"Rewound {removed_turns} turn(s). {len(messages)} messages remain.")
    print()

    return removed_turns


def choose_mode(config: dict) -> None:
    modes = ["safe_auto", "full_auto"]
    current_mode = config.get("approval_mode", "safe_auto")

    print()
    print(f"Current approval mode: {current_mode}")
    print()
    print("1. safe_auto  auto-allow safe commands, ask for risky ones")
    print("2. full_auto  allow all commands automatically")
    print()

    choice = input("Enter number (or press Enter to keep current): ").strip()

    if not choice:
        print()
        print(f"Mode unchanged: {current_mode}")
        print()
        return

    if choice not in ("1", "2"):
        print()
        print("Invalid choice. Mode unchanged.")
        print()
        return

    selected_mode = modes[int(choice) - 1]
    config["approval_mode"] = selected_mode

    try:
        save_config(config)
    except Exception as error:
        print()
        print(f"Mode changed for this run only: {selected_mode}")
        print(f"Could not save agent_config.json: {error}")
