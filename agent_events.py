import json
from safety import decide_command


from config import TOOLS
from llm import complete
from agent import (
    run_tool,
    get_tool_call_name,
    get_tool_call_arguments,
    get_tool_call_id,
    assistant_record_for_history,
)

def run_agent_events(client, model_name, messages, max_agent_steps, approval_mode, intent=''):
    for step_number in range(1, max_agent_steps +1):
        assistant_record = complete(
            client, model_name, messages, stream_messages=False, tools=TOOLS
        )

        content = assistant_record["content"]
        tool_calls = assistant_record["tool_calls"] or []

        if content:
            yield {"type": "assistant_message", "content": content}
        
        if not tool_calls:
            messages.append({"role": "assistant", "content": content})
            yield {"type": "done"}
            return

        messages.append(assistant_record_for_history(assistant_record))

        denied = False

        for tool_call in tool_calls:
            name = get_tool_call_name(tool_call)
            args = get_tool_call_arguments(tool_call)

            yield {"type": "tool_start", "name": name, "args": args}

            run_mode = approval_mode

            if name == "execute_terminal_command":
                try: 
                    command = json.loads(args).get("command", "")
                except (json.JSONDecodeError, AttributeError):
                    command = ""
                
                decision = decide_command(command, approval_mode, intent=intent)

                if decision.action == "approval_required":
                    answer = yield {
                        "type": "approval_request",
                        "command": command,
                        "reason": decision.reason
                    }

                    if answer != "approve":
                        result = "CANCELLED: User did not approve the command."
                        yield {"type": "tool_result", "name": name, "args": args, "result": result}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": get_tool_call_id(tool_call), 
                            "content": result, 
                        })
                        denied = True
                        continue

                    run_mode = "full_auto"    

            result = run_tool(name, args, False, run_mode)

            yield {"type": "tool_result", "name": name, "args": args, "result": result}

            messages.append({
                "role": "tool",
                "tool_call_id": get_tool_call_id(tool_call),
                "content": result,
            })

        if denied:
            messages.append({
                "role": "assistant",
                "content": "Stopped: you denied the command, so I will not continue this turn.",
            })
            yield {"type": "done"}
            return

    
