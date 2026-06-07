"""The agent's brain: running tools, planning, the step loop, and compaction."""

import json

from config import AVAILABLE_TOOL, TOOLS
from llm import complete
from tools import log_tool_call
from ui import console, print_agent_markdown


def run_tool(
    tool_name: str,
    argument_text: str,
    show_tool_calls: bool = False,
    approval_mode: str = "safe_auto",
) -> str:
    if show_tool_calls:
        print()
        print(f"[Tool call]: {tool_name}")
        print(f"[Arguments]: {argument_text}")
        print()

    if tool_name not in AVAILABLE_TOOL:
        result = f"ERROR: Unknown tool requested: {tool_name}"
        log_tool_call(tool_name, argument_text, result)
        return result

    try:
        arguments = json.loads(argument_text)
    except json.JSONDecodeError:
        result = "ERROR: Tool arguments were not valid JSON."
        log_tool_call(tool_name, argument_text, result)
        return result

    try:
        tool_function = AVAILABLE_TOOL[tool_name]
        if tool_name == "execute_terminal_command":
            result = tool_function(**arguments, approval_mode=approval_mode)
        else:
            result = tool_function(**arguments)
        log_tool_call(tool_name, argument_text, result)
        return result

    except TypeError as error:
        result = f"ERROR: Tool arguments were wrong: {error}"
        log_tool_call(tool_name, argument_text, result)
        return result

    except Exception as error:
        result = f"ERROR: Tool failed: {error}"
        log_tool_call(tool_name, argument_text, result)
        return result


def create_assistant_message(
    client,
    model_name,
    messages: list[dict],
    stream_messages: bool,
) -> dict:
    with console.status("Thinking...", spinner="dots"):
        assistant_record = complete(
            client, model_name, messages, stream_messages, tools=TOOLS
        )

    if assistant_record["content"]:
        print_agent_markdown(assistant_record["content"])

    return assistant_record


def normalize_turn_plan(plan: str) -> str:
    plan = plan.strip()

    if not plan or plan.upper() == "SKIP_PLAN":
        return ""

    return plan


def create_turn_plan(
    client,
    model_name,
    messages: list[dict],
    stream_messages: bool,
) -> str:
    planning_instruction = {
        "role": "system",
        "content": (
            "Before tools are available, decide whether this user turn needs a plan. "
            "If the user is asking a quick question that can be answered directly, "
            "reply exactly SKIP_PLAN. Otherwise write 2 to 4 short sentences explaining "
            "what you will check, why, and how you will verify the result. Do not call "
            "tools because tools are not available in this planning step."
        ),
    }

    try:
        with console.status("Planning...", spinner="dots"):
            assistant_record = complete(
                client, model_name, messages + [planning_instruction], stream_messages
            )
            plan = assistant_record["content"]

    except Exception:
        return ""

    plan = normalize_turn_plan(plan)

    if plan:
        print_agent_markdown(plan)

    return plan


def maybe_create_turn_plan(
    client,
    model_name,
    messages: list[dict],
    config: dict,
) -> None:
    if not config.get("plan_before_tools"):
        return

    plan = create_turn_plan(
        client,
        model_name,
        messages,
        bool(config["stream_messages"]),
    )

    if plan:
        messages.append(
            {
                "role": "assistant",
                "content": plan,
            }
        )


def create_tool_result_summary(
    client,
    model_name,
    messages: list[dict],
    stream_messages: bool,
) -> str:
    summary_instruction = {
        "role": "system",
        "content": (
            "Briefly summarize what the latest tool results showed and what that means "
            "for the next step. Use 1 to 3 short sentences. Do not call tools. "
            "Do not give a final answer unless the task is clearly complete from the "
            "tool results."
        ),
    }

    try:
        with console.status("Reading tool results...", spinner="dots"):
            assistant_record = complete(
                client, model_name, messages + [summary_instruction], stream_messages
            )
            summary = assistant_record["content"]

    except Exception as error:
        return f"Tool result summary failed: {error}"

    summary = summary.strip()

    if summary:
        print_agent_markdown(summary)

    return summary


# Tool calls are always dicts by the time they reach here (llm.py normalizes them).
def get_tool_call_name(tool_call) -> str:
    return tool_call["function"]["name"]


def get_tool_call_arguments(tool_call) -> str:
    return tool_call["function"]["arguments"]


