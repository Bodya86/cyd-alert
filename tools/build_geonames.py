#!/usr/bin/env python3
"""Компактний газетир населених пунктів України з дампу GeoNames (CC BY 4.0) для історичної моделі.

  python3 tools/build_geonames.py path/to/UA.txt     # UA.txt з архіву UA.zip (geonames.org, розділ export/dump)
→ data/geonames_ua.json: [[імена кирилицею], lat, lon, область (індекс locations.json), населення], за спаданням населення.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "geonames_ua.json")
GN_ADM1 = {"11": 0, "24": 1, "23": 2, "04": 3, "05": 4, "27": 5, "25": 6, "26": 7, "06": 8, "12": 9, "13": 10,
           "10": 11, "14": 12, "15": 13, "16": 14, "17": 15, "18": 16, "19": 17, "20": 18, "21": 19, "22": 20,
           "07": 21, "08": 22, "09": 23, "01": 24, "03": 25, "02": 26}
APOS = ("\u2019", "\u02bc")


def norm(s):
    s = s.strip().lower()
    for a in APOS:
        s = s.replace(a, "'")
    return s


def main(path):
    out = []
    for line in open(path, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if len(f) < 15 or f[6] != "P" or not f[7].startswith("PPL"):
            continue
        names = {norm(n) for n in f[3].split(",")}
        names = sorted(n for n in names if re.fullmatch(r"[а-яіїєґё'\-]{4,}", n))
        if names:
            out.append([names, round(float(f[4]), 3), round(float(f[5]), 3), GN_ADM1.get(f[10], -1), int(f[14] or 0)])
    out.sort(key=lambda e: -e[4])
    with open(OUT, "w", encoding="utf-8") as fo:
        json.dump(out, fo, ensure_ascii=False, separators=(",", ":"))
    print(len(out), "→", OUT, os.path.getsize(OUT), "B")


if __name__ == "__main__":
    main(sys.argv[1])
