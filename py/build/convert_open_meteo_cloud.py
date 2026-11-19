#!/usr/bin/env python3
"""Open-Meteo 多位置小时云量 CSV（UTC）-> data/weather/cloud_<模型>_<来源>.csv（Budapest 本地时间，每小时一行），并更新 data/weather/models.json。

模型 id（页面里 Cloud 区的 Cloud model 下拉用它）：ifs9km = ECMWF IFS HRES 9km，ifs025 = ECMWF IFS 0.25°，aifs025 = ECMWF AIFS 0.25°，gfs、icon_eu、icon_global、arpege_europe、gem_global、ukmo……（见 KNOWN_MODELS，也可以自己起 id，用 --label 写显示名）。
一份导出里可以有多个模型（见下面的用法）；同一个模型的所有数据（含不同年份）都要用同一个模型导出，不要混用。

来源 source 有两种（页面里分别用于 DA 和 IDC 的云量策略）：
  day1  Previous Runs API 的 cloud_cover_previous_day1：提前约 24 小时发布的预报（DA 用，只有总云量）
  hist  Historical Forecast API：每次模型运行最前面几小时拼成的序列，非常接近事后实际（IDC 用，偏乐观；有总 / 低 / 中 / 高云量）

输入 CSV 是 Open-Meteo 多位置导出：开头是 location_id,latitude,... 的位置表，空行后是 location_id,time,<变量...> 数据，时区 GMT。
位置 -> 区域：location_id 0 = west（Putnok、Serenyfalva，坐标 48.29831, 20.42644），location_id 1 = east（Sajokaza、Szuhakálló 1、Szuhakálló 2，坐标 48.27581, 20.64581）；其余 location_id 忽略。
（这两个坐标在 0.25° 的网格下落在两个不同的格子里（48.25°N 20.50°E 和 20.75°E）；换别的模型时请核对两个坐标确实落在两个不同的格子，各站点在同一个区域里的数据才会相同。）

输出列：time（Europe/Budapest 本地时间 YYYY-MM-DD HH:00），以及 <层>_<区域>，层 = cloud_cover / cloud_cover_low / cloud_cover_mid / cloud_cover_high。
变量名里的 _previous_day1 和模型后缀会去掉，层按名字匹配（Open-Meteo 的列顺序是 总、低、高、中）。
夏令时：转回冬令时那天本地 02:00 出现两次，取平均；转到夏令时那天本地 02:00 不存在。
再次运行同一个输出文件时，新数据覆盖相同的小时，其余小时和列保留（可以分批追加）。

一份导出里有多个模型时（Open-Meteo 页面勾了几个模型，列名带模型后缀），按模型 id 自动选列（KNOWN_COLUMNS），别的模型用 --column <列名里的模型后缀> 指定；同一份导出可以对每个模型各运行一次。

用法：python3 py/build/convert_open_meteo_cloud.py <模型 id> <day1|hist> <open-meteo.csv> [输出文件] [--label "显示名"] [--column <模型后缀>]
"""
import csv
import json
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
KNOWN_MODELS = {
    "ifs9km": "ECMWF IFS HRES 9km", "ifs025": "ECMWF IFS 0.25°", "aifs025": "ECMWF AIFS 0.25°", "gfs": "NCEP GFS", "icon_eu": "DWD ICON EU",
    "icon_global": "DWD ICON Global", "icon_d2": "DWD ICON D2", "arpege_europe": "Météo-France ARPEGE Europe", "arpege_world": "Météo-France ARPEGE World",
    "gem_global": "GEM Global", "ukmo_global": "UK Met Office Global 10km", "jma_gsm": "JMA GSM", "cma_grapes": "CMA GRAPES Global",
}
INDEX = OUT_DIR / "models.json"


def fmt(v):
    return "" if v is None else (str(int(v)) if v == int(v) else str(round(v, 1)))


