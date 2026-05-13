from unittest import TestCase

from security_demo import evaluate_rule


class RuleTests(TestCase):
    def test_evaluate_rule_supports_arithmetic(self) -> None:
        self.assertEqual(
            evaluate_rule("price - discount", {"price": 20, "discount": 5}),
            15,
        )
