import json
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import agent
import config
import llm
import prompt
import sessions
import tools
import ui


def make_tool_call(name: str, arguments: dict):
    return {
        "id": "call_test",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


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
    def test_wheel_include_list_contains_runtime_files(self):
        from pathlib import Path

        text = Path("pyproject.toml").read_text(encoding="utf-8")
        required_files = [
            "main.py",
            "agent.py",
            "config.py",
            "llm.py",
            "prompt.py",
            "sessions.py",
            "ui.py",
            "tools.py",
            "safety.py",
            "tools_schema.json",
        ]

        for file_name in required_files:
            self.assertIn(file_name, text)

    def test_default_config_enables_streaming_messages(self):
        self.assertTrue(config.DEFAULT_CONFIG["stream_messages"])

    def test_default_config_skips_planning_for_efficiency(self):
        self.assertFalse(config.DEFAULT_CONFIG["plan_before_tools"])

    def test_default_config_skips_tool_result_summary_for_efficiency(self):
        self.assertFalse(config.DEFAULT_CONFIG["summarize_tool_results"])

    def test_default_config_uses_summary_tool_display(self):
        self.assertEqual(config.DEFAULT_CONFIG["tool_display"], "summary")

    def test_default_config_uses_safe_auto_approval_mode(self):
        self.assertEqual(config.DEFAULT_CONFIG["approval_mode"], "safe_auto")

    def test_context_health_warning_triggers_over_threshold(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 320},
        ]
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 100
        runtime_config["context_warning_percent"] = 70

        warning = config.context_health_warning(messages, runtime_config)

        self.assertIn("Context is about", warning)
        self.assertIn("/compact", warning)

    def test_context_health_warning_stays_quiet_below_threshold(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "short"},
        ]
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 1000
        runtime_config["context_warning_percent"] = 70

        self.assertEqual(config.context_health_warning(messages, runtime_config), "")

    def test_show_context_warning_prints_only_when_needed(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 100
        runtime_config["context_warning_percent"] = 70
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 320},
        ]

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ui.show_context_warning(messages, runtime_config)

        output = buffer.getvalue()
        self.assertIn("Context is about", output)
        self.assertIn("/compact", output)

    def test_show_context_warning_stays_quiet_below_threshold(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 1000
        runtime_config["context_warning_percent"] = 70
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "short"},
        ]

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ui.show_context_warning(messages, runtime_config)

        self.assertEqual(buffer.getvalue(), "")

    def test_save_config_writes_merged_json(self):
        from pathlib import Path

        config_path = Path("tmp_agent_config_save.json")
        self.addCleanup(lambda: config_path.exists() and config_path.unlink())

        with patch.object(config, "CONFIG_PATH", config_path):
            config.save_config(
                {
                    "approval_mode": "full_auto",
                    "custom_setting": "kept",
                }
            )

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["approval_mode"], "full_auto")
        self.assertEqual(saved["custom_setting"], "kept")
        self.assertEqual(saved["model"], config.DEFAULT_CONFIG["model"])

    def test_save_config_writes_pretty_json_with_trailing_newline(self):
        from pathlib import Path

        config_path = Path("tmp_agent_config_format.json")
        self.addCleanup(lambda: config_path.exists() and config_path.unlink())

        with patch.object(config, "CONFIG_PATH", config_path):
            config.save_config({"approval_mode": "full_auto"})

        text = config_path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertIn('\n    "approval_mode": "full_auto"', text)

    def test_choose_mode_persists_selected_mode(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["approval_mode"] = "ask"

        with patch("builtins.input", return_value="2"), \
                patch("ui.save_config") as fake_save:
            with redirect_stdout(io.StringIO()):
                ui.choose_mode(runtime_config)

        self.assertEqual(runtime_config["approval_mode"], "safe_auto")
        fake_save.assert_called_once_with(runtime_config)

    def test_choose_mode_handles_save_failure_without_crashing(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["approval_mode"] = "ask"

        with patch("builtins.input", return_value="3"), \
                patch("ui.save_config", side_effect=OSError("disk full")):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                ui.choose_mode(runtime_config)

        output = buffer.getvalue()
        self.assertEqual(runtime_config["approval_mode"], "full_auto")
        self.assertIn("Mode changed for this run only: full_auto", output)
        self.assertIn("Could not save agent_config.json: disk full", output)

    def test_read_file_range_tool_is_registered(self):
        self.assertIn("read_file_range", config.AVAILABLE_TOOL)

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
            self.assertIn(tool_name, config.AVAILABLE_TOOL)

    def test_skill_tools_are_registered(self):
        expected_tools = [
            "list_skills",
            "read_skill",
            "save_skill",
            "delete_skill",
        ]

        for tool_name in expected_tools:
            self.assertIn(tool_name, config.AVAILABLE_TOOL)

    def test_run_tool_passes_approval_mode_to_terminal_tool(self):
        original_tool = config.AVAILABLE_TOOL["execute_terminal_command"]
        calls = {}

        def fake_execute_terminal_command(command, approval_mode="safe_auto"):
            calls["command"] = command
            calls["approval_mode"] = approval_mode
            return "SUCCESS"

        try:
            config.AVAILABLE_TOOL["execute_terminal_command"] = fake_execute_terminal_command

            result = agent.run_tool(
                "execute_terminal_command",
                json.dumps({"command": "python --version"}),
                approval_mode="ask",
            )

        finally:
            config.AVAILABLE_TOOL["execute_terminal_command"] = original_tool

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(calls["command"], "python --version")
        self.assertEqual(calls["approval_mode"], "ask")

    def test_system_prompt_asks_for_natural_plan(self):
        system_prompt = prompt.build_system_prompt().lower()

        self.assertIn("plan", system_prompt)
        self.assertIn("natural", system_prompt)
        self.assertIn("root cause", system_prompt)
        self.assertIn("do not reveal raw internal chain-of-thought", system_prompt)
        self.assertIn("workflow discipline", system_prompt)
        self.assertIn("read_skill", system_prompt)
        self.assertIn("list_skills", system_prompt)
        self.assertIn("debugging", system_prompt)
        self.assertIn("plan-a-feature", system_prompt)
        self.assertIn("running narration is required", system_prompt)

    def test_refresh_system_message_updates_current_skills(self):
        messages = [
            {"role": "system", "content": "old skills index"},
            {"role": "user", "content": "hello"},
        ]

        with patch(
            "prompt.build_system_content",
            return_value="new skills index with add-a-tool [project]",
        ):
            refreshed = prompt.refresh_system_message(messages)

        self.assertIs(refreshed, messages)
        self.assertEqual(
            messages[0]["content"],
            "new skills index with add-a-tool [project]",
        )
        self.assertEqual(messages[1]["content"], "hello")

    def test_is_skill_question_detects_saved_skill_questions(self):
        examples = [
            "what skills do you have?",
            "show skills",
            "list saved skills",
            "do you have a project skill",
            "is there a project skill",
        ]

        for text in examples:
            self.assertTrue(ui.is_skill_question(text), text)

    def test_is_skill_question_ignores_task_requests(self):
        examples = [
            "use the debugging skill to fix the test",
            "save this as a project skill",
            "read the add-a-tool skill",
        ]

        for text in examples:
            self.assertFalse(ui.is_skill_question(text), text)

    def test_describe_tool_calls_mentions_files_and_folders(self):
        note = agent.describe_tool_calls(
            [
                make_tool_call("list_files", {"path": "."}),
                make_tool_call("read_file", {"path": "main.py"}),
                make_tool_call("read_file", {"path": "tools.py"}),
            ]
        )

        self.assertIn("list_files: .", note)
        self.assertIn("read_file: main.py", note)
        self.assertIn("read_file: tools.py", note)

    def test_describe_tool_calls_mentions_file_ranges(self):
        note = agent.describe_tool_calls(
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

        self.assertIn("read_file_range: main.py", note)

    def test_describe_tool_calls_mentions_direct_coding_tools(self):
        note = agent.describe_tool_calls(
            [
                make_tool_call("list_project_tree", {"path": ".", "max_depth": 2}),
                make_tool_call("read_many_files", {"paths": ["main.py", "tools.py"]}),
                make_tool_call("run_python_tests", {"test_path": "test_main_behavior.py"}),
                make_tool_call("git_status", {}),
            ]
        )

        self.assertIn("list_project_tree: .", note)
        self.assertIn("read_many_files: main.py, tools.py", note)
        self.assertIn("run_python_tests: test_main_behavior.py", note)
        self.assertIn("git_status", note)

    def test_describe_tool_calls_mentions_skill_listing(self):
        note = agent.describe_tool_calls(
            [
                make_tool_call("list_skills", {}),
            ]
        )

        self.assertIn("list_skills", note)

    def test_print_tool_activity_status_shows_working_summary(self):
        output = io.StringIO()

        with redirect_stdout(output):
            agent.print_tool_activity_status(
                [
                    make_tool_call("list_files", {"path": "."}),
                    make_tool_call("read_file", {"path": "main.py"}),
                ]
            )

        text = output.getvalue()
        self.assertIn("Working:", text)
        self.assertIn("list_files: .", text)
        self.assertIn("read_file: main.py", text)

    def test_collect_streamed_assistant_message_collects_content(self):
        message = llm.collect_streamed_assistant_message(
            [
                make_stream_chunk(content="Hello"),
                make_stream_chunk(content=" world"),
            ]
        )

        self.assertEqual(message["content"], "Hello world")
        self.assertIsNone(message["tool_calls"])

    def test_collect_streamed_assistant_message_assembles_tool_calls(self):
        message = llm.collect_streamed_assistant_message(
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
            removed = ui.rewind_conversation(messages, 2)

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
            removed = ui.rewind_conversation(messages, 5)

        self.assertEqual(removed, 1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")

    def test_show_memory_displays_both_memories(self):
        with patch("tools.read_global_memory", return_value="- likes simple code"), \
                patch("tools.read_project_memory", return_value="- uses uv and unittest"):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                ui.show_memory()

        output = buffer.getvalue()
        self.assertIn("Global memory", output)
        self.assertIn("likes simple code", output)
        self.assertIn("Project memory", output)
        self.assertIn("uses uv and unittest", output)

    def test_show_skills_lists_index(self):
        with patch(
            "tools.list_skills",
            return_value="Saved skills:\n- demo [project]: a demo skill",
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                ui.show_skills()

        output = buffer.getvalue()
        self.assertIn("Saved skills", output)
        self.assertIn("demo", output)

    def test_print_agent_markdown_renders_content(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ui.print_agent_markdown("Hello **world**")

        output = buffer.getvalue()
        self.assertIn("Hello", output)
        self.assertIn("world", output)

    def test_normalize_turn_plan_skips_marker(self):
        self.assertEqual(agent.normalize_turn_plan("SKIP_PLAN"), "")
        self.assertEqual(agent.normalize_turn_plan("  skip_plan  "), "")
        self.assertEqual(agent.normalize_turn_plan("I will inspect the files."), "I will inspect the files.")

    def test_create_turn_plan_uses_no_tools(self):
        calls = {}

        def fake_create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="I will inspect the project structure first."
                        )
                    )
                ]
            )

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )

        with redirect_stdout(io.StringIO()):
            plan = agent.create_turn_plan(
                client,
                "test-model",
                [{"role": "user", "content": "explain this project"}],
                False,
            )

        self.assertEqual(plan, "I will inspect the project structure first.")
        self.assertNotIn("tools", calls)
        self.assertEqual(calls["model"], "test-model")
        self.assertEqual(calls["messages"][-1]["role"], "system")
        self.assertIn("Before tools are available", calls["messages"][-1]["content"])

    def test_maybe_create_turn_plan_appends_plan(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "explain the project"},
        ]

        with patch(
            "agent.create_turn_plan",
            return_value="I will inspect the structure, then summarize the workflow.",
        ):
            agent.maybe_create_turn_plan(
                None,
                "test-model",
                messages,
                {
                    "plan_before_tools": True,
                    "stream_messages": False,
                },
            )

        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(
            messages[-1]["content"],
            "I will inspect the structure, then summarize the workflow.",
        )

    def test_maybe_create_turn_plan_can_be_disabled(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "explain the project"},
        ]

        with patch("agent.create_turn_plan") as create_turn_plan:
            agent.maybe_create_turn_plan(
                None,
                "test-model",
                messages,
                {
                    "plan_before_tools": False,
                    "stream_messages": False,
                },
            )

        create_turn_plan.assert_not_called()
        self.assertEqual(messages[-1]["role"], "user")

    def test_create_tool_result_summary_uses_no_tools(self):
        calls = {}

        def fake_create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="I found that main.py contains the response loop."
                        )
                    )
                ]
            )

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )

        with redirect_stdout(io.StringIO()):
            summary = agent.create_tool_result_summary(
                client,
                "test-model",
                [
                    {"role": "user", "content": "check the loop"},
                    {"role": "tool", "tool_call_id": "call_1", "content": "SUCCESS"},
                ],
                False,
            )

        self.assertEqual(summary, "I found that main.py contains the response loop.")
        self.assertNotIn("tools", calls)
        self.assertEqual(calls["model"], "test-model")
        self.assertEqual(calls["messages"][-1]["role"], "system")
        self.assertIn("latest tool results", calls["messages"][-1]["content"])

    def test_run_agent_loop_adds_tool_result_summary_before_continuing(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "list files"},
        ]
        first_assistant_record = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "arguments": '{"path": "."}',
                    },
                }
            ],
        }
        final_assistant_record = {
            "role": "assistant",
            "content": "The project contains main.py.",
            "tool_calls": None,
        }

        with patch(
            "agent.create_assistant_message",
            side_effect=[first_assistant_record, final_assistant_record],
        ), patch("agent.run_tool", return_value="SUCCESS:\n[FILE] main.py"), patch(
            "agent.create_tool_result_summary",
            return_value="I found main.py in the project root.",
        ):
            with redirect_stdout(io.StringIO()):
                agent.run_agent_loop(
                    None,
                    "test-model",
                    messages,
                    3,
                    "summary",
                    False,
                    "safe_auto",
                    True,
                )

        assistant_messages = [
            message["content"]
            for message in messages
            if message["role"] == "assistant"
        ]

        self.assertIn("I found main.py in the project root.", assistant_messages)
        self.assertEqual(messages[-1]["content"], "The project contains main.py.")

    def test_delete_session_file_removes_file(self):
        from pathlib import Path

        sessions_dir = Path("tmp_sessions_delete")
        sessions_dir.mkdir(exist_ok=True)
        session_file = sessions_dir / "session-x.json"
        session_file.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: sessions_dir.exists() and sessions_dir.rmdir())
        self.addCleanup(lambda: session_file.exists() and session_file.unlink())

        with patch.object(sessions, "SESSIONS_DIR", sessions_dir):
            deleted = sessions.delete_session_file("session-x")
            missing = sessions.delete_session_file("session-does-not-exist")

        self.assertTrue(deleted)
        self.assertFalse(missing)
        self.assertFalse(session_file.exists())

    def test_format_relative_time_reads_recent_and_old(self):
        from datetime import datetime, timedelta

        recent = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertEqual(sessions.format_relative_time(recent), "5 minutes ago")

        hours = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        self.assertEqual(sessions.format_relative_time(hours), "2 hours ago")

        self.assertEqual(sessions.format_relative_time("unknown"), "unknown")

    def test_session_preview_returns_first_user_message(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "help me add a web_fetch tool"},
            {"role": "assistant", "content": "sure"},
        ]

        self.assertEqual(
            sessions.session_preview(messages), "help me add a web_fetch tool"
        )
        self.assertEqual(sessions.session_preview([{"role": "system", "content": "x"}]), "")

    def test_rename_session_sets_display_name(self):
        from pathlib import Path

        sessions_dir = Path("tmp_sessions_rename")
        sessions_dir.mkdir(exist_ok=True)
        session_file = sessions_dir / "session-x.json"
        session_file.write_text('{"name": "session-x", "messages": []}', encoding="utf-8")
        self.addCleanup(lambda: sessions_dir.exists() and sessions_dir.rmdir())
        self.addCleanup(lambda: session_file.exists() and session_file.unlink())

        with patch.object(sessions, "SESSIONS_DIR", sessions_dir):
            ok = sessions.rename_session("session-x", "auth-refactor")
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

        with patch.object(sessions, "EXPORTS_DIR", exports_dir), \
                patch.object(sessions, "SESSIONS_DIR", sessions_dir):
            export_path = sessions.export_session_markdown("session-x")
            text = export_path.read_text(encoding="utf-8")

        self.assertIn("my-feature", text)
        self.assertIn("## You", text)
        self.assertIn("add a feature", text)
        self.assertIn("## Agent", text)
        self.assertIn("Done, I added it.", text)
        self.assertNotIn("system prompt", text)

    def test_pick_session_parses_number(self):
        session_list = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

        self.assertEqual(sessions.pick_session(session_list, "2"), {"name": "b"})
        self.assertIsNone(sessions.pick_session(session_list, "9"))
        self.assertIsNone(sessions.pick_session(session_list, "x"))

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

        with patch.object(sessions, "SESSIONS_DIR", sessions_dir):
            saved = sessions.list_saved_sessions()

        self.assertEqual([session["name"] for session in saved], ["session-z", "session-a"])

    def test_continue_most_recent_loads_newest_session(self):
        system_message = {"role": "system", "content": "new system"}

        with patch("sessions.most_recent_session_name", return_value="session-new"), \
                patch("sessions.load_session", return_value=[
                    {"role": "system", "content": "old system"},
                    {"role": "user", "content": "hello"},
                ]):
            with redirect_stdout(io.StringIO()):
                session_name, messages = sessions.continue_most_recent(system_message)

        self.assertEqual(session_name, "session-new")
        self.assertEqual(messages[0], system_message)
        self.assertEqual(messages[1]["content"], "hello")

    def test_resume_session_picker_can_start_new(self):
        system_message = {"role": "system", "content": "system"}

        with patch("sessions.list_saved_sessions", return_value=[
            {
                "name": "session-1",
                "display_name": "feature-work",
                "updated_at": "2026-01-01T10:00:00",
                "message_count": 2,
                "preview": "hello",
            }
        ]), patch("builtins.input", return_value="new"), \
                patch("sessions.create_session_name", return_value="session-new"):
            with redirect_stdout(io.StringIO()):
                session_name, messages = sessions.resume_session_picker(system_message)

        self.assertEqual(session_name, "session-new")
        self.assertEqual(messages, [system_message])


if __name__ == "__main__":
    unittest.main()
