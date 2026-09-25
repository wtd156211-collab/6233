#!/usr/bin/env python3
"""跑 samples/ 下全部用例。

默认把生成的输出打印到 stdout;加 --check 则与 *.expected.txt 对比。
"""

import difflib
import json
import sys
from pathlib import Path

from runner import run_case

SAMPLES = Path(__file__).parent / "samples"

# 每个样例的说明文字与查询(规则 JSON 里不带这两样,属于用例定义的一部分)
CASES = {
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


def generate(name: str) -> str:
    rule = json.loads((SAMPLES / f"{name}.rule.json").read_text(encoding="utf-8"))
    meta = CASES[name]
    lines = run_case(name, meta["note"], rule, meta["queries"])
    return "\n".join(lines) + "\n"


def main(argv):
    check = "--check" in argv
    ok = True
    for name in sorted(CASES):
        output = generate(name)
        if not check:
            print(output, end="")
            continue
        expected = (SAMPLES / f"{name}.expected.txt").read_text(encoding="utf-8")
        if output != expected:
            ok = False
            print(f"--- {name} 与期望不一致 ---")
            diff = difflib.unified_diff(
                expected.splitlines(), output.splitlines(),
                fromfile="expected", tofile="actual", lineterm="",
            )
            print("\n".join(diff))
    if check:
        print("全部通过" if ok else "存在不一致")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
