#!/usr/bin/env python3
"""
EnAppSys 下载脚本 — 匈牙利 aFRR 容量（capacity）up / down，15min
- 与 fetch_enappsys_ida.py 同样用 Montel 官方 Python 包 enappsys（Chart API），凭证只从环境变量读取：
  ENAPPSYS_USER，以及 ENAPPSYS_PASS（或 ENAPPSYS_SECRET）
- 两个方向各一个 code：
    up    hu/elec/ancillary/afrr/summary/up
    down  hu/elec/ancillary/afrr/summary/down
- 接口返回 14 列，只保留 7 列（列名原样、带单位，up / down 各自的名字）：
    WEIGHTED AVERAGE PRICE (HOURLY / DAILY / MONTHLY)、VOLUME (HOURLY / DAILY / MONTHLY)、TOTAL VOLUME
  按名字匹配，对应不上就报错并打出全部列名；按时间合并进
    data/enappsys/afrr/capacity/<up|down>/15min/HU_<年>.csv（按当地时间的年份分文件）
- 时间列 datetime 的格式与 IDA 相同（带偏移的 ISO 时间，秋季回拨无歧义）
- 模式：
    --days-back 3            取 [今天-3 天, 明天)（默认，每日更新用，覆盖事后修正）
    --start/--end YYYY-MM-DD 补数：指定起止日期（含 start，不含 end；过大会由官方包自动分段）
    --direction up|down      只抓一个方向（缺省两个都抓）
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from enappsys import EnAppSys

from fetch_enappsys_ida import TIME_COL, merge, to_frame

CODES = {
    "up": "hu/elec/ancillary/afrr/summary/up",
    "down": "hu/elec/ancillary/afrr/summary/down",
}
OUT_ROOT_DEFAULT = Path("data/enappsys/afrr/capacity")


def pick_columns(df):
    """只留 WAP(hourly/daily/monthly)、Volume(hourly/daily/monthly)、Total volume 七列；对应不上就报错。"""
    want = [("weighted average price", p) for p in ("(hourly)", "(daily)", "(monthly)")]
    want += [("volume", p) for p in ("(hourly)", "(daily)", "(monthly)")] + [("total volume", "")]
    cols = []
    for name, period in want:
        hit = [c for c in df.columns if c != TIME_COL and name in c.lower() and period in c.lower()
               and (name != "volume" or "total" not in c.lower())]
        if len(hit) != 1:
            sys.exit(f"cannot map a single '{name} {period}' column from {list(df.columns)}")
        cols.append(hit[0])
    return df[[TIME_COL] + cols]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD，缺省为明天")
    ap.add_argument("--direction", choices=sorted(CODES), help="缺省 up 和 down 都抓")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT_DEFAULT,
                    help="输出到 <out-root>/<up|down>/15min/HU_<年>.csv")
    a = ap.parse_args(argv)

    user = os.environ.get("ENAPPSYS_USER")
    secret = os.environ.get("ENAPPSYS_PASS") or os.environ.get("ENAPPSYS_SECRET")
    if not user or not secret:
        sys.exit("ENAPPSYS_USER / ENAPPSYS_PASS not set")

    start = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else date.today() - timedelta(days=a.days_back)
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today() + timedelta(days=1)
    client = EnAppSys(user=user, secret=secret)
    for direction in ([a.direction] if a.direction else sorted(CODES)):
        chart = client.chart.get("csv", code=CODES[direction], start_dt=f"{start}T00:00", end_dt=f"{end}T00:00",
                                 resolution="qh", time_zone="CET", currency="EUR")
        frame = to_frame(chart)
        print(f"[{direction}] API columns: {[c for c in frame.columns if c != TIME_COL]}")
        new = pick_columns(frame)
        if new.empty:
            sys.exit(f"[{direction}] no data rows returned for {start} .. {end}")
        out_dir = a.out_root / direction / "15min"
        out_dir.mkdir(parents=True, exist_ok=True)
        for year, part in new.groupby(new[TIME_COL].str[:4]):
            path = out_dir / f"HU_{year}.csv"
            old = pd.read_csv(path, dtype={TIME_COL: str}) if path.exists() else None
            out = merge(old, part)
            out.to_csv(path, index=False)
            print(f"{path}: +{len(part)} rows [{part[TIME_COL].iloc[0]} .. {part[TIME_COL].iloc[-1]}], "
                  f"file now {len(out)} rows")


if __name__ == "__main__":
    main()
