import json
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import main


def make_tool_call(name: str, arguments: dict):
    return SimpleNamespace(
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments),
        )
    )


def make_stream_chunk(content=None, tool_call=None):
    delta = SimpleNamespace(content=content)

    if tool_call is None:
        delta.tool_calls = None
    else:
        delta.tool_calls = [tool_call]

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=delta,
            )
        ]
    )


def make_tool_delta(index: int, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function" if call_id else None,
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


class MainBehaviorTests(unittest.TestCase):
    def test_default_config_enables_streaming_messages(self):
        self.assertTrue(main.DEFAULT_CONFIG["stream_messages"])

    def test_default_config_uses_summary_tool_display(self):
        self.assertEqual(main.DEFAULT_CONFIG["tool_display"], "summary")

    def test_default_config_uses_safe_auto_approval_mode(self):
        self.assertEqual(main.DEFAULT_CONFIG["approval_mode"], "safe_auto")

    def test_read_file_range_tool_is_registered(self):
        self.assertIn("read_file_range", main.AVAILABLE_TOOL)

    def test_direct_coding_tools_are_registered(self):
        expected_tools = [
            "get_file_info",
            "read_many_files",
            "list_project_tree",
            "run_python_tests",
            "compile_python",
            "git_status",
            "git_diff",
        ]

        for tool_name in expected_tools:
            self.assertIn(tool_name, main.AVAILABLE_TOOL)

    def test_run_tool_passes_approval_mode_to_terminal_tool(self):
        original_tool = main.AVAILABLE_TOOL["execute_terminal_command"]
        calls = {}

        def fake_execute_terminal_command(command, approval_mode="safe_auto"):
            calls["command"] = command
            calls["approval_mode"] = approval_mode
            return "SUCCESS"

        try:
            main.AVAILABLE_TOOL["execute_terminal_command"] = fake_execute_terminal_command

            result = main.run_tool(
                "execute_terminal_command",
                json.dumps({"command": "python --version"}),
                approval_mode="ask",
            )

        finally:
            main.AVAILABLE_TOOL["execute_terminal_command"] = original_tool

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(calls["command"], "python --version")
        self.assertEqual(calls["approval_mode"], "ask")

    def test_system_prompt_asks_for_natural_plan(self):
        system_prompt = main.build_system_prompt().lower()

        self.assertIn("plan", system_prompt)
        self.assertIn("natural", system_prompt)
        self.assertIn("root cause", system_prompt)
        self.assertIn("do not reveal hidden chain-of-thought", system_prompt)

    def test_describe_tool_calls_mentions_files_and_folders(self):
        note = main.describe_tool_calls(
            [
                make_tool_call("list_files", {"path": "."}),
                make_tool_call("read_file", {"path": "main.py"}),
                make_tool_call("read_file", {"path": "tools.py"}),
            ]
        )

        self.assertIn("look at folders: .", note)
        self.assertIn("read files: main.py, tools.py", note)

    def test_describe_tool_calls_mentions_file_ranges(self):
        note = main.describe_tool_calls(
            [
                make_tool_call(
                    "read_file_range",
                    {
                        "path": "main.py",
                        "start_line": 200,
                        "end_line": 300,
                    },
                ),
            ]
        )

        self.assertIn("read files: main.py lines 200-300", note)

    def test_describe_tool_calls_mentions_direct_coding_tools(self):
        note = main.describe_tool_calls(
            [
                make_tool_call("list_project_tree", {"path": ".", "max_depth": 2}),
                make_tool_call("read_many_files", {"paths": ["main.py", "tools.py"]}),
                make_tool_call("run_python_tests", {"test_path": "test_main_behavior.py"}),
                make_tool_call("git_status", {}),
            ]
        )

        self.assertIn("look at folders: . tree", note)
        self.assertIn("read files: main.py, tools.py", note)
        self.assertIn("check python tests test_main_behavior.py, git status", note)

    def test_print_tool_activity_status_shows_working_summary(self):
        output = io.StringIO()

        with redirect_stdout(output):
            main.print_tool_activity_status(
                [
                    make_tool_call("list_files", {"path": "."}),
                    make_tool_call("read_file", {"path": "main.py"}),
                ]
            )

        text = output.getvalue()
        self.assertIn("Working:", text)
        self.assertIn("look at folders: .", text)
        self.assertIn("read files: main.py", text)

    def test_collect_streamed_assistant_message_prints_content(self):
        output = io.StringIO()

        with redirect_stdout(output):
            message = main.collect_streamed_assistant_message(
                [
                    make_stream_chunk(content="Hello"),
                    make_stream_chunk(content=" world"),
                ]
            )

        self.assertEqual(message["content"], "Hello world")
        self.assertIsNone(message["tool_calls"])
        self.assertIn("Agent: Hello world", output.getvalue())

    def test_collect_streamed_assistant_message_assembles_tool_calls(self):
        message = main.collect_streamed_assistant_message(
            [
                make_stream_chunk(
                    tool_call=make_tool_delta(
                        0,
                        call_id="call_1",
                        name="read_file",
                        arguments="",
                    )
                ),
                make_stream_chunk(
                    tool_call=make_tool_delta(
                        0,
                        arguments='{"path": ',
                    )
                ),
                make_stream_chunk(
                    tool_call=make_tool_delta(
                        0,
                        arguments='"main.py"}',
                    )
                ),
            ]
        )

        self.assertEqual(message["content"], "")
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(
            message["tool_calls"][0]["function"]["arguments"],
            '{"path": "main.py"}',
        )


if __name__ == "__main__":
    unittest.main()
