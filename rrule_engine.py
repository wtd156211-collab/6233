"""日历重复规则引擎（RRULE 子集）。

范围：freq 仅 daily/weekly/monthly/yearly，支持 interval/count/until，
筛选支持 byday/bymonthday/bysetpos/bymonth，支持 exdates/moves 覆盖。
不做时区与夏令时。只使用标准库。

核心思路：时间被切成「周期」（第 k 个周期由 k 与 interval 直接算出起始日，
不逐天推进）。每个周期内由 BYxxx 规则一次性算出候选日期。展开扫描以周期
为单位，上限 MAX_PERIODS 个周期；超出 9999-12-31 的周期视为空周期。
最终序列 = (原始序列 - exdates - moves.from + moves.to) 排序去重后，
再按 count 截断。取第 N 次是对最终序列的 O(1) 索引，求下一次是二分查找。
"""

from bisect import bisect_right
from calendar import monthrange
from datetime import date, timedelta

MAX_PERIODS = 100000
MAX_OVERRIDES = 1000
MAX_DATE = date(9999, 12, 31)

FREQS = ("daily", "weekly", "monthly", "yearly")
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


class RuleError(Exception):
    """携带错误码的规则/查询异常。"""

    code = "ERROR"

    def __init__(self, detail=""):
        super().__init__(detail)
        self.detail = detail


class NoOccurrence(RuleError):
    code = "NO_OCCURRENCE"


class OutOfRange(RuleError):
    code = "OUT_OF_RANGE"


class TooManyOverrides(RuleError):
    code = "TOO_MANY_OVERRIDES"


def _parse_date(value, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: 非法日期 {value!r}")


class Rule:
    """解析并校验 JSON 规则。"""

    def __init__(self, spec):
        if not isinstance(spec, dict):
            raise ValueError("规则必须是 JSON 对象")
        freq = spec.get("freq")
        if freq not in FREQS:
            raise ValueError(f"freq 必须是 {FREQS} 之一，得到 {freq!r}")
        self.freq = freq

        interval = spec.get("interval", 1)
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
            raise ValueError(f"interval 必须是正整数，得到 {interval!r}")
        self.interval = interval

        count = spec.get("count")
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool) or count < 0
        ):
            raise ValueError(f"count 必须是非负整数，得到 {count!r}")
        self.count = count

        if "dtstart" not in spec:
            raise ValueError("缺少 dtstart")
        self.dtstart = _parse_date(spec["dtstart"], "dtstart")

        until = spec.get("until")
        self.until = _parse_date(until, "until") if until is not None else None

        self.byday = self._parse_byday(spec.get("byday"))
        self.bymonthday = self._parse_int_list(
            spec.get("bymonthday"), "bymonthday", 1, 31
        )
        self.bysetpos = self._parse_int_list(
            spec.get("bysetpos"), "bysetpos", -366, 366, allow_zero=False
        )
        self.bymonth = self._parse_int_list(spec.get("bymonth"), "bymonth", 1, 12)

        self.exdates = [
            _parse_date(d, "exdates") for d in spec.get("exdates") or []
        ]
        if len(self.exdates) > MAX_OVERRIDES:
            raise TooManyOverrides(
                f"exdates={len(self.exdates)} limit={MAX_OVERRIDES}"
            )

        self.moves = []
        for i, mv in enumerate(spec.get("moves") or []):
            if not isinstance(mv, dict) or "from" not in mv or "to" not in mv:
                raise ValueError(f"moves[{i}] 需要 from 与 to 字段")
            self.moves.append(
                (_parse_date(mv["from"], "moves.from"),
                 _parse_date(mv["to"], "moves.to"))
            )
        if len(self.moves) > MAX_OVERRIDES:
            raise TooManyOverrides(
                f"moves={len(self.moves)} limit={MAX_OVERRIDES}"
            )

    @staticmethod
    def _parse_byday(value):
        if not value:
            return None
        days = set()
        for item in value:
            if item not in WEEKDAYS:
                raise ValueError(f"byday 非法取值 {item!r}")
            days.add(WEEKDAYS[item])
        return days

    @staticmethod
    def _parse_int_list(value, field, lo, hi, allow_zero=True):
        if not value:
            return None
        result = []
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool):
                raise ValueError(f"{field} 必须是整数列表，得到 {item!r}")
            if item < lo or item > hi or (item == 0 and not allow_zero):
                raise ValueError(f"{field} 取值越界 {item!r}")
            result.append(item)
        return result


