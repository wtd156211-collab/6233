"""日历重复规则引擎(RRULE 子集)。

设计要点:
- 按周期直接推算:每个周期(天/周/月/年)用日历算术一次算出候选日期,
  不逐日枚举;展开与查询最多扫描 MAX_PERIODS 个周期,兜底防死循环。
- 取第 N 次 = 最终序列的下标访问 O(1);求下一次 = 二分查找 O(log n)。
- 覆盖(exdates/moves)作用在原始序列上,排序去重后得到最终序列,
  count 与序号都针对最终序列。
- 只依赖标准库。
"""

from __future__ import annotations

import bisect
import calendar
import datetime as dt
from typing import NamedTuple, Optional

MAX_PERIODS = 100000
MAX_OVERRIDES = 1000

FREQS = ("daily", "weekly", "monthly", "yearly")
WEEKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

MAX_ORD = dt.date.max.toordinal()


class RuleError(Exception):
    """规则层面的错误,code 对应输出里的错误码。"""

    code = "INVALID_RULE"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TooManyOverrides(RuleError):
    code = "TOO_MANY_OVERRIDES"


class NoOccurrence(RuleError):
    code = "NO_OCCURRENCE"


class OutOfRange(RuleError):
    code = "OUT_OF_RANGE"


class ExpandResult(NamedTuple):
    total: int
    first: Optional[dt.date]
    last: Optional[dt.date]
    periods: int
    dates: list


def _parse_date(value) -> Optional[dt.date]:
    """宽松解析 YYYY-MM-DD,非法值返回 None(不许因为脏数据崩掉)。"""
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_int_list(value) -> Optional[list]:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool):
            out.append(item)
    return out or None


