#!/usr/bin/env python3
"""
把匈牙利（HU）15 分钟数据整合成一张表，供同事通过 GitHub contents API 取用。
  表放在只含这一个文件的私有仓库 hanyu-jian/hu-market-data（由 build_combined.yml 推送），同事的
  token 只授权那个仓库：
  GET https://api.github.com/repos/hanyu-jian/hu-market-data/contents/HU_2026.csv
  头：Authorization: Bearer <token>、Accept: application/vnd.github.raw+json
来源（都是已有文件，本脚本只读不抓）：
  data/enappsys/IDA/HU_<年>.csv   日前价、IDA1/2/3 价格与成交量、连续交易 QH 指标（原列名）
  data/enappsys/ID3/HU_<年>.csv   ID3 价格、成交量
  data/mavir/imbalance/HU_<年>.csv  System direction [MWh]（MAVIR；1~9 月为修正数据，之后为 preliminary）
  raw_data/<年>/A85.csv           HU 不平衡价（ENTSO-E A85）：2026-10-01 前是 HUF/MWh，之后已是 EUR/MWh
输出：
  <out>/HU_<年>.csv                   全年一张表（--out 指定，默认 data/combined）
约定：
  Date 为匈牙利当地时间（Europe/Budapest 墙钟）YYYY-MM-DD HH:MM，UTC 为同一时刻的 UTC 时间，每行 15 分钟；
  任何来源缺的时段留空。夏令时开始那天跳过 02:00~02:45（少 4 行，共 92 行）；
  秋季回拨那天 02:00~02:45 出现两遍（Date 重复，UTC 不同，共 100 行），以 UTC 列区分、对齐各来源：
    IDA 自带 +02:00 / +01:00 偏移，两遍都是准确的；
    A85、ID3、MAVIR 的文件里重复的当地时间被去重过，只剩一遍，按它们抓取脚本的去重规则归属：
    A85 留的是第二遍（UTC+1），ID3、MAVIR 留的是第一遍（UTC+2）；某来源的文件里如果出现了两遍，则按先后顺序当第一、二遍。
    被去掉的那一遍留空，不会再把不同遍的数据拼进同一行。
  不平衡价统一成 EUR/MWh：HUF 的部分用 data/fx/MNB.csv 的 EUR 汇率，取前一个工作日（与
  analysis/tokaj_imbalance_price_calculation.py 一致）；2026-10-01 起原样。
  年份取 MAVIR / IDA / ID3 文件里有的年份（MAVIR 从 2025 年开始，更早的年份 System direction 一列留空）；--years 可指定。
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DEFAULT = ROOT / "data" / "combined"
EUR_FROM = "2026-10-01"     # A85 的 HU 从这天起已是 EUR/MWh
FX_LAG_DAYS = 1
DATE_FMT = "%Y-%m-%d %H:%M"
LOCAL_TZ = "Europe/Budapest"


def to_utc(df, single_is_first):
    """当地墙钟索引 -> UTC 索引。回拨日重复的当地时间：来源里出现两次就按先后当第一、二遍；
    只出现一次的按 single_is_first 归属（True = 第一遍 UTC+2，False = 第二遍 UTC+1）。"""
    if df.empty:
        return df
    key = pd.Series(0, index=df.index)
    n = key.groupby(level=0).transform("size").to_numpy()
    occ = key.groupby(level=0).cumcount().to_numpy()
    first_pass = np.where(n > 1, occ == 0, single_is_first)
    out = df.copy()
    out.index = df.index.tz_localize(LOCAL_TZ, ambiguous=first_pass, nonexistent="NaT").tz_convert("UTC")
    out = out[out.index.notna()]
    return out[~out.index.duplicated(keep="first")]


def read_ida(year):
    p = ROOT / "data" / "enappsys" / "IDA" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df.index = pd.to_datetime(df.pop("datetime"), utc=True)   # 带 +02:00 / +01:00 偏移，直接转 UTC
    return df[~df.index.duplicated(keep="first")]


def read_id3(year):
    p = ROOT / "data" / "enappsys" / "ID3" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, sep="\t")
    df.index = pd.to_datetime(df.pop("Date"), format="%d/%m/%Y %H:%M")
    return df.rename(columns={"ID3": "ID3 Price (EUR/MWh)", "Vol": "ID3 Volume (MW)"})


def read_direction(year):
    p = ROOT / "data" / "mavir" / "imbalance" / f"HU_{year}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, usecols=["Date", "System direction [MWh]"])
    df.index = pd.to_datetime(df.pop("Date"), format="%d/%m/%Y %H:%M")
    return df.rename(columns={"System direction [MWh]": "System direction (MWh)"})


def read_imbalance(year):
    p = ROOT / "raw_data" / str(year) / "A85.csv"
    if not p.exists():
        return pd.DataFrame()
    x = pd.read_csv(p, usecols=["Date", "HU"])
    x.index = pd.to_datetime(x.pop("Date"), format="%Y/%m/%d %H:%M")
    price = x["HU"].dropna()
    fx = pd.read_csv(ROOT / "data" / "fx" / "MNB.csv", usecols=["Date", "EUR"])
    fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
    fx = fx.drop_duplicates("Date").set_index("Date")["EUR"].sort_index()
    day = price.index.normalize() - pd.Timedelta(days=FX_LAG_DAYS)
    rate = fx.reindex(day, method="ffill").to_numpy()
    eur = (price / rate).where(price.index < pd.Timestamp(EUR_FROM), price)
    return eur.rename("Imbalance Price (EUR/MWh)").to_frame()


def build(year):
    parts = [read_ida(year),                              # 已是 UTC
             to_utc(read_id3(year), single_is_first=True),
             to_utc(read_direction(year), single_is_first=True),
             to_utc(read_imbalance(year), single_is_first=False)]   # A85 抓取脚本去重时留的是第二遍
    df = pd.concat([p for p in parts if not p.empty], axis=1).sort_index()
    df = df[df.index.tz_convert(LOCAL_TZ).year == year]
    # 没有 MAVIR 数据的年份（2025 年以前）也保留这一列（留空），所有年份的列一致
    direction, imbalance = "System direction (MWh)", "Imbalance Price (EUR/MWh)"
    if direction not in df:
        df[direction] = float("nan")
    df = df[[c for c in df if c not in (direction, imbalance)] + [direction] + ([imbalance] if imbalance in df else [])]
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(9)
    return df.dropna(how="all")


def write(df, path):
    out = df.copy()
    out.insert(0, "UTC", out.index.strftime(DATE_FMT))
    out.index = out.index.tz_convert(LOCAL_TZ).strftime(DATE_FMT)
    out.index.name = "Date"
    out.to_csv(path, lineterminator="\r\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    a = ap.parse_args()
    OUT = a.out
    OUT.mkdir(parents=True, exist_ok=True)
    years = a.years or sorted({
        int(m.group(1))
        for d in ("data/mavir/imbalance", "data/enappsys/IDA", "data/enappsys/ID3")
        for p in (ROOT / d).glob("HU_*.csv")
        if (m := re.fullmatch(r"HU_(\d{4})\.csv", p.name))})
    for year in years:
        df = build(year)
        write(df, OUT / f"HU_{year}.csv")
        print(f"HU_{year}: {len(df)} rows, {df.index.min()} .. {df.index.max()}")


if __name__ == "__main__":
    main()
