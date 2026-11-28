import sys
import unittest
if sys.platform != "win32":
    sys.modules.setdefault("pygetwindow", None)

from smart_ai import NaturalRuleParser


class NaturalRuleTest(unittest.TestCase):
    def setUp(self):
        self.parser = NaturalRuleParser([
            {"name": "Rồng Lửa Đỏ", "rarity": "Phổ biến"},
            {"name": "Rồng Rừng", "rarity": "Event"},
        ])

    def test_unsupported_sell_condition_leaves_every_rule_unchanged(self):
        for sentence in (
            "Bán tất cả trừ rồng Event",
            "Không được bán rồng Phổ biến",
            "Đừng bán rồng Event",
            "Bán rồng Phổ biến <= 23 sao",
            "Bán rồng Phổ biến tối đa 23 sao",
            "Bán rồng Phổ biến; cất Event 100 sao",
        ):
            with self.subTest(sentence=sentence):
                result = self.parser.parse(sentence)
                self.assertTrue(result["error"])
                self.assertEqual(result["matched_count"], 0)
                self.assertFalse(result["rules"]["sell"])

    def test_supported_conditional_actions_stay_in_their_original_clause(self):
        result = self.parser.parse("Bán rồng Phổ biến 23★ nhưng cất rồng Event > 23 sao")
        self.assertEqual(result["rules"]["sell"], ["Rồng Lửa Đỏ (23 sao)"])
        self.assertEqual(result["rules"]["keep"], ["Rồng Rừng (>= 24 sao)"])