class Engine:
    """一条规则的展开与查询。查询前需先 expand()。"""

    def __init__(self, rule: dict):
        if not isinstance(rule, dict):
            raise RuleError("rule must be a JSON object")
        freq = rule.get("freq")
        if freq not in FREQS:
            raise RuleError(f"unsupported freq: {freq!r}")
        self.freq = freq

        interval = rule.get("interval", 1)
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
            interval = 1  # 离谱的间隔兜底为 1,不许转不出来
        self.interval = interval

        self.dtstart = _parse_date(rule.get("dtstart"))
        if self.dtstart is None:
            raise RuleError("dtstart is required (YYYY-MM-DD)")

        count = rule.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            count = None
        self.count = count

        until = rule.get("until")
        self.until = _parse_date(until) if until is not None else None

        byday = rule.get("byday")
        self.byday = None
        if isinstance(byday, list):
            days = sorted({WEEKDAY[d] for d in byday if d in WEEKDAY})
            self.byday = days or None
        self.byday_set = frozenset(self.byday) if self.byday else None

        self.bymonthday = _parse_int_list(rule.get("bymonthday"))
        self.bysetpos = _parse_int_list(rule.get("bysetpos"))
        self.bymonth = _parse_int_list(rule.get("bymonth"))
        self.bymonth_set = frozenset(self.bymonth) if self.bymonth else None

        exdates = rule.get("exdates") or []
        moves = rule.get("moves") or []
        if not isinstance(exdates, list):
            exdates = []
        if not isinstance(moves, list):
            moves = []
        if len(exdates) > MAX_OVERRIDES or len(moves) > MAX_OVERRIDES:
            raise TooManyOverrides(
                f"exdates={len(exdates)} moves={len(moves)} limit={MAX_OVERRIDES}"
            )

        exdate_set = {d for d in (_parse_date(x) for x in exdates) if d is not None}
        move_froms = set()
        move_tos = set()
        for entry in moves:
            if not isinstance(entry, dict):
                continue
            to = _parse_date(entry.get("to"))
            if to is None:
                continue
            move_tos.add(to)
            frm = _parse_date(entry.get("from"))
            if frm is not None:
                move_froms.add(frm)
        # 顺序:先生成原始序列,再按 exdates 删除、按 moves 替换,最后排序去重
        self.removed = frozenset(exdate_set | move_froms)
        self.move_tos = frozenset(move_tos)

        self._start_ord = self.dtstart.toordinal()
        self._week0_ord = self._start_ord - self.dtstart.weekday()  # 周一为一周起点
        self._month0 = self.dtstart.year * 12 + (self.dtstart.month - 1)
        self._year0 = self.dtstart.year

        self._result: Optional[ExpandResult] = None

    # ---- 周期内候选日期的直接推算 ----

    def _match_day_filters(self, d: dt.date) -> bool:
        if self.byday_set is not None and d.weekday() not in self.byday_set:
            return False
        if self.bymonthday is not None and d.day not in self.bymonthday:
            return False
        if self.bymonth_set is not None and d.month not in self.bymonth_set:
            return False
        return True

    def _month_candidates(self, year: int, month: int) -> list:
        if self.bymonth_set is not None and month not in self.bymonth_set:
            return []
        dim = calendar.monthrange(year, month)[1]
        if self.bymonthday is not None:
            days = set()
            for md in self.bymonthday:
                if 1 <= md <= dim:  # 没有这一天就跳过,不顺延
                    d = dt.date(year, month, md)
                    if self.byday_set is None or d.weekday() in self.byday_set:
                        days.add(d)
            return sorted(days)
        if self.byday_set is not None:
            return [
                dt.date(year, month, day)
                for day in range(1, dim + 1)
                if dt.date(year, month, day).weekday() in self.byday_set
            ]
        md = self.dtstart.day
        return [dt.date(year, month, md)] if md <= dim else []

    def _apply_setpos(self, days: list) -> list:
        """bysetpos:在当期已筛出的日期里取第几个(1 起,负数从后往前)。"""
        if not self.bysetpos:
            return days
        n = len(days)
        picked = set()
        for pos in self.bysetpos:
            idx = pos - 1 if pos > 0 else n + pos
            if 0 <= idx < n:  # 越界(如第 5 个星期五但只有 4 个)则当期不产生
                picked.add(days[idx])
        return sorted(picked)

    def _period_candidates(self, p: int) -> list:
        """第 p 个周期(0 起)内的候选日期,按日历算术直接算出。"""
        if self.freq == "daily":
            ordinal = self._start_ord + p * self.interval
            if ordinal > MAX_ORD:
                return []
            d = dt.date.fromordinal(ordinal)
            days = [d] if self._match_day_filters(d) else []
            return self._apply_setpos(days)
        if self.freq == "weekly":
            monday = self._week0_ord + p * 7 * self.interval
            weekdays = self.byday if self.byday else [self.dtstart.weekday()]
            days = []
            for wd in weekdays:
                ordinal = monday + wd
                if ordinal > MAX_ORD:
                    continue
                d = dt.date.fromordinal(ordinal)
                if self.bymonthday is not None and d.day not in self.bymonthday:
                    continue
                if self.bymonth_set is not None and d.month not in self.bymonth_set:
                    continue
                days.append(d)
            return self._apply_setpos(sorted(days))
        if self.freq == "monthly":
            year, month0 = divmod(self._month0 + p * self.interval, 12)
            if year > 9999:
                return []
            return self._apply_setpos(self._month_candidates(year, month0 + 1))
        # yearly
        year = self._year0 + p * self.interval
        if year > 9999:
            return []
        months = self.bymonth if self.bymonth else [self.dtstart.month]
        days = []
        for m in months:
            if 1 <= m <= 12:
                days.extend(self._month_candidates(year, m))
        return self._apply_setpos(sorted(days))

    def _period_min_ordinal(self, p: int) -> int:
        """第 p 个周期可能产生的最早日期的序数,用于 until 的提前终止。"""
        if self.freq == "daily":
            return self._start_ord + p * self.interval
        if self.freq == "weekly":
            return self._week0_ord + p * 7 * self.interval
        if self.freq == "monthly":
            year, month0 = divmod(self._month0 + p * self.interval, 12)
            if year > 9999:
                return MAX_ORD + 1
            return dt.date(year, month0 + 1, 1).toordinal()
        year = self._year0 + p * self.interval
        if year > 9999:
            return MAX_ORD + 1
        return dt.date(year, 1, 1).toordinal()

    # ---- 展开 ----

    def expand(self) -> ExpandResult:
        if self._result is not None:
            return self._result
        raw = []
        survivors = 0  # 原始序列中未被 exdates/moves 删掉的条数
        periods = 0
        until_ord = self.until.toordinal() if self.until else None
        for p in range(MAX_PERIODS):  # 兜底:最多扫描 MAX_PERIODS 个周期
            if until_ord is not None and self._period_min_ordinal(p) > until_ord:
                break  # 之后的周期只会更晚,until 之外不可能再有发生
            for d in self._period_candidates(p):
                o = d.toordinal()
                if o < self._start_ord or (until_ord is not None and o > until_ord):
                    continue
                raw.append(d)
                if d not in self.removed:
                    survivors += 1
            periods = p + 1
            if self.count is not None and survivors >= self.count:
                break  # count 作用在最终序列上:幸存条数够了就可以停
        final = sorted(set(d for d in raw if d not in self.removed) | self.move_tos)
        if self.count is not None:
            final = final[: self.count]
        self._result = ExpandResult(
            total=len(final),
            first=final[0] if final else None,
            last=final[-1] if final else None,
            periods=periods,
            dates=final,
        )
        return self._result

    # ---- 查询(都要求先 expand;均为 O(1)/O(log n)) ----

    def nth(self, n: int) -> dt.date:
        """最终序列里的第 N 次(1 起),取消后后面的日期已顶上来。"""
        result = self.expand()
        if not 1 <= n <= result.total:
            raise OutOfRange(f"n={n} total={result.total}")
        return result.dates[n - 1]

    def next_after(self, when: dt.date) -> dt.date:
        """严格晚于 when 的第一次发生。"""
        result = self.expand()
        idx = bisect.bisect_right(result.dates, when)
        if idx >= result.total:
            raise NoOccurrence(f"searched={result.periods}")
        return result.dates[idx]
