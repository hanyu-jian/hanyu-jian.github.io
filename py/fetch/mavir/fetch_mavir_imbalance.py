#!/usr/bin/env python3
"""
MAVIR 不平衡价格下载脚本 — Pos_ImbalancePrice_ENG_preliminary_<数据日>_<发布日>.xlsx（15min）
- 每个数据日一个 xlsx（表头在第 7 行，96 行）；下载后直接合并进
  data/mavir/imbalance/HU_<年>.csv，不保留 xlsx。格式与 data/enappsys 下的一致：
    逗号分隔（Excel 双击可直接分列），Date 为当地时间 dd/mm/YYYY HH:MM（取 Period 的起始时刻），行尾 CRLF，
    其余列保持 xlsx 原列名（Pos 与 Neg 价格相同，只取这一个文件）
- 文件名里的发布日不固定（如 18 日 → 22 日，19 日 → 23 日），所以对每个缺失的数据日
  依次试 数据日+1 … +MAX_LAG 的发布日；拿不到就跳过，下次再试。
- MAVIR 的文件放在 Liferay 的目录里，目录号会换（2026-10 的文件在 358398864，11 月起在 360748237），换了以后旧目录里就再也没有新文件。
  所以目录是一个列表（FOLDERS，最新的排前面），每个文件依次试每个目录；以后再换目录，在 FOLDERS 最前面加一行目录号即可。
  （临时试一个新目录也可以不改代码：运行时设环境变量 MAVIR_BASES，逗号分隔，目录号或完整地址都行，排在内置目录前面。）
- 2026-01-01 … 09-30 只有 System direction [MWh] 一列（手工提供的数据），其余列留空；10-01 起为 preliminary 全部列
- 全空的列不写入；数值保留 9 位小数
- 同一个 Date 以新数据为准；秋季回拨那天 02:00~02:45 出现两遍（xlsx 里第二遍标成 "02+00"、"02+15"…），
  两遍都保留：Date 重复，先出现的是第一遍（UTC+2），共 100 行。旧文件里只有第一遍的日子，
  用 --start/--end --refresh 重新下载该日即可补上第二遍
- 模式：
    默认                      取 [今天-30 天, 今天-3 天] 中尚未入库的数据日
    --start/--end YYYY-MM-DD  补数：数据日范围（含两端）
    --refresh                 范围内已入库的数据日也重新下载并覆盖（preliminary 可能被修订）
"""

import argparse
import io
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = "https://www.mavir.hu/documents/187408084/"
FOLDERS = ["360748237", "358398864"]        # 最新的排前面：11 月起 → 10 月
NAME = "Pos_ImbalancePrice_ENG_preliminary_{d}_{p}.xlsx"
OUT_DIR = Path("data/mavir/imbalance")
DATE_FMT = "%d/%m/%Y %H:%M"
MAX_LAG = 14
SETTLE_DAYS = 3
HEADER_ROW = 6  # 0 起算，即第 7 行
LOCAL_TZ = "Europe/Budapest"


def get(url):
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        print(f"  request error: {e}")
        return None
    return r.content if r.status_code == 200 and r.content[:2] == b"PK" else None


def bases():
    """要试的目录地址列表：环境变量 MAVIR_BASES 里的在前，内置的 FOLDERS 在后；去重保序。"""
    out = []
    for item in [x.strip() for x in os.environ.get("MAVIR_BASES", "").split(",") if x.strip()] + FOLDERS:
        url = item if "/" in item else ROOT + item
        url = url if url.endswith("/") else url + "/"
        if url not in out:
            out.append(url)
    return out


def download(d):
    ds = d.strftime("%Y%m%d")
    folders = bases()
    for lag in range(1, MAX_LAG + 1):
        pub = (d + timedelta(days=lag)).strftime("%Y%m%d")
        for base in folders:
            if c := get(base + NAME.format(d=ds, p=pub)):
                return c
    return None


def parse(content):
    """xlsx 字节 → DataFrame：Date（当地时间 datetime）+ 其余列原样。"""
    df = pd.read_excel(io.BytesIO(content), header=HEADER_ROW)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df = df.dropna(subset=["Period"])
    start = df["Period"].astype(str).str.split("-").str[0].str.strip()
    # 秋季回拨那天重复的一小时，MAVIR 标成 "02+00"、"02+15"…：这是第二遍，也保留，时间写成 02:00、02:15…
    # （xlsx 里第一遍在前、第二遍在后，保持原顺序）
    start = start.str.replace("+", ":", regex=False)
    day = pd.to_datetime(df["Date"].astype(str).str.strip())
    date_col = pd.to_datetime(day.dt.strftime("%Y-%m-%d") + " " + start)
    out = df.drop(columns=["Date", "Period"]).copy()
    out.insert(0, "Date", date_col)
    return out.reset_index(drop=True)


def sort_chronologically(df):
    """按真实时间先后排序。回拨日重复的 02:00~02:45：按出现的先后当第一遍（UTC+2）、第二遍（UTC+1），第一遍整块在前。"""
    first_pass = (df.groupby("Date").cumcount() == 0).to_numpy()
    utc = pd.DatetimeIndex(df["Date"]).tz_localize(LOCAL_TZ, ambiguous=first_pass, nonexistent="NaT").tz_convert("UTC")
    return df.iloc[np.argsort(utc.asi8, kind="stable")].reset_index(drop=True)


def read_year(path):
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], format=DATE_FMT)
    return df


def merge_into_files(new):
    for year, part in new.groupby(new["Date"].dt.year):
        path = OUT_DIR / f"HU_{year}.csv"
        old = read_year(path)
        # 以新数据为准：新数据里出现的 Date，旧文件里同一 Date 的行（含回拨日的两遍）整体被替换
        merged = part if old is None else pd.concat([old[~old["Date"].isin(part["Date"])], part])
        merged = sort_chronologically(merged)
        # 去掉没有任何数据的列；四舍五入到 9 位小数，避免浮点噪声（>15 位有效数字，WPS/Excel 会当成文本）
        merged = merged.dropna(axis=1, how="all")
        num = merged.select_dtypes("number").columns
        merged[num] = merged[num].round(9)
        out = merged.copy()
        out["Date"] = out["Date"].dt.strftime(DATE_FMT)
        out.to_csv(path, index=False, lineterminator="\r\n")
        print(f"{path}: {len(merged)} rows")


def stored_days():
    days = set()
    for p in OUT_DIR.glob("HU_*.csv"):
        days |= set(read_year(p)["Date"].dt.strftime("%Y%m%d"))
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    today = date.today()
    start = date.fromisoformat(a.start) if a.start else today - timedelta(days=30)
    end = date.fromisoformat(a.end) if a.end else today - timedelta(days=SETTLE_DAYS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    have = set() if a.refresh else stored_days()
    frames = []
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        if ds not in have:
            content = download(d)
            if content:
                frames.append(parse(content))
                print(f"fetched {ds}")
            else:
                print(f"not available: {ds}")
        d += timedelta(days=1)
    if frames:
        merge_into_files(pd.concat(frames, ignore_index=True))


if __name__ == "__main__":
    sys.exit(main())
