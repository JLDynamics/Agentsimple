import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

import tools


class ToolsBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.test_file = Path("tmp_read_range_test.txt")
        self.test_file.write_text(
            "line one\nline two\nline three\nline four\n",
            encoding="utf-8",
        )
        self.second_test_file = Path("tmp_read_many_test.txt")
        self.second_test_file.write_text(
            "alpha\nbeta\n",
            encoding="utf-8",
        )
        self.test_folder = Path("tmp_tree_folder")
        self.test_folder.mkdir(exist_ok=True)
        (self.test_folder / "nested.txt").write_text("nested\n", encoding="utf-8")

    def tearDown(self):
        for path in (self.test_file, self.second_test_file):
            if path.exists():
                path.unlink()

        nested_file = self.test_folder / "nested.txt"
        if nested_file.exists():
            nested_file.unlink()
        if self.test_folder.exists():
            self.test_folder.rmdir()

    def test_read_file_range_returns_numbered_lines(self):
        result = tools.read_file_range("tmp_read_range_test.txt", 2, 3)

        self.assertIn("Showing lines 2-3 of 4", result)
        self.assertIn("2: line two", result)
        self.assertIn("3: line three", result)
        self.assertNotIn("1: line one", result)

    def test_terminal_get_content_is_blocked(self):
        result = tools.execute_terminal_command("Get-Content main.py")

        self.assertIn("BLOCKED", result)
        self.assertIn("read_file", result)
        self.assertIn("read_file_range", result)

    def test_terminal_get_content_with_semicolon_is_blocked_before_approval(self):
        result = tools.execute_terminal_command(
            '$lines = Get-Content main.py -Encoding utf8; $lines[200..300] -join "`n"'
        )

        self.assertIn("BLOCKED", result)
        self.assertIn("read_file_range", result)

    def test_get_file_info_returns_basic_metadata(self):
        result = tools.get_file_info("tmp_read_range_test.txt")

        self.assertIn("Path: tmp_read_range_test.txt", result)
        self.assertIn("Type: file", result)
        self.assertIn("Lines: 4", result)
        self.assertIn("Size:", result)
        self.assertIn("Modified:", result)

    def test_read_many_files_returns_labeled_sections(self):
        result = tools.read_many_files(
            ["tmp_read_range_test.txt", "tmp_read_many_test.txt"]
        )

        self.assertIn("FILE: tmp_read_range_test.txt", result)
        self.assertIn("line one", result)
        self.assertIn("FILE: tmp_read_many_test.txt", result)
        self.assertIn("alpha", result)

    def test_list_project_tree_shows_nested_files(self):
        result = tools.list_project_tree(".", max_depth=2)

        self.assertIn("[DIR] tmp_tree_folder", result)
        self.assertIn("[FILE] nested.txt", result)

    def test_run_python_tests_runs_unittest_command(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="tests passed\n",
            stderr="",
        )

        with patch("tools.subprocess.run", return_value=completed) as fake_run:
            result = tools.run_python_tests("test_main_behavior.py")

        command = fake_run.call_args.args[0]
        self.assertEqual(
            command,
            ["uv", "run", "python", "-m", "unittest", "test_main_behavior.py"],
        )
        self.assertIn("SUCCESS", result)
        self.assertIn("tests passed", result)

    def test_compile_python_runs_py_compile(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        with patch("tools.subprocess.run", return_value=completed) as fake_run:
            result = tools.compile_python(["main.py", "tools.py"])

        command = fake_run.call_args.args[0]
        self.assertEqual(
            command,
            ["uv", "run", "python", "-m", "py_compile", "main.py", "tools.py"],
        )
        self.assertIn("SUCCESS", result)

    def test_git_status_runs_safe_git_command(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=" M main.py\n",
            stderr="",
        )

        with patch("tools.subprocess.run", return_value=completed) as fake_run:
            result = tools.git_status()

        command = fake_run.call_args.args[0]
        self.assertEqual(command, ["git", "status", "--short"])
        self.assertIn("SUCCESS", result)
        self.assertIn("M main.py", result)

    def test_git_diff_runs_safe_git_command(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff output\n",
            stderr="",
        )

        with patch("tools.subprocess.run", return_value=completed) as fake_run:
            result = tools.git_diff("main.py")

        command = fake_run.call_args.args[0]
        self.assertEqual(command, ["git", "diff", "--", "main.py"])
        self.assertIn("SUCCESS", result)
        self.assertIn("diff output", result)

    def test_execute_terminal_command_ask_mode_requests_approval(self):
        with patch("tools.ask_for_approval", return_value=False) as fake_approval:
            with patch("tools.subprocess.run") as fake_run:
                result = tools.execute_terminal_command(
                    "python --version",
                    approval_mode="ask",
                )

        self.assertIn("CANCELLED", result)
        fake_approval.assert_called_once()
        fake_run.assert_not_called()

    def test_execute_terminal_command_safe_auto_runs_known_safe_command(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Python 3.13\n",
            stderr="",
        )

        with patch("tools.ask_for_approval") as fake_approval:
            with patch("tools.subprocess.run", return_value=completed) as fake_run:
                result = tools.execute_terminal_command(
                    "python --version",
                    approval_mode="safe_auto",
                )

        self.assertIn("SUCCESS", result)
        self.assertIn("Python 3.13", result)
        fake_approval.assert_not_called()
        fake_run.assert_called_once()

    def test_execute_terminal_command_full_auto_runs_unknown_command(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="custom output\n",
            stderr="",
        )

        with patch("tools.ask_for_approval") as fake_approval:
            with patch("tools.subprocess.run", return_value=completed) as fake_run:
                result = tools.execute_terminal_command(
                    "python custom_script.py",
                    approval_mode="full_auto",
                )

        self.assertIn("SUCCESS", result)
        self.assertIn("custom output", result)
        fake_approval.assert_not_called()
        fake_run.assert_called_once()

    def test_delete_file_removes_file(self):
        temp_path = Path("tmp_delete_test.txt")
        temp_path.write_text("bye\n", encoding="utf-8")
        self.addCleanup(lambda: temp_path.exists() and temp_path.unlink())

        result = tools.delete_file("tmp_delete_test.txt")

        self.assertIn("SUCCESS", result)
        self.assertFalse(temp_path.exists())

    def test_delete_file_refuses_folder(self):
        result = tools.delete_file("tmp_tree_folder")

        self.assertIn("ERROR", result)
        self.assertTrue(self.test_folder.exists())

    def test_move_file_renames_file(self):
        source = Path("tmp_move_source.txt")
        source.write_text("data\n", encoding="utf-8")
        destination = Path("tmp_move_dest.txt")
        self.addCleanup(lambda: source.exists() and source.unlink())
        self.addCleanup(lambda: destination.exists() and destination.unlink())

        result = tools.move_file("tmp_move_source.txt", "tmp_move_dest.txt")

        self.assertIn("SUCCESS", result)
        self.assertFalse(source.exists())
        self.assertTrue(destination.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "data\n")

    def test_search_files_supports_regex(self):
        temp_path = Path("tmp_search_test.txt")
        temp_path.write_text("def zzqq_handler():\n    pass\n", encoding="utf-8")
        self.addCleanup(lambda: temp_path.exists() and temp_path.unlink())

        result = tools.search_files("def zz.*handler", ".")

        self.assertIn("tmp_search_test.txt", result)
        self.assertIn("zzqq_handler", result)

    def test_search_files_reports_invalid_regex(self):
        result = tools.search_files("def (", ".")

        self.assertIn("ERROR", result)
        self.assertIn("Invalid search pattern", result)

    def test_apply_patch_replaces_unique_old_text(self):
        temp_path = Path("tmp_patch_unique.txt")
        temp_path.write_text("a = 1\nb = 2\n", encoding="utf-8")
        self.addCleanup(lambda: temp_path.exists() and temp_path.unlink())

        result = tools.apply_patch(
            "tmp_patch_unique.txt",
            [{"old_text": "b = 2", "new_text": "b = 3"}],
        )

        self.assertIn("SUCCESS", result)
        self.assertEqual(temp_path.read_text(encoding="utf-8"), "a = 1\nb = 3\n")

    def test_apply_patch_refuses_non_unique_old_text(self):
        temp_path = Path("tmp_patch_dup.txt")
        temp_path.write_text("x = 1\nx = 1\n", encoding="utf-8")
        self.addCleanup(lambda: temp_path.exists() and temp_path.unlink())

        result = tools.apply_patch(
            "tmp_patch_dup.txt",
            [{"old_text": "x = 1", "new_text": "x = 2"}],
        )

        self.assertIn("ERROR", result)
        self.assertIn("must be unique", result)
        self.assertEqual(temp_path.read_text(encoding="utf-8"), "x = 1\nx = 1\n")


if __name__ == "__main__":
    unittest.main()
