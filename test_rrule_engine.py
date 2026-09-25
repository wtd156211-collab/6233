"""rrule_engine 的 unittest 测试。"""

import datetime as dt
import json
import time
import unittest
from pathlib import Path

from rrule_engine import (
    MAX_OVERRIDES,
    MAX_PERIODS,
    Engine,
    NoOccurrence,
    OutOfRange,
    TooManyOverrides,
)
from runner import run_case
from run_samples import CASES, SAMPLES


class SampleCasesTest(unittest.TestCase):
    """samples/ 下每个用例的输出必须与 expected 文件逐行一致。"""

    def test_samples_match_expected(self):
        for name in sorted(CASES):
            with self.subTest(case=name):
                rule = json.loads(
                    (SAMPLES / f"{name}.rule.json").read_text(encoding="utf-8")
                )
                meta = CASES[name]
                actual = run_case(name, meta["note"], rule, meta["queries"])
                expected = (
                    (SAMPLES / f"{name}.expected.txt")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                self.assertEqual(actual, expected)


def expand(rule):
    return Engine(rule).expand()


class FreqTest(unittest.TestCase):
    def test_daily_interval(self):
        r = expand({"freq": "daily", "dtstart": "2026-01-01",
                    "interval": 3, "count": 3})
        self.assertEqual(r.dates, [dt.date(2026, 1, 1), dt.date(2026, 1, 4),
                                   dt.date(2026, 1, 7)])
        self.assertEqual(r.periods, 3)

    def test_weekly_default_weekday(self):
        # 不带 byday 时按 dtstart 的星期几
        r = expand({"freq": "weekly", "dtstart": "2026-01-07", "count": 2})
        self.assertEqual(r.dates, [dt.date(2026, 1, 7), dt.date(2026, 1, 14)])

    def test_weekly_first_week_clipped_by_dtstart(self):
        # dtstart 是周三,第一周里周一的候选要被裁掉
        r = expand({"freq": "weekly", "dtstart": "2026-01-07",
                    "byday": ["MO", "WE"], "count": 3})
        self.assertEqual(r.dates, [dt.date(2026, 1, 7), dt.date(2026, 1, 12),
                                   dt.date(2026, 1, 14)])

    def test_monthly_default_day_skips_short_months(self):
        # 没有 31 号的月份不产生,不顺延到 30 号
        r = expand({"freq": "monthly", "dtstart": "2026-01-31", "count": 3})
        self.assertEqual(r.dates, [dt.date(2026, 1, 31), dt.date(2026, 3, 31),
                                   dt.date(2026, 5, 31)])

    def test_yearly_feb29_skips_common_years(self):
        r = expand({"freq": "yearly", "dtstart": "2024-02-29", "count": 3})
        self.assertEqual(r.dates, [dt.date(2024, 2, 29), dt.date(2028, 2, 29),
                                   dt.date(2032, 2, 29)])

    def test_yearly_2100_is_not_leap(self):
        eng = Engine({"freq": "yearly", "dtstart": "2096-02-29"})
        self.assertEqual(eng.nth(2), dt.date(2104, 2, 29))  # 2100 是平年


class FilterTest(unittest.TestCase):
    def test_bysetpos_negative(self):
        # 每月最后一个周五
        eng = Engine({"freq": "monthly", "dtstart": "2026-01-01",
                      "byday": ["FR"], "bysetpos": [-1]})
        self.assertEqual(eng.nth(1), dt.date(2026, 1, 30))
        self.assertEqual(eng.nth(2), dt.date(2026, 2, 27))

    def test_bysetpos_out_of_range_yields_nothing(self):
        # 2026 年 2 月只有 4 个周五,取第 5 个 → 该月不产生
        eng = Engine({"freq": "monthly", "dtstart": "2026-02-01",
                      "byday": ["FR"], "bysetpos": [5]})
        self.assertEqual(eng.nth(1), dt.date(2026, 5, 29))

    def test_bysetpos_multiple_positions(self):
        eng = Engine({"freq": "monthly", "dtstart": "2026-01-01",
                      "bymonthday": [1, 15, 28], "bysetpos": [1, -1]})
        self.assertEqual(eng.nth(1), dt.date(2026, 1, 1))
        self.assertEqual(eng.nth(2), dt.date(2026, 1, 28))
        self.assertEqual(eng.nth(3), dt.date(2026, 2, 1))

    def test_bymonth_limits_yearly(self):
        eng = Engine({"freq": "yearly", "dtstart": "2026-01-01",
                      "bymonth": [3, 9], "bymonthday": [10]})
        self.assertEqual(eng.nth(1), dt.date(2026, 3, 10))
        self.assertEqual(eng.nth(2), dt.date(2026, 9, 10))
        self.assertEqual(eng.nth(3), dt.date(2027, 3, 10))


class LimitTest(unittest.TestCase):
    def test_until_inclusive(self):
        # 截止当天算在内
        r = expand({"freq": "daily", "dtstart": "2026-01-01",
                    "until": "2026-01-03"})
        self.assertEqual(r.dates, [dt.date(2026, 1, 1), dt.date(2026, 1, 2),
                                   dt.date(2026, 1, 3)])
        self.assertEqual(r.total, 3)

    def test_until_exclusive_would_drop_last(self):
        r = expand({"freq": "daily", "dtstart": "2026-01-01",
                    "until": "2026-01-01"})
        self.assertEqual(r.total, 1)

    def test_count_on_final_sequence_after_exdates(self):
        # count 作用在最终序列上:取消后后面的日期顶上来
        r = expand({"freq": "daily", "dtstart": "2026-01-01", "count": 3,
                    "exdates": ["2026-01-02"]})
        self.assertEqual(r.dates, [dt.date(2026, 1, 1), dt.date(2026, 1, 3),
                                   dt.date(2026, 1, 4)])

    def test_fallback_cap(self):
        # 永远排不出来的规则:扫满兜底周期后报 NO_OCCURRENCE
        r = expand({"freq": "yearly", "dtstart": "2026-01-01",
                    "bymonth": [2], "bymonthday": [30]})
        self.assertEqual(r.total, 0)
        self.assertEqual(r.periods, MAX_PERIODS)

    def test_no_count_no_until_capped(self):
        r = expand({"freq": "daily", "dtstart": "2026-01-01"})
        self.assertEqual(r.total, MAX_PERIODS)
        self.assertEqual(r.periods, MAX_PERIODS)


class OverrideTest(unittest.TestCase):
    def test_exdate_shifts_nth(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01",
                      "exdates": ["2026-01-01"]})
        self.assertEqual(eng.nth(1), dt.date(2026, 1, 2))

    def test_move_replaces_date(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01", "count": 3,
                      "moves": [{"from": "2026-01-02", "to": "2026-02-01"}]})
        self.assertEqual(eng.nth(1), dt.date(2026, 1, 1))
        self.assertEqual(eng.nth(2), dt.date(2026, 1, 3))
        self.assertEqual(eng.nth(3), dt.date(2026, 1, 4))
        # 挪到的日期也在最终序列里(count 之外的由 total 体现)
        r = eng.expand()
        self.assertIn(dt.date(2026, 2, 1),
                      Engine({"freq": "daily", "dtstart": "2026-01-01",
                              "moves": [{"from": "2026-01-02",
                                         "to": "2026-02-01"}]}).expand().dates)
        self.assertEqual(r.total, 3)

    def test_move_to_existing_date_dedupes(self):
        r = expand({"freq": "daily", "dtstart": "2026-01-01", "count": 5,
                    "moves": [{"from": "2026-01-02", "to": "2026-01-03"}]})
        self.assertEqual(r.dates.count(dt.date(2026, 1, 3)), 1)

    def test_too_many_exdates(self):
        with self.assertRaises(TooManyOverrides):
            Engine({"freq": "daily", "dtstart": "2026-01-01",
                    "exdates": ["2026-01-01"] * (MAX_OVERRIDES + 1)})

    def test_too_many_moves(self):
        with self.assertRaises(TooManyOverrides):
            Engine({"freq": "daily", "dtstart": "2026-01-01",
                    "moves": [{"from": "2026-01-01", "to": "2026-01-02"}]
                    * (MAX_OVERRIDES + 1)})

    def test_too_many_overrides_output(self):
        lines = run_case("x", "n", {"freq": "daily", "dtstart": "2026-01-01",
                                    "exdates": ["2026-01-01"] * 1001}, [])
        self.assertEqual(lines[-1],
                         "error,TOO_MANY_OVERRIDES,exdates=1001 moves=0 limit=1000")


