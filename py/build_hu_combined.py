#!/usr/bin/env python3
"""
把匈牙利（HU）15 分钟数据整合成一张表，供同事通过 GitHub contents API 取用：
  GET https://api.github.com/repos/hanyu-jian/hanyu-jian.github.io/contents/data/combined/HU_2026.csv
  头：Authorization: Bearer <token>、Accept: application/vnd.github.raw+json
来源（都是已有文件，本脚本只读不抓）：
  data/enappsys/IDA/HU_<年>.csv   日前价、IDA1/2/3 价格与成交量、连续交易 QH 指标（原列名）
  data/enappsys/ID3/HU_<年>.csv   ID3 价格、成交量
  data/mavir/imbalance/HU_<年>.csv  System direction [MWh]（MAVIR；1~9 月为修正数据，之后为 preliminary）
  raw_data/<年>/A85.csv           HU 不平衡价（ENTSO-E A85）：2026-10-01 前是 HUF/MWh，之后已是 EUR/MWh
输出：
  data/combined/HU_<年>.csv           全年一张表
约定：
  Date 为匈牙利当地时间（Europe/Budapest 墙钟）YYYY-MM-DD HH:MM，每行 15 分钟；
  秋季回拨那天重复的当地时间只保留第一次出现的；任何来源缺的时段留空。
  不平衡价统一成 EUR/MWh：HUF 的部分用 data/fx/MNB.csv 的 EUR 汇率，取前一个工作日（与
  tokaj_backtest.py 一致）；2026-10-01 起原样。
  年份取 data/mavir/imbalance 下有的年份；--years 可指定。
"""

import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "combined"
EUR_FROM = "2026-10-01"     # A85 的 HU 从这天起已是 EUR/MWh
FX_LAG_DAYS = 1
DATE_FMT = "%Y-%m-%d %H:%M"


def first(s):
    """回拨日重复的时间点只留第一次出现的。"""
    return s[~s.index.duplicated(keep="first")]


def read_ida(year):
    p = ROOT / "data" / "enappsys" / "IDA" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df.index = pd.to_datetime(df.pop("datetime").str[:19])   # 去掉偏移，当地墙钟
    return first(df)


def read_id3(year):
    p = ROOT / "data" / "enappsys" / "ID3" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, sep="\t")
    df.index = pd.to_datetime(df.pop("Date"), format="%d/%m/%Y %H:%M")
    return first(df.rename(columns={"ID3": "ID3 Price (EUR/MWh)", "Vol": "ID3 Volume (MW)"}))


def read_direction(year):
    p = ROOT / "data" / "mavir" / "imbalance" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, usecols=["Date", "System direction [MWh]"])
    df.index = pd.to_datetime(df.pop("Date"), format="%d/%m/%Y %H:%M")
    return first(df.rename(columns={"System direction [MWh]": "System direction (MWh)"}))


def read_imbalance(year):
    p = ROOT / "raw_data" / str(year) / "A85.csv"
    if not p.exists():
        return pd.DataFrame()
    x = pd.read_csv(p, usecols=["Date", "HU"])
    x.index = pd.to_datetime(x.pop("Date"), format="%Y/%m/%d %H:%M")
    price = first(x["HU"].dropna())
    fx = pd.read_csv(ROOT / "data" / "fx" / "MNB.csv", usecols=["Date", "EUR"])
    fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
    fx = fx.drop_duplicates("Date").set_index("Date")["EUR"].sort_index()
    day = price.index.normalize() - pd.Timedelta(days=FX_LAG_DAYS)
    rate = fx.reindex(day, method="ffill").to_numpy()
    eur = (price / rate).where(price.index < pd.Timestamp(EUR_FROM), price)
    return eur.rename("Imbalance Price (EUR/MWh)").to_frame()


def build(year):
    parts = [f(year) for f in (read_ida, read_id3, read_direction, read_imbalance)]
    df = pd.concat([p for p in parts if not p.empty], axis=1).sort_index()
    df = df[df.index.year == year]
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(9)
    return df.dropna(how="all")


def write(df, path):
    out = df.copy()
    out.index = out.index.strftime(DATE_FMT)
    out.index.name = "Date"
    out.to_csv(path, lineterminator="\r\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    years = a.years or sorted(
        int(m.group(1)) for p in (ROOT / "data" / "mavir" / "imbalance").glob("HU_*.csv")
        if (m := re.fullmatch(r"HU_(\d{4})\.csv", p.name)))
    for year in years:
        df = build(year)
        write(df, OUT / f"HU_{year}.csv")
        print(f"HU_{year}: {len(df)} rows, {df.index.min()} .. {df.index.max()}")


if __name__ == "__main__":
    main()
