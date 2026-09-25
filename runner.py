"""把引擎结果格式化成 README 约定的输出。"""

from __future__ import annotations

from rrule_engine import (
    Engine,
    NoOccurrence,
    OutOfRange,
    RuleError,
    _parse_date,
)


def run_case(case_name: str, note: str, rule: dict, queries: list) -> list:
    """返回该用例的输出行。queries 形如 [("nth", 100000), ("next", "2030-05-05")]。"""
    lines = [f"case={case_name}", f"note={note}"]
    try:
        engine = Engine(rule)
        result = engine.expand()
    except RuleError as e:
        lines.append(f"error,{e.code},{e.message}")
        return lines
    if result.total == 0:
        lines.append(f"error,NO_OCCURRENCE,searched={result.periods}")
        return lines
    lines.append(f"total={result.total}")
    lines.append(f"first={result.first.isoformat()}")
    lines.append(f"last={result.last.isoformat()}")
    lines.append(f"periods={result.periods}")
    for kind, arg in queries:
        if kind == "nth":
            try:
                d = engine.nth(int(arg))
                lines.append(f"query=nth,{arg},{d.isoformat()}")
            except OutOfRange as e:
                lines.append(f"query=nth,{arg},error,OUT_OF_RANGE,{e.message}")
        elif kind == "next":
            t = _parse_date(str(arg))
            if t is None:
                lines.append(f"query=next,{arg},error,INVALID_RULE,bad date {arg}")
                continue
            try:
                d = engine.next_after(t)
                lines.append(f"query=next,{arg},{d.isoformat()}")
            except NoOccurrence as e:
                lines.append(f"query=next,{arg},error,NO_OCCURRENCE,{e.message}")
    return lines
