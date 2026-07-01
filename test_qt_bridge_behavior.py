"""Tests for the Qt bridge logic and the streaming parameter on run_agent_events."""
import json
import unittest
from unittest.mock import MagicMock, patch

from agent_events import run_agent_events


class StreamingParameterTests(unittest.TestCase):
    def test_signature_has_optional_callbacks(self):
        """run_agent_events must accept on_assistant_chunk and on_reasoning_chunk,
        both defaulting to None so existing callers are unaffected."""
        import inspect

        sig = inspect.signature(run_agent_events)
        params = sig.parameters
        self.assertIn("on_assistant_chunk", params)
        self.assertIn("on_reasoning_chunk", params)
        self.assertIsNone(params["on_assistant_chunk"].default)
        self.assertIsNone(params["on_reasoning_chunk"].default)

    def test_no_callbacks_uses_non_streaming_complete(self):
        """With callbacks=None, complete() is called with stream_messages=False
        (the existing behavior)."""
        client = MagicMock()
        with patch("agent_events.complete") as mock_complete:
            mock_complete.return_value = {
                "role": "assistant",
                "content": "hi",
                "tool_calls": None,
                "reasoning": None,
            }
            gen = run_agent_events(
                client, "m", [{"role": "user", "content": "hi"}],
                max_agent_steps=1, approval_mode="safe_auto",
            )
            list(gen)  # drain
            _, kwargs = mock_complete.call_args
            self.assertEqual(kwargs.get("stream_messages"), False)

    def test_callbacks_enable_streaming_and_accumulate(self):
        """When on_assistant_chunk is provided, complete() is called with
        stream_messages=True, on_chunk receives accumulated text, and the
        callback is invoked with the accumulated string (not the raw delta)."""
        client = MagicMock()
        chunks_received = []

        def fake_complete(client, model_name, messages, stream_messages,
                          tools=None, on_chunk=None, on_reasoning=None):
            self.assertTrue(stream_messages)
            # Simulate the stream emitting two deltas
            on_chunk("Hel")
            on_chunk("lo")
            if on_reasoning:
                on_reasoning("thinking...")
            return {
                "role": "assistant",
                "content": "Hello",
                "tool_calls": None,
                "reasoning": "thinking...",
            }

        def on_assistant_chunk(accumulated):
            chunks_received.append(accumulated)

        with patch("agent_events.complete", side_effect=fake_complete):
            gen = run_agent_events(
                client, "m", [{"role": "user", "content": "hi"}],
                max_agent_steps=1, approval_mode="safe_auto",
                on_assistant_chunk=on_assistant_chunk,
            )
            events = list(gen)

        # The callback must have been called with accumulated text, not raw deltas.
        self.assertEqual(chunks_received, ["Hel", "Hello"])
        # The generator must still yield the final assistant_message with streaming=False.
        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        self.assertTrue(any(not e.get("streaming", False) for e in assistant_events))


if __name__ == "__main__":
    unittest.main()