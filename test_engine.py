"""rrule_engine 的 unittest 测试。

运行：python3 -m unittest test_engine -v
"""

import json
import time
import unittest
from datetime import date
from pathlib import Path

from rrule_engine import (
    MAX_PERIODS,
    Engine,
    NoOccurrence,
    OutOfRange,
    Rule,
    TooManyOverrides,
)
from main import run_file

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"


def expand(spec):
    engine = Engine(Rule(spec))
    engine.expand()
    return engine


class SamplesTest(unittest.TestCase):
    """samples/ 下每个 case 的输出必须与 expected 文件逐字节一致。"""

    def test_all_samples(self):
        for expected_path in sorted(SAMPLES_DIR.glob("*.expected.txt")):
            case = expected_path.name[: -len(".expected.txt")]
            with self.subTest(case=case):
                lines = run_file(SAMPLES_DIR / f"{case}.rule.json")
                expected = expected_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(lines, expected)


class BoundaryTest(unittest.TestCase):
    def test_month_without_31st_is_skipped(self):
        engine = expand({
            "freq": "monthly", "dtstart": "2026-01-31",
            "bymonthday": [31], "count": 3,
        })
        self.assertEqual(
            engine.dates,
            [date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31)],
        )

    def test_fifth_friday_missing_month_produces_nothing(self):
        engine = expand({
            "freq": "monthly", "dtstart": "2026-02-01",
            "byday": ["FR"], "bysetpos": [5], "count": 1,
        })
        # 2026 年 2 月只有 4 个星期五，整个月跳过
        self.assertEqual(engine.dates[0], date(2026, 5, 29))

    def test_feb29_skipped_in_common_years(self):
        engine = expand({
            "freq": "yearly", "dtstart": "2024-02-29",
            "bymonth": [2], "bymonthday": [29], "count": 3,
        })
        self.assertEqual(
            engine.dates,
            [date(2024, 2, 29), date(2028, 2, 29), date(2032, 2, 29)],
        )

    def test_until_day_is_inclusive(self):
        engine = expand({
            "freq": "daily", "dtstart": "2026-01-01", "until": "2026-01-03",
        })
        self.assertEqual(
            engine.dates,
            [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
        )

    def test_bysetpos_negative_picks_from_end(self):
        engine = expand({
            "freq": "monthly", "dtstart": "2026-01-01",
            "bymonthday": [1, 15], "bysetpos": [-1], "count": 2,
        })
        self.assertEqual(engine.dates, [date(2026, 1, 15), date(2026, 2, 15)])

    def test_bysetpos_out_of_range_month_is_empty(self):
        engine = expand({
            "freq": "monthly", "dtstart": "2026-01-01",
            "bymonthday": [1, 15], "bysetpos": [3], "until": "2026-03-31",
        })
        self.assertEqual(engine.dates, [])


class OverrideTest(unittest.TestCase):
    def test_exdate_shifts_indices_up(self):
        engine = expand({
            "freq": "daily", "dtstart": "2026-01-01",
            "exdates": ["2026-01-02"], "count": 3,
        })
        # count 作用在最终序列上：取消 1 月 2 日后，后面的日期顶上来
        self.assertEqual(
            engine.dates,
            [date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 4)],
        )

    def test_move_replaces_date(self):
        engine = expand({
            "freq": "weekly", "dtstart": "2026-01-05", "byday": ["MO"],
            "moves": [{"from": "2026-01-12", "to": "2026-01-14"}],
            "count": 3,
        })
        self.assertEqual(
            engine.dates,
            [date(2026, 1, 5), date(2026, 1, 14), date(2026, 1, 19)],
        )

    def test_too_many_exdates(self):
        spec = {
            "freq": "daily", "dtstart": "2026-01-01",
            "exdates": ["2026-01-01"] * 1001,
        }
        with self.assertRaises(TooManyOverrides):
            Rule(spec)

    def test_too_many_moves(self):
        spec = {
            "freq": "daily", "dtstart": "2026-01-01",
            "moves": [{"from": "2026-01-01", "to": "2026-01-02"}] * 1001,
        }
        with self.assertRaises(TooManyOverrides):
            Rule(spec)

    def test_exactly_1000_overrides_is_allowed(self):
        spec = {
            "freq": "daily", "dtstart": "2026-01-01",
            "exdates": ["2026-01-01"] * 1000,
        }
        Rule(spec)


class QueryTest(unittest.TestCase):
    def test_nth_is_one_based(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01", "count": 3})
        self.assertEqual(engine.nth(1), date(2026, 1, 1))
        self.assertEqual(engine.nth(3), date(2026, 1, 3))

    def test_nth_out_of_range(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01", "count": 2})
        with self.assertRaises(OutOfRange) as ctx:
            engine.nth(3)
        self.assertEqual(ctx.exception.detail, "n=3 total=2")
        with self.assertRaises(OutOfRange):
            engine.nth(0)

    def test_next_is_strictly_after(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01"})
        # 2026-01-01 本身就是发生日，下一次是 01-02
        self.assertEqual(engine.next_after(date(2026, 1, 1)), date(2026, 1, 2))

    def test_next_beyond_end_reports_no_occurrence(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01", "count": 2})
        with self.assertRaises(NoOccurrence) as ctx:
            engine.next_after(date(2026, 1, 2))
        self.assertIn("searched=", ctx.exception.detail)

    def test_big_nth_is_fast(self):
        # 大 N 直接推算：第 100000 次不能逐条数，整体应在很短时间内返回
        engine = Engine(Rule({"freq": "daily", "dtstart": "2026-01-01"}))
        start = time.monotonic()
        self.assertEqual(engine.nth(100000), date(2299, 10, 16))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_next_far_future_is_fast(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01"})
        start = time.monotonic()
        self.assertEqual(engine.next_after(date(2030, 5, 5)), date(2030, 5, 6))
        self.assertLess(time.monotonic() - start, 1.0)


class FallbackTest(unittest.TestCase):
    def test_impossible_rule_stops_at_cap(self):
        engine = expand({
            "freq": "yearly", "dtstart": "2026-01-01",
            "bymonth": [2], "bymonthday": [30],
        })
        self.assertEqual(engine.dates, [])
        self.assertEqual(engine.periods, MAX_PERIODS)

    def test_unbounded_rule_caps_at_max_periods(self):
        engine = expand({"freq": "daily", "dtstart": "2026-01-01"})
        self.assertEqual(engine.periods, MAX_PERIODS)
        self.assertEqual(len(engine.dates), MAX_PERIODS)

    def test_determinism(self):
        spec = json.loads(
            (SAMPLES_DIR / "case-5.rule.json").read_text(encoding="utf-8")
        )
        first = run_file(SAMPLES_DIR / "case-5.rule.json")
        second = run_file(SAMPLES_DIR / "case-5.rule.json")
        self.assertEqual(first, second)
        self.assertEqual(expand(spec).dates, expand(spec).dates)


if __name__ == "__main__":
    unittest.main()
