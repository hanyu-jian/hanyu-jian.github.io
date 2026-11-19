#!/usr/bin/env python3
"""几个天气模型的云量取平均 -> data/weather/cloud_<id>_<来源>.csv（默认 id = avg），并更新 data/weather/models.json。

每个来源（day1 / hist）、每一列（<层>_<区域>）、每个小时：取各模型该小时的值的平均数（只用**所有**参与的模型都有值的小时）。
平均数不是真实的云量，而是几个模型的“共识分数”，但按 2025–2026 年的对比，它和遮光程度的相关性比任何单个模型都高（见 README）。
某个模型没有某个来源（比如只有 day1）时，那个来源就只用有它的模型；一个来源少于 2 个模型时不生成。
新增或更新了模型的数据之后重新运行一次。

用法：python3 py/build/average_cloud_models.py [--models ifs025 arpege_europe icon_eu] [--id avg] [--label "显示名"]
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import convert_open_meteo_cloud as conv  # noqa: E402

DEFAULT_MODELS = ["ifs025", "arpege_europe", "icon_eu"]


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        head = next(r)
        rows = {row[0]: row[1:] for row in r if row}
    return head[1:], rows


def average(model_files):
    """[(列名, {时间: [值...]})...] -> (列名, {时间: [平均...]})：只用所有文件都有的列和小时，值为空的格子该小时不算。"""
    cols = [c for c in model_files[0][0] if all(c in h for h, _ in model_files)]
    times = sorted(set.intersection(*[set(rows) for _, rows in model_files]))
    out = {}
    for t in times:
        vals = []
        for c in cols:
            xs = []
            for h, rows in model_files:
                v = rows[t][h.index(c)]
                if v != "":
                    xs.append(float(v))
            vals.append("" if len(xs) < len(model_files) else conv.fmt(round(sum(xs) / len(xs), 1)))
        out[t] = vals
    return cols, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--id", default="avg")
    ap.add_argument("--label", default=None)
    ap.add_argument("--dir", default=str(conv.OUT_DIR))
    a = ap.parse_args(argv)
    d = Path(a.dir)
    made = []
    for source in conv.SOURCES:
        files = [(m, d / f"cloud_{m}_{source}.csv") for m in a.models if (d / f"cloud_{m}_{source}.csv").exists()]
        if len(files) < 2:
            print(f"{source}: fewer than 2 models, skipped")
            continue
        cols, rows = average([read(f) for _, f in files])
        dst = d / f"cloud_{a.id}_{source}.csv"
        with open(dst, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["time", *cols])
            for t in sorted(rows):
                w.writerow([t, *rows[t]])
        made.append(source)
        print(f"{source}: average of {', '.join(m for m, _ in files)} -> {dst} ({len(rows)} hours, {len(cols)} columns)")
    if made:
        label = a.label or "Average of the models (" + ", ".join(conv.KNOWN_MODELS.get(m, m) for m in a.models) + ")"
        conv.update_index(a.id, label, d / "models.json")


if __name__ == "__main__":
    main()
