from __future__ import annotations

import unittest

from agent.task_parser import parse_action_sequence


class TaskParserTests(unittest.TestCase):
    def test_button_word_does_not_create_press_key_action(self) -> None:
        hints = parse_action_sequence("点击 `...` 按钮。")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].action, "click")
        self.assertEqual(hints[0].target_hint, "...")

    def test_press_down_still_creates_press_key_action(self) -> None:
        hints = parse_action_sequence("按下 `Enter`。")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].action, "press_key")
        self.assertEqual(hints[0].target_hint, "Enter")


if __name__ == "__main__":
    unittest.main()
