"""Entry point: set up the client, then run the read-command-respond loop."""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from agent import compact_conversation, maybe_create_turn_plan, run_agent_loop
from config import AGENT_HOME, get_tool_display, load_config
from prompt import current_system_message, refresh_system_message
from sessions import (
    choose_session,
    continue_most_recent,
    create_session_name,
    resume_session_picker,
    save_session,
)
from tools import set_llm
from ui import (
    choose_mode,
    clear_conversation,
    is_skill_question,
    print_welcome,
    rewind_conversation,
    show_context_warning,
    show_help,
    show_memory,
    show_skills,
    show_status,
    show_tools,
)


def main():
    load_dotenv(AGENT_HOME / ".env")
    config = load_config()

    api_key = os.getenv("OPENROUTER_API_KEY")
    model_name = config["model"]

    if not api_key:
        raise ValueError("Missing OPENROUTER_API_KEY in your .env file.")

    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
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
        user_input = input("You: ")
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

        if is_skill_question(user_input):
            show_skills()
            continue

        refresh_system_message(messages)
        turn_start_index = len(messages)

        messages.append(
            {
                "role": "user",
                "content": user_input,
            }
        )

        try:
            maybe_create_turn_plan(
                client,
                model_name,
                messages,
                config,
            )

            run_agent_loop(
                client,
                model_name,
                messages,
                int(config["max_agent_steps"]),
                get_tool_display(config),
                bool(config["stream_messages"]),
                str(config["approval_mode"]),
                bool(config["summarize_tool_results"]),
            )
            save_session(current_session_name, model_name, messages)
            show_context_warning(messages, config)
        except KeyboardInterrupt:
            del messages[turn_start_index:]

            print()
            print("Interrupted. Last turn removed.")
            print()


if __name__ == "__main__":
    main()
