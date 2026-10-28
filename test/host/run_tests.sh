#!/bin/bash
# Збирає та запускає тести аналізатора на ПК (потрібен g++)
set -e
cd "$(dirname "$0")"
python3 ../../tools/gen_data.py
python3 - <<'PY'
import json
L = {"green": "LVL_GREEN", "yellow": "LVL_YELLOW", "orange": "LVL_ORANGE", "red": "LVL_RED"}
b = lambda v: -1 if v is None else int(v)
out = ["// згенеровано з test/analyzer_cases.json (run_tests.sh)", "struct Case { const char* t; const char* d; Level l; int near, far, ow; const char* ch; };",
       "static const Case CASES[] = {"]
for c in json.load(open("../analyzer_cases.json", encoding="utf-8")):
    out.append("  {%s, %s, %s, %d, %d, %d, %s}," % (json.dumps(c["t"], ensure_ascii=False), json.dumps(c["d"]), L[c["lvl"]],
                                               b(c.get("near")), b(c.get("far")), b(c.get("ow")), json.dumps(c.get("ch", ""))))
out.append("};")
open("cases_gen.h", "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
${CXX:-g++} -std=gnu++17 -O1 -I. -I../../include -o /tmp/cyd_test test_main.cpp ../../src/analyzer.cpp ../../src/textutil.cpp ../../src/outage_calc.cpp
/tmp/cyd_test "$@"
