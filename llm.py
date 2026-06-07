"""Talking to the language model: streaming, plain responses, and one entry point."""


def collect_streamed_assistant_message(stream) -> dict:
    content_parts = []
    tool_calls_by_index = {}

    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []

        if not choices:
            continue

        delta = choices[0].delta
        content_piece = getattr(delta, "content", None)

        if content_piece:
            content_parts.append(content_piece)

        for tool_call_delta in getattr(delta, "tool_calls", None) or []:
            index = tool_call_delta.index
            tool_call = tool_calls_by_index.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                },
            )

            call_id = getattr(tool_call_delta, "id", None)
            call_type = getattr(tool_call_delta, "type", None)
            function_delta = getattr(tool_call_delta, "function", None)

            if call_id:
                tool_call["id"] = call_id
            if call_type:
                tool_call["type"] = call_type
            if function_delta:
                function_name = getattr(function_delta, "name", None)
                function_arguments = getattr(function_delta, "arguments", None)

                if function_name:
                    tool_call["function"]["name"] += function_name
                if function_arguments:
                    tool_call["function"]["arguments"] += function_arguments

    tool_calls = [
        tool_calls_by_index[index]
        for index in sorted(tool_calls_by_index)
    ]

    return {
        "role": "assistant",
        "content": "".join(content_parts),
        "tool_calls": tool_calls or None,
    }


def normal_assistant_message_to_dict(assistant_message) -> dict:
    tool_calls = None
    raw_tool_calls = getattr(assistant_message, "tool_calls", None)

    if raw_tool_calls:
        tool_calls = [tool_call.model_dump() for tool_call in raw_tool_calls]

    return {
        "role": "assistant",
        "content": assistant_message.content or "",
        "tool_calls": tool_calls,
    }


def complete(client, model_name, messages, stream_messages, tools=None) -> dict:
    # One place to call the LLM. Returns the same assistant record whether or not
    # we stream, and only advertises tools when the caller actually wants them.
    request = {"model": model_name, "messages": messages}

    if tools is not None:
        request["tools"] = tools
        request["tool_choice"] = "auto"

    if stream_messages:
        stream = client.chat.completions.create(**request, stream=True)
        return collect_streamed_assistant_message(stream)

    response = client.chat.completions.create(**request)
    return normal_assistant_message_to_dict(response.choices[0].message)
