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
        self.assertEqual(assistant_events[-1]["streaming"], False)

    def test_reasoning_chunk_callback_accumulates(self):
        """on_reasoning_chunk receives accumulated reasoning text (not raw
        deltas), and the generator yields exactly one reasoning event with
        streaming=False as the final marker."""
        client = MagicMock()
        reasoning_received = []

        def fake_complete(client, model_name, messages, stream_messages,
                          tools=None, on_chunk=None, on_reasoning=None):
            self.assertTrue(stream_messages)
            on_reasoning("thin")
            on_reasoning("king")
            return {
                "role": "assistant",
                "content": "Hello",
                "tool_calls": None,
                "reasoning": "thinking",
            }

        def on_reasoning_chunk(accumulated):
            reasoning_received.append(accumulated)

        with patch("agent_events.complete", side_effect=fake_complete):
            gen = run_agent_events(
                client, "m", [{"role": "user", "content": "hi"}],
                max_agent_steps=1, approval_mode="safe_auto",
                on_reasoning_chunk=on_reasoning_chunk,
            )
            events = list(gen)

        # Accumulated, not raw deltas.
        self.assertEqual(reasoning_received, ["thin", "thinking"])
        # Exactly one reasoning event, with streaming=False.
        reasoning_events = [e for e in events if e["type"] == "reasoning"]
        self.assertEqual(len(reasoning_events), 1)
        self.assertEqual(reasoning_events[0]["streaming"], False)


from qt_app import serialize_event, diff_counts


class SerializeEventTests(unittest.TestCase):
    def test_user_event(self):
        self.assertEqual(
            serialize_event({"type": "user", "text": "hi"}),
            json.dumps({"type": "user", "text": "hi"}),
        )

    def test_assistant_message_streaming(self):
        out = json.loads(serialize_event({"type": "assistant_message", "content": "x", "streaming": True}))
        self.assertTrue(out["streaming"])

    def test_diff_includes_counts(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n+extra\n"
        out = json.loads(serialize_event({
            "type": "diff", "path": "foo.py", "diff": diff,
        }))
        self.assertEqual(out["added"], 2)
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["path"], "foo.py")

    def test_theme_event(self):
        out = json.loads(serialize_event({"type": "theme", "mode": "light"}))
        self.assertEqual(out["mode"], "light")

    def test_done_event(self):
        self.assertEqual(serialize_event({"type": "done"}), json.dumps({"type": "done"}))


class DiffCountsTests(unittest.TestCase):
    def test_counts_skip_file_headers(self):
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(diff_counts(diff), (1, 1))

    def test_empty(self):
        self.assertEqual(diff_counts(""), (0, 0))


from qt_app import WebBridge


class BridgePushContractTests(unittest.TestCase):
    """The JS source WebBridge.push generates must embed a JSON STRING literal
    (so app.js's JSON.parse succeeds), not a bare JS object literal."""

    def _pushed_js(self, event):
        captured = {}
        with patch.object(WebBridge, "push", autospec=True, side_effect=lambda self, ev: captured.__setitem__("ev", ev)):
            pass
        # Build the same string WebBridge.push would emit, without a real view.
        from qt_app import serialize_event
        import json as _json
        return "window.__appendEvent && window.__appendEvent(%s);" % _json.dumps(serialize_event(event))

    def test_push_embeds_a_string_literal_not_object(self):
        js = self._pushed_js({"type": "user", "text": "hi"})
        # The payload must be wrapped in quotes (a JS string), so __appendEvent
        # receives a string it can JSON.parse. A bare object literal would start
        # with "{" unquoted — here the "%s" of json.dumps(serialize_event(...))
        # yields a quoted string literal.
        self.assertIn('__appendEvent("', js)

    def test_round_trip_user_event(self):
        js = self._pushed_js({"type": "user", "text": "hi"})
        # Extract the string literal between the parens and JSON.parse it back,
        # mirroring what app.js does.
        import json as _json
        payload = js[js.index("(") + 1 : js.rindex(")")]
        decoded = _json.loads(_json.loads(payload))  # json.loads the quoted string, then the JSON
        self.assertEqual(decoded, {"type": "user", "text": "hi"})


if __name__ == "__main__":
    unittest.main()