#!/usr/bin/env python3
"""命令行入口:python3 main.py rule.json

rule.json 在规则字段之外可以带:
  "note":    说明文字(原样输出到 note= 行)
  "queries": [["nth", 100000], ["next", "2030-05-05"]]
"""

import json
import sys
from pathlib import Path

from runner import run_case


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = Path(argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    note = data.pop("note", "")
    queries = data.pop("queries", [])
    for line in run_case(path.stem, note, data, queries):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
