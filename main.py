import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from tools import (
    apply_patch,
    execute_terminal_command,
    glob_files,
    list_files,
    log_tool_call,
    read_file,
    search_files,
    write_file,
)

AVAILABLE_TOOL = {
    "list_files": list_files,
    "glob_files": glob_files,
    "read_file": read_file,
    "write_file": write_file,
    "apply_patch": apply_patch,
    "search_files": search_files,
    "execute_terminal_command": execute_terminal_command,
}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and folders inside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path relative to the project root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
    "type": "function",
    "function": {
        "name": "glob_files",
        "description": "Find files inside the project using a filename pattern like *.py or *.md.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Filename pattern to match, such as *.py or *.txt.",
                },
                "path": {
                    "type": "string",
                    "description": "Folder path relative to the project root.",
                },
            },
            "required": ["pattern", "path"],
        },
    },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file inside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the project root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file inside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the project root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply one or more exact text replacements to a file inside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the project root.",
                    },
                    "replacements": {
                        "type": "array",
                        "description": "Exact text replacements to apply in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "Existing text to replace exactly once.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "Replacement text.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["path", "replacements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for text inside project files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Folder path relative to the project root.",
                    },
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_terminal_command",
            "description": (
                "Run a PowerShell command in the project folder. "
                "Use this for tests, scripts, git commands, uv commands, and shell commands. "
                "File commands are allowed, but prefer the specific file tools when they fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact PowerShell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
]


def run_tool(tool_name: str, argument_text: str) -> str:
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


def run_agent_loop(client, model_name, messages):
    while True:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            assistant_reply = assistant_message.content or ""

            print()
            print(f"Agent: {assistant_reply}")
            print()

            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                }
            )
            break

        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    tool_call.model_dump() for tool_call in assistant_message.tool_calls
                ],
            }
        )

        for tool_call in assistant_message.tool_calls:
            tool_result = run_tool(
                tool_call.function.name,
                tool_call.function.arguments,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )


def main():
    load_dotenv()

    api_key = os.getenv("OPENROUTER_API_KEY")
    model_name = os.getenv("MODEL_NAME", "deepseek/deepseek-chat")

    if not api_key:
        raise ValueError("Missing OPENROUTER_API_KEY in your .env file.")

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful local coding agent. "
                "Use the most specific tool for the job. "
                "Prefer list_files, glob_files, read_file, search_files, write_file, and apply_patch for normal file work. "
                "Terminal file commands are allowed, but prefer the specific file tools when they fit. "
                "When the user asks you to inspect, create, edit, or run something on the computer, use tools instead of only explaining."
            ),
        }
    ]

    print("Simple Agent Chat")
    print("Type 'exit' to quit.")
    print()

    while True:
        user_input = input("You: ")

        if user_input.lower() == "exit":
            print("Goodbye.")
            break

        messages.append(
            {
                "role": "user",
                "content": user_input,
            }
        )

        run_agent_loop(client, model_name, messages)


if __name__ == "__main__":
    main()
