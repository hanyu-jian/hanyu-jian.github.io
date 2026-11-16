#!/usr/bin/env python3
"""Open-Meteo 小时云量 CSV（UTC）-> data/weather/tokaj_cloud_cover.csv（Budapest 本地时间，每小时一行）。

输入是 Open-Meteo 导出的 CSV（前几行是位置信息，空行之后是 time,cloud_cover (%),cloud_cover_low (%),... 表头，time 为 UTC）。
输出五列：time（Europe/Budapest 本地时间，YYYY-MM-DD HH:00）、cloud_cover（总云量 %）、cloud_cover_low、cloud_cover_mid、cloud_cover_high（低 / 中 / 高云量 %）。
Imbalance 页面的时间都是 Budapest 本地时间，所以在这里一次转好。
夏令时：转回冬令时那天本地 02:00 出现两次，取平均；转到夏令时那天本地 02:00 不存在（页面那天也没有这个时段）。

用法：python3 py/build/convert_open_meteo_cloud.py <open-meteo.csv> [输出文件]
再次运行同一个输出文件时，新数据覆盖相同的小时，其余小时保留（可以分批追加）；输入里没有的列留空。
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BUD = ZoneInfo("Europe/Budapest")
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "weather" / "tokaj_cloud_cover.csv"
# 输出列名 -> Open-Meteo 表头
COLUMNS = {
    "cloud_cover": "cloud_cover (%)",
    "cloud_cover_low": "cloud_cover_low (%)",
    "cloud_cover_mid": "cloud_cover_mid (%)",
    "cloud_cover_high": "cloud_cover_high (%)",
}


def fmt(v):
    return "" if v is None else (str(int(v)) if v == int(v) else str(round(v, 1)))


def read_open_meteo(path):
    """-> {本地小时: {输出列: 平均值}}，输入里没有的列不出现。"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "time"), None)
    if start is None:
        raise SystemExit("no 'time' header row found")
    head = [h.strip() for h in rows[start]]
    cols = {name: head.index(src) for name, src in COLUMNS.items() if src in head}
    if "cloud_cover" not in cols:
        raise SystemExit("no total cloud_cover (%) column found")
    acc = defaultdict(lambda: defaultdict(list))
    for r in rows[start + 1:]:
        if not r or not r[0].strip():
            continue
        utc = datetime.strptime(r[0].strip(), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        key = utc.astimezone(BUD).strftime("%Y-%m-%d %H:00")
        for name, i in cols.items():
            if len(r) > i and r[i].strip() != "":
                acc[key][name].append(float(r[i]))
    return {k: {n: sum(v) / len(v) for n, v in d.items()} for k, d in acc.items()}


def read_existing(path):
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        out = {}
        for row in r:
            out[row["time"]] = {n: float(row[n]) for n in COLUMNS if row.get(n) not in (None, "")}
        return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src, dst = sys.argv[1], Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    merged = read_existing(dst)
    new = read_open_meteo(src)
    for k, d in new.items():
        merged.setdefault(k, {}).update(d)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["time", *COLUMNS])
        for k in sorted(merged):
            w.writerow([k, *(fmt(merged[k].get(n)) for n in COLUMNS)])
    print(f"{len(new)} hours read, {len(merged)} hours in {dst}")


if __name__ == "__main__":
    main()
