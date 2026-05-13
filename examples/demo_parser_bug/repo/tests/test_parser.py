from unittest import TestCase

from parser_demo import parse_csv_line


class ParserTests(TestCase):
    def test_parse_csv_line_trims_fields(self) -> None:
        self.assertEqual(parse_csv_line(" alpha, beta ,gamma "), ["alpha", "beta", "gamma"])

    def test_parse_csv_line_keeps_inner_empty_fields(self) -> None:
        self.assertEqual(parse_csv_line("alpha,,gamma"), ["alpha", "", "gamma"])