class Engine:
    """按周期展开规则，并回答 nth / next 查询。"""

    def __init__(self, rule):
        self.rule = rule
        self.dates = None      # 最终序列（排序去重、应用覆盖与 count 之后）
        self.periods = 0       # 实际扫描的周期数（上限 MAX_PERIODS）

    # ---- 周期推算 -------------------------------------------------------

    def _period_start(self, k):
        """第 k 个周期（0 起）的起始日；超出可表示范围返回 None。"""
        r = self.rule
        step = k * r.interval
        try:
            if r.freq == "daily":
                start = r.dtstart + timedelta(days=step)
            elif r.freq == "weekly":
                monday = r.dtstart - timedelta(days=r.dtstart.weekday())
                start = monday + timedelta(weeks=step)
            elif r.freq == "monthly":
                total = r.dtstart.year * 12 + (r.dtstart.month - 1) + step
                year, month = divmod(total, 12)
                if year > 9999:
                    return None
                start = date(year, month + 1, 1)
            else:  # yearly
                year = r.dtstart.year + step
                if year > 9999:
                    return None
                start = date(year, 1, 1)
        except (OverflowError, ValueError):
            return None
        return start if start <= MAX_DATE else None

    def _month_days(self, year, month):
        """月内候选：bymonthday 与 byday 取并集；都没有则回退到 dtstart 的日号。

        月内没有该日号（如 2 月 30 日、平年 2 月 29 日）时不产生日期。
        """
        r = self.rule
        dim = monthrange(year, month)[1]
        days = set()
        if r.bymonthday:
            for d in r.bymonthday:
                if d <= dim:
                    days.add(date(year, month, d))
        if r.byday:
            first_wd = date(year, month, 1).weekday()
            for wd in r.byday:
                day = 1 + (wd - first_wd) % 7
                while day <= dim:
                    days.add(date(year, month, day))
                    day += 7
        if not r.bymonthday and not r.byday:
            if r.dtstart.day <= dim:
                days.add(date(year, month, r.dtstart.day))
        return days

    def _period_dates(self, k):
        """第 k 个周期内、经 BYxxx 筛选与 bysetpos 选取后的有序日期。"""
        r = self.rule
        start = self._period_start(k)
        if start is None:
            return []
        if r.freq == "daily":
            cands = {start}
            if r.byday is not None:
                cands = {d for d in cands if d.weekday() in r.byday}
            if r.bymonthday is not None:
                cands = {d for d in cands if d.day in r.bymonthday}
            if r.bymonth is not None:
                cands = {d for d in cands if d.month in r.bymonth}
        elif r.freq == "weekly":
            weekdays = r.byday if r.byday is not None else {r.dtstart.weekday()}
            cands = {start + timedelta(days=wd) for wd in weekdays}
            if r.bymonthday is not None:
                cands = {d for d in cands if d.day in r.bymonthday}
            if r.bymonth is not None:
                cands = {d for d in cands if d.month in r.bymonth}
        elif r.freq == "monthly":
            if r.bymonth is not None and start.month not in r.bymonth:
                cands = set()
            else:
                cands = self._month_days(start.year, start.month)
        else:  # yearly：bysetpos 作用在全年已筛出的日期上
            months = r.bymonth if r.bymonth is not None else [r.dtstart.month]
            cands = set()
            for month in months:
                cands |= self._month_days(start.year, month)

        ordered = sorted(cands)
        if r.bysetpos:
            n = len(ordered)
            picked = set()
            for pos in r.bysetpos:
                idx = pos - 1 if pos > 0 else n + pos
                if 0 <= idx < n:
                    picked.add(ordered[idx])
            ordered = sorted(picked)
        return ordered

    # ---- 展开 -----------------------------------------------------------

    def expand(self):
        """扫描周期生成最终序列。结果缓存，重复调用开销为 0。"""
        if self.dates is not None:
            return
        r = self.rule
        raw = []
        # count 作用在最终序列上：覆盖最多删掉 len(exdates)+len(moves) 条，
        # 多扫这么多原始日期即可保证最终序列前 count 条正确。
        need = None
        if r.count is not None:
            need = r.count + len(r.exdates) + len(r.moves)

        k = 0
        periods = 0
        while k < MAX_PERIODS:
            start = self._period_start(k)
            if start is None:
                # 周期起始日超出 9999-12-31，之后所有周期都不可表示。
                # 无 count 时序列在兜底上限处截至，periods 记满上限。
                if r.count is None:
                    periods = MAX_PERIODS
                break
            if r.until is not None and start > r.until:
                break
            for d in self._period_dates(k):
                if d < r.dtstart:
                    continue
                if r.until is not None and d > r.until:
                    continue
                raw.append(d)
            k += 1
            periods = k
            if need is not None and len(raw) >= need:
                break

        excluded = set(r.exdates) | {frm for frm, _ in r.moves}
        added = {to for _, to in r.moves}
        seq = sorted((set(raw) - excluded) | added)
        if r.count is not None:
            seq = seq[: r.count]
        self.dates = seq
        self.periods = periods

    # ---- 查询 -----------------------------------------------------------

    def nth(self, n):
        """最终序列里的第 N 次（1 起）。直接索引，不逐条数。"""
        self.expand()
        total = len(self.dates)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > total:
            raise OutOfRange(f"n={n} total={total}")
        return self.dates[n - 1]

    def next_after(self, when):
        """严格晚于 when 的第一次发生。二分查找定位。"""
        self.expand()
        idx = bisect_right(self.dates, when)
        if idx >= len(self.dates):
            raise NoOccurrence(f"searched={self.periods}")
        return self.dates[idx]
