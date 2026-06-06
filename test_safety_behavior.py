import unittest

from safety import decide_command


class SafetyBehaviorTests(unittest.TestCase):
    def test_ask_mode_asks_for_known_safe_commands(self):
        decision = decide_command("git status", approval_mode="ask")

        self.assertEqual(decision.action, "ask")

    def test_safe_auto_mode_allows_known_safe_commands(self):
        decision = decide_command("git status", approval_mode="safe_auto")

        self.assertEqual(decision.action, "allow")

    def test_safe_auto_mode_asks_for_unknown_commands(self):
        decision = decide_command("python custom_script.py", approval_mode="safe_auto")

        self.assertEqual(decision.action, "ask")

    def test_full_auto_mode_allows_unknown_commands(self):
        decision = decide_command("python custom_script.py", approval_mode="full_auto")

        self.assertEqual(decision.action, "allow")

    def test_full_auto_mode_still_blocks_dangerous_commands(self):
        decision = decide_command("git reset --hard", approval_mode="full_auto")

        self.assertEqual(decision.action, "block")


if __name__ == "__main__":
    unittest.main()
