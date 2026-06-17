import unittest

from safety import decide_command


class SafetyBehaviorTests(unittest.TestCase):
    def test_unknown_approval_mode_falls_back_to_safe_auto(self):
        decision = decide_command("git status", approval_mode="nonsense")

        self.assertEqual(decision.action, "allow")

    def test_safe_auto_mode_allows_known_safe_commands(self):
        decision = decide_command("git status", approval_mode="safe_auto")

        self.assertEqual(decision.action, "allow")

    def test_safe_auto_mode_requires_approval_for_unknown_commands(self):
        decision = decide_command("python custom_script.py", approval_mode="safe_auto")

        self.assertEqual(decision.action, "approval_required")

    def test_full_auto_mode_allows_unknown_commands(self):
        decision = decide_command("python custom_script.py", approval_mode="full_auto")

        self.assertEqual(decision.action, "allow")

    def test_full_auto_mode_still_blocks_dangerous_commands(self):
        decision = decide_command("git reset --hard", approval_mode="full_auto")

        self.assertEqual(decision.action, "block")


if __name__ == "__main__":
    unittest.main()
