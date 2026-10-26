#!/usr/bin/env python3
"""
EnAppSys 下载脚本 — HUPX ID3 指数（价格 + 成交量，15min）
- 与 fetch_enappsys_ida.py 同样用 Montel 官方 Python 包 enappsys（Chart API，
  code=hu/elec/hupx/indexes/id3/version2），凭证只从环境变量读取：
  ENAPPSYS_USER，以及 ENAPPSYS_PASS（或 ENAPPSYS_SECRET）
- 接着现有的 data/enappsys/ID3/HU_<年>.csv 往下写，保持原表格格式不变：
    制表符分隔，列 Date, ID3, Vol，Date 为当地时间 dd/mm/YYYY HH:MM，行尾 CRLF
  接口返回的列按名字对应到 ID3 / Vol（见 pick_columns）；日前价不放在这里，
  price-intraday.html 和回测都从 data/entsoe/15min/price.csv 的 HU 列读
- 同一个 Date 以新数据为准（覆盖 TSO / 交易所的事后修正）；秋季回拨那一天重复的
  当地时间只保留第一次出现的（与页面和回测读取时 keep first 的做法一致）
- 模式：
    --days-back 3            取 [今天-3 天, 明天)（默认，每日更新用）
    --start/--end YYYY-MM-DD 补数：指定起止日期（含 start，不含 end；过大会由官方包自动分段）
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from enappsys import EnAppSys

from fetch_enappsys_ida import TIME_COL, to_frame

CODE = "hu/elec/hupx/indexes/id3/version2"
OUT_DIR_DEFAULT = Path("data/enappsys/ID3")
DATE_FMT = "%d/%m/%Y %H:%M"
COLS = ["Date", "ID3", "Vol"]


def pick_columns(df):
    """把接口返回的列名对应到 ID3 / Vol；对应不上或有歧义就报错，并把全部列名打出来。"""
    found = {}
    for c in df.columns:
        if c == TIME_COL:
            continue
        low = c.lower()
        if "volume" in low or low.startswith("vol"):
            key = "Vol"
        elif "id3" in low:
            key = "ID3"
        else:
            continue
        found.setdefault(key, []).append(c)
    for key in ("ID3", "Vol"):
        if len(found.get(key, [])) != 1:
            sys.exit(f"cannot map a single '{key}' column from {list(df.columns)}")
    return {k: v[0] for k, v in found.items()}


def to_legacy(frame):
    """to_frame() 的结果 → 现有表格的格式（Date, ID3, Vol）。"""
    cols = pick_columns(frame)
    local = pd.to_datetime(frame[TIME_COL].str[:19])          # 当地时间墙钟，去掉偏移
    out = pd.DataFrame({
        "Date": local,
        "ID3": pd.to_numeric(frame[cols["ID3"]], errors="coerce"),
        "Vol": pd.to_numeric(frame[cols["Vol"]], errors="coerce"),
    })
    out = out.drop_duplicates("Date", keep="first")             # 秋季回拨的重复时间只留第一次
    return out.dropna(subset=["ID3", "Vol"], how="all").reset_index(drop=True)


def read_old(path):
    """读现有文件，保持原文本不变（全部按字符串读）。"""
    if not path.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)[COLS]


def merge(old, new):
    """新数据覆盖同一 Date 的旧行，其余旧行（含回拨日的重复行）原样保留，按时间稳定排序。"""
    new_s = new.assign(Date=new["Date"].dt.strftime(DATE_FMT))
    keep = old[~old["Date"].isin(set(new_s["Date"]))]
    out = pd.concat([keep, new_s.astype(object).where(new_s.notna(), "")], ignore_index=True)
    key = pd.to_datetime(out["Date"], format=DATE_FMT)
    return out.iloc[key.argsort(kind="stable")].reset_index(drop=True)


def write(out, path):
    out.to_csv(path, sep="\t", index=False, lineterminator="\r\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD，缺省为明天")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
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
    frame = to_frame(chart)
    print(f"API columns: {[c for c in frame.columns if c != TIME_COL]}")
    new = to_legacy(frame)
    if new.empty:
        sys.exit(f"no data rows returned for {start} .. {end}")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for year, part in new.groupby(new["Date"].dt.year):
        path = a.out_dir / f"HU_{year}.csv"
        out = merge(read_old(path), part)
        write(out, path)
        print(f"{path}: +{len(part)} rows [{part['Date'].iloc[0]} .. {part['Date'].iloc[-1]}], "
              f"file now {len(out)} rows")


if __name__ == "__main__":
    main()
