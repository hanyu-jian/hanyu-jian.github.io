#!/usr/bin/env python3
"""Open-Meteo 小时云量 CSV（UTC）-> data/weather/tokaj_cloud_cover.csv（Budapest 本地时间，每小时一行）。

输入是 Open-Meteo 导出的 CSV（前几行是位置信息，空行之后是 time,cloud_cover (%),... 表头，time 为 UTC）。
输出两列：time（Europe/Budapest 本地时间，YYYY-MM-DD HH:00）、cloud_cover（总云量 %，0-100）。
Imbalance 页面的时间都是 Budapest 本地时间，所以在这里一次转好。
夏令时：转回冬令时那天本地 02:00 出现两次，取平均；转到夏令时那天本地 02:00 不存在（页面那天也没有这个时段）。

用法：python3 py/build/convert_open_meteo_cloud.py <open-meteo.csv> [输出文件]
再次运行同一个输出文件时，新数据覆盖相同的小时，其余小时保留（可以分批追加）。
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BUD = ZoneInfo("Europe/Budapest")
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "weather" / "tokaj_cloud_cover.csv"


def read_open_meteo(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "time"), None)
    if start is None:
        raise SystemExit("no 'time' header row found")
    head = [h.strip() for h in rows[start]]
    col = next((i for i, h in enumerate(head) if h.startswith("cloud_cover") and "(%)" in h and "_" not in h.split("(")[0][len("cloud_cover"):]), None)
    if col is None:
        raise SystemExit("no total cloud_cover (%) column found")
    out = defaultdict(list)
    for r in rows[start + 1:]:
        if len(r) <= col or not r[0].strip() or r[col].strip() == "":
            continue
        utc = datetime.strptime(r[0].strip(), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        out[utc.astimezone(BUD).strftime("%Y-%m-%d %H:00")].append(float(r[col]))
    return {k: sum(v) / len(v) for k, v in out.items()}


def read_existing(path):
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        return {row[0]: float(row[1]) for row in r if len(row) >= 2 and row[1] != ""}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src, dst = sys.argv[1], Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    merged = read_existing(dst)
    new = read_open_meteo(src)
    merged.update(new)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "cloud_cover"])
        for k in sorted(merged):
            v = merged[k]
            w.writerow([k, int(v) if v == int(v) else round(v, 1)])
    print(f"{len(new)} hours read, {len(merged)} hours in {dst}")


if __name__ == "__main__":
    main()
