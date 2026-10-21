#!/usr/bin/env python3
"""
EnAppSys 下载脚本 — HUPX IDA1/IDA2/IDA3（价格 + 成交量，15min）
- 使用 Montel 官方 Python 包 enappsys（Chart API，code=hu/elec/hupxauctionid/idsummary/version2）
- 凭证只从环境变量读取：ENAPPSYS_USER，以及 ENAPPSYS_PASS（或 ENAPPSYS_SECRET）；
  官方包会在日志里把 secret 屏蔽
- 返回的列原样保留（列名带单位，如 "IDA1 Price (EUR/MWh)"），按时间合并进
  private_data/enappsys/hupx_ida_qh.csv（该目录在 .assetsignore 中，不对外发布）
- 时间列 datetime：请求 time_zone=CET，官方包按行序判定秋季回拨的重复小时，
  文件里存带偏移的 ISO 时间（如 2026-10-25T02:15:00+01:00），无歧义
- 模式：
    --days-back 3            取 [今天-3 天, 明天)（默认，每日更新用，覆盖 TSO/交易所的事后修正）
    --start/--end YYYY-MM-DD 补数：指定起止日期（含 start，不含 end 当天之后；过大会由官方包自动分段）
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from enappsys import EnAppSys

CODE = "hu/elec/hupxauctionid/idsummary/version2"
OUT_DEFAULT = Path("private_data/enappsys/hupx_ida_qh.csv")
TIME_COL = "datetime"


def to_frame(chart):
    """ChartCSV → DataFrame，首列为带偏移的 ISO 时间文本。"""
    df = chart.to_df(unit_in_columns=True)
    df = df.dropna(how="all")
    df.insert(0, TIME_COL, [t.isoformat() for t in df.index])
    return df.reset_index(drop=True)


def merge(old, new):
    if old is None or old.empty:
        out = new
    else:
        cols = new.columns.tolist() + [c for c in old.columns if c not in new.columns]
        out = pd.concat([old.reindex(columns=cols), new.reindex(columns=cols)])
    out = out[~out[TIME_COL].duplicated(keep="last")]
    key = pd.to_datetime(out[TIME_COL], utc=True)
    return out.assign(_k=key).sort_values("_k").drop(columns="_k").reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD，缺省为明天")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    a = ap.parse_args(argv)

    user = os.environ.get("ENAPPSYS_USER")
    secret = os.environ.get("ENAPPSYS_PASS") or os.environ.get("ENAPPSYS_SECRET")
    if not user or not secret:
        sys.exit("ENAPPSYS_USER / ENAPPSYS_PASS not set")

    start = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else date.today() - timedelta(days=a.days_back)
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today() + timedelta(days=1)
    client = EnAppSys(user=user, secret=secret)
    chart = client.chart.get("csv", code=CODE, start_dt=f"{start}T00:00", end_dt=f"{end}T00:00",
                             resolution="qh", time_zone="CET", currency="EUR")
    new = to_frame(chart)
    if new.empty:
        sys.exit(f"no data rows returned for {start} .. {end}")
    old = pd.read_csv(a.out, dtype={TIME_COL: str}) if a.out.exists() else None
    out = merge(old, new)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    print(f"downloaded {len(new)} rows [{new[TIME_COL].iloc[0]} .. {new[TIME_COL].iloc[-1]}]; "
          f"file now {len(out)} rows, columns: {list(out.columns)}")


if __name__ == "__main__":
    main()
