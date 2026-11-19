#!/usr/bin/env python3
"""Open-Meteo 多位置小时云量 CSV（UTC）-> data/weather/cloud_<source>.csv（Budapest 本地时间，每小时一行）。

source 有两种（页面里分别用于 DA 和 IDC 的云量策略）：
  day1  Previous Runs API 的 cloud_cover_previous_day1：提前约 24 小时发布的预报（DA 用，只有总云量）
  hist  Historical Forecast API：每次模型运行最前面几小时拼成的序列，非常接近事后实际（IDC 用，偏乐观；有总 / 低 / 中 / 高云量）
两份都用 ECMWF IFS HRES 9km、GMT。

输入 CSV 是 Open-Meteo 多位置导出：开头是 location_id,latitude,... 的位置表，空行后是 location_id,time,<变量...> 数据。
位置 -> 区域：location_id 0 = west（Putnok、Serenyfalva，坐标 48.29831, 20.42644），location_id 1 = east（Sajokaza、Szuhakálló 1、Szuhakálló 2，坐标 48.27581, 20.64581）。
IFS 9km 的网格下 Sajokaza / Szuhakálló 附近的几个坐标落在同一个格子里，数据逐小时相同，所以只用两个坐标；其余 location_id 忽略。

输出列：time（Europe/Budapest 本地时间 YYYY-MM-DD HH:00），以及 <层>_<区域>，层 = cloud_cover / cloud_cover_low / cloud_cover_mid / cloud_cover_high。
变量名里的 _previous_day1 后缀会去掉，层按名字匹配（Open-Meteo 的列顺序是 总、低、高、中）。
夏令时：转回冬令时那天本地 02:00 出现两次，取平均；转到夏令时那天本地 02:00 不存在。
再次运行同一个输出文件时，新数据覆盖相同的小时，其余小时和列保留（可以分批追加）。

用法：python3 py/build/convert_open_meteo_cloud.py <day1|hist> <open-meteo.csv> [输出文件]
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BUD = ZoneInfo("Europe/Budapest")
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "weather"
AREAS = {"0": "west", "1": "east"}
LAYERS = ["cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"]
SOURCES = ("day1", "hist")


def fmt(v):
    return "" if v is None else (str(int(v)) if v == int(v) else str(round(v, 1)))


LAYER_RE = re.compile(r"^(cloud_cover(?:_low|_mid|_high)?)(?:_previous_day\d+)?(?:_[a-z0-9_]+)?$")


def layer_of(header):
    """'cloud_cover_low_previous_day1 (%)' -> 'cloud_cover_low'；带模型后缀的列名（'cloud_cover_ecmwf_ifs025 (%)'）也认；其它变量返回 None。"""
    m = LAYER_RE.match(header.split("(")[0].strip())
    return m.group(1) if m else None


def read_open_meteo(path):
    """-> {本地小时: {'<层>_<区域>': 平均值}}"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    start = next((i for i, r in enumerate(rows) if len(r) > 1 and r[0].strip() == "location_id" and r[1].strip() == "time"), None)
    if start is None:
        raise SystemExit("no 'location_id,time,...' data header found (is this a multi-location export?)")
    cols = {i: layer_of(h) for i, h in enumerate(rows[start]) if i > 1 and layer_of(h)}
    if "cloud_cover" not in cols.values():
        raise SystemExit("no total cloud cover column found")
    if len(set(cols.values())) != len(cols):
        raise SystemExit("the same cloud layer appears more than once (several models in one export?): export one model only, e.g. ECMWF IFS 0.25 deg")
    acc = defaultdict(lambda: defaultdict(list))
    for r in rows[start + 1:]:
        if len(r) < 3 or not r[1].strip() or r[0].strip() not in AREAS:
            continue
        utc = datetime.strptime(r[1].strip(), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        key = utc.astimezone(BUD).strftime("%Y-%m-%d %H:00")
        for i, layer in cols.items():
            if len(r) > i and r[i].strip() != "":
                acc[key][f"{layer}_{AREAS[r[0].strip()]}"].append(float(r[i]))
    return {k: {n: sum(v) / len(v) for n, v in d.items()} for k, d in acc.items()}


def read_existing(path):
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        out = {}
        for row in csv.DictReader(f):
            out[row["time"]] = {n: float(v) for n, v in row.items() if n != "time" and v not in (None, "")}
        return out


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in SOURCES:
        raise SystemExit(__doc__)
    source, src = sys.argv[1], sys.argv[2]
    dst = Path(sys.argv[3]) if len(sys.argv) > 3 else OUT_DIR / f"cloud_{source}.csv"
    merged = read_existing(dst)
    new = read_open_meteo(src)
    for k, d in new.items():
        merged.setdefault(k, {}).update(d)
    names = [f"{layer}_{area}" for layer in LAYERS for area in AREAS.values() if any(f"{layer}_{area}" in d for d in merged.values())]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["time", *names])
        for k in sorted(merged):
            w.writerow([k, *(fmt(merged[k].get(n)) for n in names)])
    print(f"{len(new)} hours read, {len(merged)} hours, {len(names)} columns in {dst}")


if __name__ == "__main__":
    main()