def get_tool_call_id(tool_call) -> str:
    return tool_call["id"]


# Argument keys we show after a tool name, in priority order, to hint what it acts on.
TOOL_DETAIL_KEYS = ("path", "paths", "source", "command", "pattern", "query", "name", "test_path")


def describe_tool_calls(tool_calls) -> str:
    parts = []

    for tool_call in tool_calls:
        name = get_tool_call_name(tool_call)
        try:
            arguments = json.loads(get_tool_call_arguments(tool_call))
        except json.JSONDecodeError:
            arguments = {}

        detail = next((arguments[key] for key in TOOL_DETAIL_KEYS if arguments.get(key)), "")
        if isinstance(detail, list):
            detail = ", ".join(str(item) for item in detail)

        parts.append(f"{name}: {detail}" if detail else name)

    if not parts:
        return "I will use tools to continue checking this."

    return "I will use " + "; ".join(parts) + "."


def print_tool_activity_status(tool_calls) -> None:
    status = describe_tool_calls(tool_calls)

    if status.startswith("I will "):
        status = status.removeprefix("I will ")

    print()
    print(f"Working: {status}")
    print()


def assistant_record_for_history(assistant_record: dict) -> dict:
    history_message = {
        "role": "assistant",
        "content": assistant_record["content"],
    }

    if assistant_record["tool_calls"]:
        history_message["tool_calls"] = assistant_record["tool_calls"]

    return history_message


def run_agent_loop(
    client,
    model_name,
    messages,
    max_agent_steps: int,
    tool_display: str,
    stream_messages: bool,
    approval_mode: str,
    summarize_tool_results: bool,
):
    for step_number in range(1, max_agent_steps + 1):
        assistant_record = create_assistant_message(
            client,
            model_name,
            messages,
            stream_messages,
        )

        tool_calls = assistant_record["tool_calls"] or []

        if tool_calls and tool_display == "summary":
            print_tool_activity_status(tool_calls)

        if not tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_record["content"],
                }
            )
            return

        messages.append(assistant_record_for_history(assistant_record))

        for tool_call in tool_calls:
            tool_result = run_tool(
                get_tool_call_name(tool_call),
                get_tool_call_arguments(tool_call),
                tool_display == "verbose",
                approval_mode,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": get_tool_call_id(tool_call),
                    "content": tool_result,
                }
            )

        if summarize_tool_results:
            summary = create_tool_result_summary(
                client,
                model_name,
                messages,
                stream_messages,
            )

            if summary:
                messages.append(
                    {
                        "role": "assistant",
                        "content": summary,
                    }
                )

    stop_message = f"Stopped because max_agent_steps ({max_agent_steps}) was reached."

    print()
    print(f"Agent: {stop_message}")
    print()

    messages.append(
        {
            "role": "assistant",
            "content": stop_message,
        }
    )


def compact_conversation(client, model_name, messages) -> list[dict]:
    if len(messages) <= 1:
        print()
        print("Nothing to compact yet.")
        print()
        return messages

    remember = input("What should i definitely remember? ").strip()
    ignore = input("What should i ignore or drop? ").strip()

    confirm = input("Proceed with compaction? (y/n) ").strip().lower()

    if confirm != "y":
        print()
        print("Compaction cancelled.")
        print()
        return messages

    system_message = messages[0]

    compaction_prompt = (
        "Summarize this coding-agent conversation so it can be resumed later."
        "Focus on the user's goals, project state, important decisions, files changed,"
        "bugs fixed, current open tasks, and any instructions the assistant should remember.\n\n"
    )

    if remember:
        compaction_prompt += f"Definitely remember this:\n{remember}\n\n"

    if ignore:
        compaction_prompt += f"Ignore or drop this:\n{ignore}\n\n"

    compaction_prompt += (
        "Conversation messages:\n"
        + json.dumps(messages[1:], indent=2)
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "You write compact but useful session summaries for coding agents.",
            },
            {
                "role": "user",
                "content": compaction_prompt,
            },
        ]
    )

    summary = response.choices[0].message.content or ""

    compacted_messages = [
        system_message,
        {
            "role": "system",
            "content": "Previous conversation summary:\n" + summary,
        },
    ]

    print()
    print("Conversation compacted.")
    print(f"Message reduced from {len(messages)} to {len(compacted_messages)}.")
    print()

    return compacted_messages
