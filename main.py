"""命令行驱动：读规则 JSON，展开并执行 nth / next 查询，按约定格式输出。

用法：
    python3 main.py samples/case-1.rule.json           # 跑单个规则
    python3 main.py --samples                          # 跑 samples/ 下全部样例
    python3 main.py rule.json --nth 100 --next 2030-01-01   # 自定义查询

样例的 note 与查询不在规则 JSON 里，内置在 SAMPLE_CASES 中；
未知规则文件可通过 --note/--nth/--next 指定。
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rrule_engine import Engine, NoOccurrence, OutOfRange, Rule, RuleError

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"

SAMPLE_CASES = {
    "case-1": {
        "note": "每天一次 + 大 N（第 100000 次）",
        "queries": [("nth", 100000), ("nth", 1), ("next", "2030-05-05")],
    },
    "case-2": {
        "note": "每周一三五 + COUNT 与 UNTIL（截止当天算在内）",
        "queries": [("nth", 6), ("nth", 7), ("next", "2026-01-09")],
    },
    "case-3": {
        "note": "每月 31 号：没有 31 号的月份跳过（2、4 月都没有）",
        "queries": [("nth", 4), ("next", "2026-01-31")],
    },
    "case-4": {
        "note": "每年 2 月 29 日：平年没有这一天就跳过",
        "queries": [("nth", 2), ("next", "2024-02-29")],
    },
    "case-5": {
        "note": "每月第 5 个星期五：只有 4 个星期五的月份跳过",
        "queries": [("nth", 3), ("next", "2026-05-29")],
    },
    "case-6": {
        "note": "取消与挪动：序号按最终序列算，取消后后面的日期顶上来",
        "queries": [("nth", 1), ("nth", 3), ("next", "2026-01-05")],
    },
    "case-7": {
        "note": "兜底：每月 2 月 30 日，永远排不出来",
        "queries": [],
    },
    "case-8": {
        "note": "每两个月 + 每月 1 号与 15 号 + 第 2 个",
        "queries": [("nth", 3), ("next", "2026-05-15")],
    },
}


def render(case_name, note, engine, queries):
    """生成输出文本行。整条规则无解时只输出 case/note 头加 error 行。"""
    engine.expand()
    lines = [f"case={case_name}"]
    if note:
        lines.append(f"note={note}")
    if not engine.dates:
        lines.append(f"error,{NoOccurrence.code},searched={engine.periods}")
        return lines

    lines.append(f"total={len(engine.dates)}")
    lines.append(f"first={engine.dates[0].isoformat()}")
    lines.append(f"last={engine.dates[-1].isoformat()}")
    lines.append(f"periods={engine.periods}")
    for kind, arg in queries:
        if kind == "nth":
            try:
                result = engine.nth(arg).isoformat()
                lines.append(f"query=nth,{arg},{result}")
            except OutOfRange as exc:
                lines.append(f"query=nth,{arg},error,{exc.code},{exc.detail}")
        else:
            try:
                result = engine.next_after(date.fromisoformat(arg)).isoformat()
                lines.append(f"query=next,{arg},{result}")
            except NoOccurrence as exc:
                lines.append(f"query=next,{arg},error,{exc.code},{exc.detail}")
    return lines


def run_rule(spec, case_name, note, queries):
    try:
        rule = Rule(spec)
    except RuleError as exc:
        lines = [f"case={case_name}"]
        if note:
            lines.append(f"note={note}")
        lines.append(f"error,{exc.code},{exc.detail}")
        return lines
    return render(case_name, note, Engine(rule), queries)


def run_file(path, note=None, queries=None):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    name = Path(path).name
    case_name = name[:-len(".rule.json")] if name.endswith(".rule.json") else Path(name).stem
    sample = SAMPLE_CASES.get(case_name, {})
    if note is None:
        note = sample.get("note")
    if queries is None:
        queries = sample.get("queries", [])
    return run_rule(spec, case_name, note, queries)


def main(argv=None):
    parser = argparse.ArgumentParser(description="日历重复规则引擎")
    parser.add_argument("rule", nargs="?", help="规则 JSON 文件")
    parser.add_argument("--samples", action="store_true", help="跑 samples/ 下全部样例")
    parser.add_argument("--note", help="note 行文本")
    parser.add_argument("--nth", type=int, action="append", default=[],
                        help="追加一个 nth 查询（可多次）")
    parser.add_argument("--next", dest="next_", action="append", default=[],
                        metavar="DATE", help="追加一个 next 查询（可多次）")
    args = parser.parse_args(argv)

    if args.samples:
        out = []
        for rule_path in sorted(SAMPLES_DIR.glob("*.rule.json")):
            out.extend(run_file(rule_path))
        print("\n".join(out))
        return 0

    if not args.rule:
        parser.error("需要规则文件或 --samples")

    queries = None
    if args.nth or args.next_:
        queries = [("nth", n) for n in args.nth]
        queries += [("next", d) for d in args.next_]
    print("\n".join(run_file(args.rule, note=args.note, queries=queries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
