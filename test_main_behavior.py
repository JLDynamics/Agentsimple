import json
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_collect_streamed_assistant_message_collects_content(self):
        message = main.collect_streamed_assistant_message(
            [
                make_stream_chunk(content="Hello"),
                make_stream_chunk(content=" world"),
            ]
        )

        self.assertEqual(message["content"], "Hello world")
        self.assertIsNone(message["tool_calls"])

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

    def test_rewind_conversation_drops_last_turns(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]

        with redirect_stdout(io.StringIO()):
            removed = main.rewind_conversation(messages, 2)

        self.assertEqual(removed, 2)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[-1]["content"], "a1")
        self.assertEqual(
            [m["content"] for m in messages if m["role"] == "user"], ["u1"]
        )

    def test_rewind_conversation_caps_at_system_message(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]

        with redirect_stdout(io.StringIO()):
            removed = main.rewind_conversation(messages, 5)

        self.assertEqual(removed, 1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")

    def test_show_memory_displays_both_memories(self):
        with patch("main.read_global_memory", return_value="- likes simple code"), \
                patch("main.read_project_memory", return_value="- uses uv and unittest"):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                main.show_memory()

        output = buffer.getvalue()
        self.assertIn("Global memory", output)
        self.assertIn("likes simple code", output)
        self.assertIn("Project memory", output)
        self.assertIn("uses uv and unittest", output)

    def test_show_skills_lists_index(self):
        with patch("main.list_skills_index", return_value="- demo [project]: a demo skill"):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                main.show_skills()

        output = buffer.getvalue()
        self.assertIn("Saved skills", output)
        self.assertIn("demo", output)

    def test_print_agent_markdown_renders_content(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main.print_agent_markdown("Hello **world**")

        output = buffer.getvalue()
        self.assertIn("Hello", output)
        self.assertIn("world", output)

    def test_delete_session_file_removes_file(self):
        from pathlib import Path

        sessions_dir = Path("tmp_sessions_delete")
        sessions_dir.mkdir(exist_ok=True)
        session_file = sessions_dir / "session-x.json"
        session_file.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: sessions_dir.exists() and sessions_dir.rmdir())
        self.addCleanup(lambda: session_file.exists() and session_file.unlink())

        with patch.object(main, "SESSIONS_DIR", sessions_dir):
            deleted = main.delete_session_file("session-x")
            missing = main.delete_session_file("session-does-not-exist")

        self.assertTrue(deleted)
        self.assertFalse(missing)
        self.assertFalse(session_file.exists())

    def test_format_relative_time_reads_recent_and_old(self):
        from datetime import datetime, timedelta

        recent = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertEqual(main.format_relative_time(recent), "5 minutes ago")

        hours = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        self.assertEqual(main.format_relative_time(hours), "2 hours ago")

        self.assertEqual(main.format_relative_time("unknown"), "unknown")

    def test_session_preview_returns_first_user_message(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "help me add a web_fetch tool"},
            {"role": "assistant", "content": "sure"},
        ]

        self.assertEqual(
            main.session_preview(messages), "help me add a web_fetch tool"
        )
        self.assertEqual(main.session_preview([{"role": "system", "content": "x"}]), "")

    def test_rename_session_sets_display_name(self):
        from pathlib import Path

        sessions_dir = Path("tmp_sessions_rename")
        sessions_dir.mkdir(exist_ok=True)
        session_file = sessions_dir / "session-x.json"
        session_file.write_text('{"name": "session-x", "messages": []}', encoding="utf-8")
        self.addCleanup(lambda: sessions_dir.exists() and sessions_dir.rmdir())
        self.addCleanup(lambda: session_file.exists() and session_file.unlink())

        with patch.object(main, "SESSIONS_DIR", sessions_dir):
            ok = main.rename_session("session-x", "auth-refactor")
            data = json.loads(session_file.read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(data["display_name"], "auth-refactor")

    def test_export_session_markdown_writes_transcript(self):
        from pathlib import Path

        exports_dir = Path("tmp_exports")
        sessions_dir = Path("tmp_sessions_export")
        self.addCleanup(lambda: __import__("shutil").rmtree(exports_dir, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(sessions_dir, ignore_errors=True))

        sessions_dir.mkdir(exist_ok=True)
        session_data = {
            "name": "session-x",
            "display_name": "my-feature",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "add a feature"},
                {"role": "assistant", "content": "Done, I added it."},
            ],
        }
        (sessions_dir / "session-x.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )

        with patch.object(main, "EXPORTS_DIR", exports_dir), \
                patch.object(main, "SESSIONS_DIR", sessions_dir):
            export_path = main.export_session_markdown("session-x")
            text = export_path.read_text(encoding="utf-8")

        self.assertIn("my-feature", text)
        self.assertIn("## You", text)
        self.assertIn("add a feature", text)
        self.assertIn("## Agent", text)
        self.assertIn("Done, I added it.", text)
        self.assertNotIn("system prompt", text)

    def test_pick_session_parses_number(self):
        sessions = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

        self.assertEqual(main.pick_session(sessions, "2"), {"name": "b"})
        self.assertIsNone(main.pick_session(sessions, "9"))
        self.assertIsNone(main.pick_session(sessions, "x"))

    def test_list_saved_sessions_orders_newest_first(self):
        from pathlib import Path

        sessions_dir = Path("tmp_sessions_order")
        self.addCleanup(lambda: __import__("shutil").rmtree(sessions_dir, ignore_errors=True))
        sessions_dir.mkdir(exist_ok=True)

        old_session = {
            "name": "session-a",
            "updated_at": "2026-01-01T10:00:00",
            "messages": [{"role": "user", "content": "old"}],
        }
        new_session = {
            "name": "session-z",
            "updated_at": "2026-01-02T10:00:00",
            "messages": [{"role": "user", "content": "new"}],
        }

        (sessions_dir / "session-a.json").write_text(
            json.dumps(old_session), encoding="utf-8"
        )
        (sessions_dir / "session-z.json").write_text(
            json.dumps(new_session), encoding="utf-8"
        )

        with patch.object(main, "SESSIONS_DIR", sessions_dir):
            sessions = main.list_saved_sessions()

        self.assertEqual([session["name"] for session in sessions], ["session-z", "session-a"])

    def test_continue_most_recent_loads_newest_session(self):
        system_message = {"role": "system", "content": "new system"}

        with patch("main.most_recent_session_name", return_value="session-new"), \
                patch("main.load_session", return_value=[
                    {"role": "system", "content": "old system"},
                    {"role": "user", "content": "hello"},
                ]):
            with redirect_stdout(io.StringIO()):
                session_name, messages = main.continue_most_recent(system_message)

        self.assertEqual(session_name, "session-new")
        self.assertEqual(messages[0], system_message)
        self.assertEqual(messages[1]["content"], "hello")

    def test_resume_session_picker_can_start_new(self):
        system_message = {"role": "system", "content": "system"}

        with patch("main.list_saved_sessions", return_value=[
            {
                "name": "session-1",
                "display_name": "feature-work",
                "updated_at": "2026-01-01T10:00:00",
                "message_count": 2,
                "preview": "hello",
            }
        ]), patch("builtins.input", return_value="new"), \
                patch("main.create_session_name", return_value="session-new"):
            with redirect_stdout(io.StringIO()):
                session_name, messages = main.resume_session_picker(system_message)

        self.assertEqual(session_name, "session-new")
        self.assertEqual(messages, [system_message])


if __name__ == "__main__":
    unittest.main()
