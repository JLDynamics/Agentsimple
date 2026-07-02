"""Entry point: set up the client, then run the read-command-respond loop."""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from agent import compact_conversation, run_agent_loop
from config import AGENT_HOME, load_config
from prompt import current_system_message, refresh_system_message
from sessions import (
    choose_session,
    continue_most_recent,
    create_session_name,
    resume_session_picker,
    save_session,
)
from tools import set_llm, set_current_intent
from ui import (
    choose_mode,
    clear_conversation,
    print_welcome,
    rewind_conversation,
    show_context_warning,
    show_help,
    show_memory,
    show_skills,
    show_status,
    show_tools,
)


def clean_surrogates(text: str) -> str:
    """Remove broken Unicode surrogate characters that can crash some providers."""
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")


def main():
    load_dotenv(AGENT_HOME / ".env")
    config = load_config()

    # Map provider name to the env variable holding its API key
    PROVIDER_KEY_MAP = {
        "openrouter": "OPENROUTER_API_KEY",
        "opencode-go": "OPENCODE_GO_API_KEY",
    }

    provider = config.get("provider", "openrouter")
    key_name = PROVIDER_KEY_MAP.get(provider, "OPENROUTER_API_KEY")
    api_key = os.getenv(key_name)
    model_name = config["model"]

    if not api_key:
        raise ValueError(f"Missing {key_name} in your .env file.")

    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
        timeout=30.0,
    )

    set_llm(client, model_name)

    system_message = current_system_message()

    args = sys.argv[1:]

    if "--continue" in args or "-c" in args:
        current_session_name, messages = continue_most_recent(system_message)
    elif "--resume" in args or "-r" in args:
        current_session_name, messages = resume_session_picker(system_message)
    else:
        current_session_name = create_session_name()
        messages = [system_message]

    print_welcome(model_name, config, current_session_name, messages)

    while True:
        user_input = clean_surrogates(input("You: "))
        command = user_input.strip().lower()

        if command in ("exit", "/exit"):
            save_session(current_session_name, model_name, messages)
            print("Goodbye.")
            break

        if command == "/sessions":
            current_session_name, messages = choose_session(
                current_session_name,
                model_name,
                messages,
            )
            continue

        if command == "/status":
            show_status(model_name, messages, config)
            continue

        if command == "/tools":
            show_tools()
            continue

        if command == "/memory":
            show_memory()
            continue

        if command == "/skills":
            show_skills()
            continue

        if command == "/help":
            show_help()
            continue

        if command == "/clear":
            clear_conversation(messages)
            continue

        if command == "/mode":
            choose_mode(config)
            continue

        if command == "/compact":
            messages = compact_conversation(client, model_name, messages)
            save_session(current_session_name, model_name, messages)
            continue

        if command == "/rewind" or command.startswith("/rewind "):
            parts = user_input.strip().split()
            count = 1

            if len(parts) > 1:
                try:
                    count = int(parts[1])
                except ValueError:
                    count = 1

            rewind_conversation(messages, count)
            save_session(current_session_name, model_name, messages)
            continue

        # ── Start a new agent turn ──
        refresh_system_message(messages)
        set_current_intent(user_input)

        messages.append(
            {
                "role": "user",
                "content": user_input,
            }
        )

        # Automatically compress if the conversation is getting too large
        import config as cfg_mod
        usage = cfg_mod.get_context_usage_percent(messages, config)
        if usage > 75:
            print(f"📦 Context is {usage:.0f}% full — auto-compressing...")
            messages = compact_conversation(client, model_name, messages)
            save_session(current_session_name, model_name, messages)
            print()

        # Save BEFORE the AI call so a crash mid-response doesn't lose everything
        save_session(current_session_name, model_name, messages)

        try:
            run_agent_loop(
                client,
                model_name,
                messages,
                int(config["max_agent_steps"]),
                bool(config["stream_messages"]),
                str(config["approval_mode"]),
                bool(config["show_tool_calls"]),
            )
        except KeyboardInterrupt:
            # Ctrl+C during a turn: save partial results and continue.
            messages.append(
                {
                    "role": "assistant",
                    "content": "[Interrupted by user]",
                }
            )
            save_session(current_session_name, model_name, messages)

            print()
            print("⏹️  Interrupted. Messages so far are saved.")
            print()

        save_session(current_session_name, model_name, messages)
        show_context_warning(messages, config)


if __name__ == "__main__":
    main()
