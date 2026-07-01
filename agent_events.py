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

def run_agent_events(client, model_name, messages, max_agent_steps,
                     approval_mode, intent='', on_assistant_chunk=None,
                     on_reasoning_chunk=None):
    for step_number in range(1, max_agent_steps +1):
        if on_assistant_chunk is not None or on_reasoning_chunk is not None:
            # Streaming path: accumulate deltas so the callbacks receive the
            # full text so far (not raw deltas), matching opencode's behavior.
            _acc_content = []
            _acc_reasoning = []

            def _chunk_cb(piece):
                _acc_content.append(piece)
                if on_assistant_chunk:
                    on_assistant_chunk("".join(_acc_content))

            def _reasoning_cb(piece):
                _acc_reasoning.append(piece)
                if on_reasoning_chunk:
                    on_reasoning_chunk("".join(_acc_reasoning))

            assistant_record = complete(
                client, model_name, messages, stream_messages=True,
                tools=TOOLS, on_chunk=_chunk_cb, on_reasoning=_reasoning_cb,
            )
        else:
            assistant_record = complete(
                client, model_name, messages, stream_messages=False, tools=TOOLS
            )

        content = assistant_record["content"]
        tool_calls = assistant_record["tool_calls"] or []
        reasoning = assistant_record.get("reasoning")

        if reasoning:
            # Streaming: incremental chunks were already pushed via the callback.
            # Non-streaming: no chunks were pushed, so this is the only event.
            # Either way, yield one final streaming=False marker so the UI can
            # stop the shimmer. (Yield exactly once — never double-yield.)
            yield {"type": "reasoning", "content": reasoning, "streaming": False}

        if content:
            if on_assistant_chunk is None:
                yield {"type": "assistant_message", "content": content}
            else:
                yield {
                    "type": "assistant_message",
                    "content": content,
                    "streaming": False,
                }
        
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

            if name == "run_command":
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

            if name == "ask_question":
                try:
                    parsed = json.loads(args)
                    q_text = parsed.get("question", "")
                    q_opts = parsed.get("options", [])
                except (json.JSONDecodeError, AttributeError):
                    q_text = ""
                    q_opts = []

                answer = yield {
                    "type": "question_request",
                    "question": q_text,
                    "options": q_opts,
                }

                result = f"User chose: {answer}" if answer else "User did not answer."
                yield {"type": "tool_result", "name": name, "args": args, "result": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": get_tool_call_id(tool_call),
                    "content": result,
                })
                continue

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

    
