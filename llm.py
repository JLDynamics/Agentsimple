"""Talking to the language model: streaming, plain responses, and one entry point."""

import time
import random


# The current reasoning effort ("low"/"medium"/"high"), or None to send nothing.
# Set from the UI (Settings). Only added to the request when a level is chosen,
# so the default behaviour is unchanged for providers that don't support it.
_reasoning_effort = None


def set_reasoning_effort(level):
    """Choose the reasoning effort sent with each request (None disables it)."""
    global _reasoning_effort
    _reasoning_effort = level if level in ("low", "medium", "high") else None


def _api_call_with_retry(client, request, stream=False, max_retries=3):
    """Call the API with exponential backoff on failure."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**request, stream=stream)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = (2 ** attempt) + random.random()
                print(f"⚠️ API error: {e}. Retrying in {wait:.1f}s "
                      f"(attempt {attempt + 2}/{max_retries})...")
                time.sleep(wait)
    raise last_error


def collect_streamed_assistant_message(stream, on_chunk=None, on_reasoning=None) -> dict:
    content_parts = []
    tool_calls_by_index = {}
    reasoning_parts = []

    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []

        if not choices:
            continue

        delta = choices[0].delta
        content_piece = getattr(delta, "content", None)
        reasoning_piece = getattr(delta, "reasoning_content", None)

        if reasoning_piece:
            reasoning_parts.append(reasoning_piece)
            if on_reasoning:
                on_reasoning(reasoning_piece)

        if content_piece:
            content_parts.append(content_piece)
            if on_chunk:
                on_chunk(content_piece)

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
        "reasoning": "".join(reasoning_parts) or None,
    }


def normal_assistant_message_to_dict(assistant_message) -> dict:
    tool_calls = None
    raw_tool_calls = getattr(assistant_message, "tool_calls", None)

    if raw_tool_calls:
        tool_calls = [tool_call.model_dump() for tool_call in raw_tool_calls]

    reasoning = getattr(assistant_message, "reasoning_content", None)
    if reasoning is None:
        # Some SDK versions put unknown fields in model_extra
        extra = getattr(assistant_message, "model_extra", None) or {}
        reasoning = extra.get("reasoning_content")

    return {
        "role": "assistant",
        "content": assistant_message.content or "",
        "tool_calls": tool_calls,
        "reasoning": reasoning,
    }


def complete(client, model_name, messages, stream_messages, tools=None,
             on_chunk=None, on_reasoning=None) -> dict:
    request = {"model": model_name, "messages": messages}

    if tools is not None:
        request["tools"] = tools
        request["tool_choice"] = "auto"

    if _reasoning_effort:
        request["reasoning_effort"] = _reasoning_effort

    if stream_messages:
        stream = _api_call_with_retry(client, request, stream=True)
        return collect_streamed_assistant_message(
            stream, on_chunk=on_chunk, on_reasoning=on_reasoning,
        )

    response = _api_call_with_retry(client, request, stream=False)
    return normal_assistant_message_to_dict(response.choices[0].message)
