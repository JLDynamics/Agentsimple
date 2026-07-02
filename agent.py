"""The agent's brain: running tools, the step loop, and compaction."""

import json
import sys

from config import AVAILABLE_TOOL, TOOLS
from llm import complete
from tools import log_tool_call
from ui import console, print_agent_markdown

# When True, the next final-response stream will clear all intermediate
# output (tool calls, reasoning) before showing the response.
_clear_before_response = False


def run_tool(
    tool_name: str,
    argument_text: str,
    show_tool_calls: bool = False,
    approval_mode: str = "safe_auto",
) -> str:
    if show_tool_calls:
        # Show a compact one-line tool call preview
        try:
            args_obj = json.loads(argument_text) if argument_text.strip() else {}
        except json.JSONDecodeError:
            args_obj = {}
        args_summary = ", ".join(
            f"{k}={str(v)[:80]}" for k, v in args_obj.items()
        )[:200]
        print(f"🔧 {tool_name}({args_summary})")

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
        if tool_name == "run_command":
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
    global _clear_before_response

    if stream_messages:
        from rich.markdown import Markdown

        header_printed = [False]
        status = console.status("Thinking...", spinner="dots")
        status.start()

        # Buffer all streamed content and track cursor for markdown re-render
        all_content = []
        cursor_saved = False

        def on_chunk(chunk):
            nonlocal cursor_saved
            global _clear_before_response
            # Save cursor position before first live-chunk so we can later
            # clear the live text and re-render as pretty markdown
            if _clear_before_response:
                sys.stdout.write("\033[s")
                sys.stdout.flush()
                _clear_before_response = False
                cursor_saved = True

            if not header_printed[0]:
                status.stop()
                console.print()
                console.print("Agent", style="bold cyan")
                header_printed[0] = True
            all_content.append(chunk)
            print(chunk, end="", flush=True)

        def on_reasoning(chunk):
            if not header_printed[0]:
                status.stop()
                print("🧠 ", end="", flush=True)
                header_printed[0] = True
            print(chunk, end="", flush=True)

        try:
            assistant_record = complete(
                client, model_name, messages, stream_messages,
                tools=TOOLS, on_chunk=on_chunk, on_reasoning=on_reasoning,
            )
        finally:
            status.stop()

        # Live streaming is done — replace raw text with rendered markdown
        if all_content and cursor_saved:
            full = "".join(all_content)
            sys.stdout.write("\033[u\033[J")
            sys.stdout.flush()
            console.print()
            console.print("Agent", style="bold cyan")
            console.print(Markdown(full.strip()))
            console.print()

        if header_printed[0] and not cursor_saved:
            # Content was streamed but cursor wasn't saved (intermediate step
            # with tool calls) — just add spacing
            print()
            print()

        return assistant_record

    # Non-streamed: spinner, then markdown-rendered response.
    with console.status("Thinking...", spinner="dots"):
        assistant_record = complete(
            client, model_name, messages, stream_messages, tools=TOOLS
        )

    if _clear_before_response:
        sys.stdout.write("\033[u\033[J")
        sys.stdout.flush()
        _clear_before_response = False
        console.print()

    if assistant_record["content"]:
        print_agent_markdown(assistant_record["content"])

    return assistant_record


# Tool calls are always dicts by the time they reach here (llm.py normalizes them).
def get_tool_call_name(tool_call) -> str:
    return tool_call["function"]["name"]


def get_tool_call_arguments(tool_call) -> str:
    return tool_call["function"]["arguments"]


def get_tool_call_id(tool_call) -> str:
    return tool_call["id"]


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
    stream_messages: bool,
    approval_mode: str,
    show_tool_calls: bool,
):
    global _clear_before_response

    # Save cursor position at the start of the turn.
    # Before the final response, we restore here and clear everything
    # below (tool calls, reasoning, etc.) so only the final answer remains.
    sys.stdout.write("\033[s")
    sys.stdout.flush()

    for step_number in range(1, max_agent_steps + 1):
        # Reset before each LLM call so the final step re-renders as markdown
        _clear_before_response = True

        assistant_record = create_assistant_message(
            client,
            model_name,
            messages,
            stream_messages,
        )

        tool_calls = assistant_record["tool_calls"] or []

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
            try:
                tool_result = run_tool(
                    get_tool_call_name(tool_call),
                    get_tool_call_arguments(tool_call),
                    show_tool_calls,
                    approval_mode,
                )
            except Exception as tool_error:
                tool_result = f"ERROR: Tool failed: {tool_error}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": get_tool_call_id(tool_call),
                    "content": tool_result,
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