LAYER_RE = re.compile(r"^(cloud_cover(?:_low|_mid|_high)?)(?:_previous_day\d+)?(?:_([a-z0-9_]+))?$")
# 我们的模型 id -> Open-Meteo 列名里的模型后缀（一份导出里有多个模型时，按它选列；其它模型用 --column <后缀> 指定）
KNOWN_COLUMNS = {"ifs9km": "ecmwf_ifs", "ifs025": "ecmwf_ifs025", "aifs025": "ecmwf_aifs025", "arpege_europe": "meteofrance_arpege_europe", "icon_eu": "dwd_icon_eu"}


def parse_header(header):
    """'cloud_cover_low_previous_day1_ecmwf_ifs025 (%)' -> ('cloud_cover_low', 'ecmwf_ifs025')；没有模型后缀时第二项是 None；其它变量返回 None。"""
    m = LAYER_RE.match(header.split("(")[0].strip())
    return (m.group(1), m.group(2)) if m else None


def layer_of(header):
    p = parse_header(header)
    return p[0] if p else None


def read_open_meteo(path, column=None):
    """-> {本地小时: {'<层>_<区域>': 平均值}}"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    start = next((i for i, r in enumerate(rows) if len(r) > 1 and r[0].strip() == "location_id" and r[1].strip() == "time"), None)
    if start is None:
        raise SystemExit("no 'location_id,time,...' data header found (is this a multi-location export?)")
    parsed = {i: parse_header(h) for i, h in enumerate(rows[start]) if i > 1 and parse_header(h)}
    suffixes = sorted({sfx for _, sfx in parsed.values() if sfx})
    if len(suffixes) > 1:                                         # several models in one export: pick the one asked for
        if column not in suffixes:
            raise SystemExit(f"this export has several models ({', '.join(suffixes)}): say which one with --column <suffix>" + (f" (not found: {column})" if column else ""))
        parsed = {i: v for i, v in parsed.items() if v[1] == column}
    cols = {i: layer for i, (layer, _) in parsed.items()}
    if "cloud_cover" not in cols.values():
        raise SystemExit("no total cloud cover column found")
    if len(set(cols.values())) != len(cols):
        raise SystemExit("the same cloud layer appears more than once: export one model only")
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


def update_index(model, label, index_path=None):
    """data/weather/models.json：每个模型有哪些来源、每个来源有哪些云层、数据从哪天到哪天（由输出文件算出来）。"""
    index_path = Path(index_path or INDEX)
    entry = {"id": model, "label": label}
    for source in SOURCES:
        f = index_path.parent / f"cloud_{model}_{source}.csv"
        if not f.exists():
            continue
        with open(f, newline="", encoding="utf-8") as fh:
            r = csv.reader(fh)
            head = next(r)
            times = [row[0] for row in r if row]
        layers = []
        for layer in LAYERS:
            if all(f"{layer}_{a}" in head for a in AREAS.values()):
                layers.append(layer)
        entry[source] = {"layers": layers, "from": times[0][:10] if times else None, "to": times[-1][:10] if times else None}
    idx = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"models": []}
    idx["models"] = [m for m in idx["models"] if m["id"] != model] + [entry]
    idx["models"].sort(key=lambda m: m["id"])
    index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return entry


def main():
    args = sys.argv[1:]
    label = None
    if "--label" in args:
        i = args.index("--label"); label = args[i + 1]; del args[i:i + 2]
    column = None
    if "--column" in args:
        i = args.index("--column"); column = args[i + 1]; del args[i:i + 2]
    if len(args) < 3 or args[1] not in SOURCES or not re.fullmatch(r"[a-z0-9_]+", args[0]):
        raise SystemExit(__doc__)
    model, source, src = args[0], args[1], args[2]
    dst = Path(args[3]) if len(args) > 3 else OUT_DIR / f"cloud_{model}_{source}.csv"
    merged = read_existing(dst)
    new = read_open_meteo(src, column or KNOWN_COLUMNS.get(model))
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
    if dst.parent == OUT_DIR:
        update_index(model, label or KNOWN_MODELS.get(model, model))


if __name__ == "__main__":
    main()