class QueryTest(unittest.TestCase):
    def test_nth_out_of_range(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01", "count": 2})
        with self.assertRaises(OutOfRange) as ctx:
            eng.nth(3)
        self.assertEqual(ctx.exception.message, "n=3 total=2")

    def test_next_is_strictly_after(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01"})
        self.assertEqual(eng.next_after(dt.date(2026, 1, 1)),
                         dt.date(2026, 1, 2))

    def test_next_before_start(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01"})
        self.assertEqual(eng.next_after(dt.date(2020, 1, 1)),
                         dt.date(2026, 1, 1))

    def test_next_after_last_raises(self):
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01", "count": 1})
        with self.assertRaises(NoOccurrence):
            eng.next_after(dt.date(2026, 1, 1))

    def test_large_nth_is_fast(self):
        # 大 N 直接定位,不许逐条数过去
        eng = Engine({"freq": "daily", "dtstart": "2026-01-01"})
        start = time.monotonic()
        self.assertEqual(eng.nth(100000), dt.date(2299, 10, 16))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_next_far_future_is_fast(self):
        eng = Engine({"freq": "monthly", "dtstart": "2026-01-31"})
        start = time.monotonic()
        self.assertEqual(eng.next_after(dt.date(9990, 1, 1)),
                         dt.date(9990, 1, 31))
        self.assertLess(time.monotonic() - start, 2.0)


class RobustnessTest(unittest.TestCase):
    def test_deterministic(self):
        rule = {"freq": "monthly", "dtstart": "2026-01-01", "interval": 2,
                "bymonthday": [1, 15], "bysetpos": [2],
                "exdates": ["2026-03-15"],
                "moves": [{"from": "2026-05-15", "to": "2026-05-16"}]}
        self.assertEqual(expand(rule).dates, expand(rule).dates)

    def test_garbage_values_do_not_crash(self):
        # 离谱输入:非法间隔/日期/越界的月日,都不许崩也不许卡
        r = expand({"freq": "monthly", "dtstart": "2026-01-01",
                    "interval": 0, "bymonthday": [0, 32, 15],
                    "bymonth": [13], "until": "not-a-date",
                    "exdates": ["2026-02-30", "bad"]})
        self.assertEqual(r.total, 0)
        self.assertEqual(r.periods, MAX_PERIODS)

    def test_no_occurrence_output_line(self):
        lines = run_case("x", "n", {"freq": "yearly", "dtstart": "2026-01-01",
                                    "bymonth": [2], "bymonthday": [30]}, [])
        self.assertEqual(lines[-1], "error,NO_OCCURRENCE,searched=100000")


if __name__ == "__main__":
    unittest.main()
