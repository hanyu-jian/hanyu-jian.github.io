#!/usr/bin/env python3
"""
MAVIR 不平衡价格下载脚本 — 15min，合并进 data/mavir/imbalance/HU_<年>.csv（不保留 xlsx）
- 两种来源，用 source 列区分，同一个 Date 上 final 优先于 preliminary：
    preliminary  Pos_ImbalancePrice_ENG_preliminary_<数据日>_<发布日>.xlsx   每个数据日一个文件，
                 约滞后一周；单位 EUR、MWh
    final        Up_BE_unitprice_<月初>_<发布日>.xlsx   月度修正后的数据，次月中旬左右发布；
                 单位 HUF、kWh，System direction 在最后一列
- 对齐：final 的数值列按表头里的单位统一成 MWh / EUR / EUR/MWh，列名改成 "名称 [单位]"：
    kWh→MWh（÷1000）；HUF→EUR、HUF/kWh→EUR/MWh（×1000）用 data/fx/MNB.csv 的 EUR 汇率，
    取前一个工作日的汇率（与 tokaj_backtest.py 一致）；已经是 EUR、MWh 的（2026-10 起匈牙利加入
    PICASSO，结算改为 EUR）原样保留。
  含义相同的列与 preliminary 同名（System state / System direction / Price of positive BRP
  imbalance / Market incentive price / Value of Avoided Activation Price）；其余（Positive …）
  各占一列，互相没有的列留空。
- 文件名里的发布日不固定，所以按范围逐个试：preliminary 试 数据日+1 … +MAX_LAG，
  final 试 次月 8 日 … 25 日，并依次试 FINAL_FOLDERS 里的文档库文件夹 ID。
- CSV：逗号分隔，Date 为当地时间 dd/mm/YYYY HH:MM（取区间起始时刻）；
  秋季回拨那天重复的当地时间只保留第一次出现的。
- 模式：
    默认                      preliminary：[今天-30 天, 今天-3 天] 中尚未入库的数据日；
                              final：最近 3 个已结束、满 FINAL_AFTER_DAYS 天且还没有 final 的月份
    --start/--end YYYY-MM-DD  preliminary 补数：数据日范围（含两端）
    --final-months 2026-08,2026-09  只下载指定月份的 final（已有 final 的月份也重新下载覆盖）
    --final-file PATH...      把手动下载的 final xlsx 直接并入（下载不到时用）
    --refresh                 preliminary 范围内已入库的数据日也重新下载
"""

import argparse
import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

DOC_ROOT = "https://mavir.hu/documents/187408084/"
PRELIM_BASE = DOC_ROOT + "358398864/"
PRELIM_NAME = "Pos_ImbalancePrice_ENG_preliminary_{d}_{p}.xlsx"
FINAL_FOLDERS = ["358579907", "356648811", "358398864"]
FINAL_NAME = "Up_BE_unitprice_{m}_{p}.xlsx"
OUT_DIR = Path("data/mavir/imbalance")
DATE_FMT = "%d/%m/%Y %H:%M"
MAX_LAG = 14
SETTLE_DAYS = 3
FINAL_AFTER_DAYS = 10     # 月末后满这么多天才开始找 final
FINAL_PUB_DAYS = range(8, 26)   # 发布日 = 次月 8 日 … 25 日
FINAL_LOOKBACK_MONTHS = 3
HEADER_ROW = 6  # 0 起算，即第 7 行
RANK = {"preliminary": 0, "final": 1}
MNB_CSV = Path("data/fx/MNB.csv")
FX_LAG_DAYS = 1
FX_MAX_STALE_DAYS = 7
# final 表头 → preliminary 表头的名称对应（去掉单位之后）
NAME_MAP = {
    "Valueof Avoided Activation": "Value of Avoided Activation Price",
    "Price for positive BRP net energy balance": "Price of positive BRP imbalance",
}
# 单位 → (目标单位, 乘数, 是否需要除以 HUF/EUR 汇率)
UNITS = {
    "kWh": ("MWh", 1e-3, False),
    "MWh": ("MWh", 1, False),
    "HUF": ("EUR", 1, True),
    "EUR": ("EUR", 1, False),
    "HUF/kWh": ("EUR/MWh", 1e3, True),
    "HUF/MWh": ("EUR/MWh", 1, True),
    "EUR/kWh": ("EUR/MWh", 1e3, False),
    "EUR/MWh": ("EUR/MWh", 1, False),
}


def get(url):
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        print(f"  request error: {e}")
        return None
    return r.content if r.status_code == 200 and r.content[:2] == b"PK" else None


def download_preliminary(d):
    ds = d.strftime("%Y%m%d")
    for lag in range(1, MAX_LAG + 1):
        pub = (d + timedelta(days=lag)).strftime("%Y%m%d")
        if c := get(PRELIM_BASE + PRELIM_NAME.format(d=ds, p=pub)):
            return c
    return None


def month_end(m):
    return (m.replace(day=28) + timedelta(days=4)).replace(day=1)  # 次月 1 日


def download_final(m):
    """m 为月初 date；试各文件夹 × 各发布日。"""
    nxt = month_end(m)
    for day in FINAL_PUB_DAYS:
        pub = nxt + timedelta(days=day - 1)
        name = FINAL_NAME.format(m=m.strftime("%Y%m%d"), p=pub.strftime("%Y%m%d"))
        for folder in FINAL_FOLDERS:
            if c := get(f"{DOC_ROOT}{folder}/{name}"):
                return c
    return None


def read_xlsx(content):
    df = pd.read_excel(io.BytesIO(content), header=HEADER_ROW)
    return df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]


def with_date(df, date_col, period_col):
    df = df.dropna(subset=[period_col]).copy()
    start = df[period_col].astype(str).str.split("-").str[0].str.strip()
    day = pd.to_datetime(df[date_col].astype(str).str.strip())
    stamp = pd.to_datetime(day.dt.strftime("%Y-%m-%d") + " " + start)
    out = df.drop(columns=[date_col, period_col])
    out.insert(0, "Date", stamp)
    return out


def parse_preliminary(content):
    out = with_date(read_xlsx(content), "Date", "Period")
    out.insert(1, "source", "preliminary")
    return out.reset_index(drop=True)


def eur_rate(stamps):
    """每行 Date 对应的 HUF/EUR：T-1 往前最近一个工作日的 MNB 汇率；MNB 数据不够新就报错。"""
    fx = pd.read_csv(MNB_CSV, usecols=["Date", "EUR"])
    fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
    fx = fx.drop_duplicates("Date").set_index("Date")["EUR"].sort_index()
    day = stamps.dt.normalize() - pd.Timedelta(days=FX_LAG_DAYS)
    if (day.max() - fx.index.max()).days > FX_MAX_STALE_DAYS:
        sys.exit(f"{MNB_CSV} ends {fx.index.max():%Y-%m-%d}, too old to convert {day.max():%Y-%m-%d}")
    return fx.reindex(day, method="ffill").to_numpy()


def parse_final(content):
    df = read_xlsx(content)
    out = with_date(df, "Local Date", "Imbalance settlement period")
    rate = None
    for col in [c for c in out.columns if c != "Date"]:
        m = re.fullmatch(r"(.*?)\s*[\(\[]([^\)\]]+)[\)\]]", str(col))
        if not m or m.group(2) not in UNITS:
            sys.exit(f"cannot read unit of column {col!r}")
        base, unit = m.group(1).strip(), m.group(2)
        target, mult, needs_fx = UNITS[unit]
        vals = out.pop(col) * mult
        if needs_fx:
            rate = eur_rate(out["Date"]) if rate is None else rate
            vals = vals / rate
        out[f"{NAME_MAP.get(base, base)} [{target}]"] = vals
    out.insert(1, "source", "final")
    return out.reset_index(drop=True)


def parse(content):
    """按表头判断是 final 还是 preliminary。"""
    return parse_final(content) if "Local Date" in read_xlsx(content).columns else parse_preliminary(content)


def read_year(path):
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], format=DATE_FMT)
    if "source" not in df.columns:
        df.insert(1, "source", "preliminary")
    return df


def merge_into_files(new):
    new = new.drop_duplicates(["Date", "source"], keep="first")
    for year, part in new.groupby(new["Date"].dt.year):
        path = OUT_DIR / f"HU_{year}.csv"
        old = read_year(path)
        cols = list(part.columns)
        if old is not None:
            cols = list(old.columns) + [c for c in part.columns if c not in old.columns]
            old = old.assign(_batch=0)
        both = pd.concat([x for x in (old, part.assign(_batch=1)) if x is not None], ignore_index=True)
        # 同一个 Date：final 优先于 preliminary，同来源以新数据为准
        both["_rank"] = both["source"].map(RANK)
        both = both.sort_values(["Date", "_rank", "_batch"], kind="stable")
        merged = both.drop_duplicates("Date", keep="last")[cols]
        out = merged.copy()
        out["Date"] = out["Date"].dt.strftime(DATE_FMT)
        out.to_csv(path, index=False, lineterminator="\r\n")
        print(f"{path}: {len(merged)} rows")


def stored(source):
    """某来源已入库的 Date 集合（按天 / 按月）。"""
    stamps = []
    for p in OUT_DIR.glob("HU_*.csv"):
        df = read_year(p)
        stamps.append(df.loc[df["source"] == source, "Date"])
    s = pd.concat(stamps) if stamps else pd.Series([], dtype="datetime64[ns]")
    return s


def final_months_due(today):
    have = set(stored("final").dt.strftime("%Y-%m"))
    due, m = [], today.replace(day=1)
    for _ in range(FINAL_LOOKBACK_MONTHS + 1):
        m = (m - timedelta(days=1)).replace(day=1)
        if (today - month_end(m)).days + 1 >= FINAL_AFTER_DAYS and m.strftime("%Y-%m") not in have:
            due.append(m)
    return sorted(due)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--final-months", help="逗号分隔，如 2026-08,2026-09")
    ap.add_argument("--final-file", nargs="+", type=Path)
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    frames = []

    for p in a.final_file or []:
        frames.append(parse(p.read_bytes()))
        print(f"loaded {p.name}")

    if not a.final_file:
        if a.final_months:
            months = [date.fromisoformat(m.strip() + "-01") for m in a.final_months.split(",")]
        elif a.start or a.end:
            months = []
        else:
            months = final_months_due(today)
        for m in months:
            if c := download_final(m):
                frames.append(parse_final(c))
                print(f"fetched final {m:%Y-%m}")
            else:
                print(f"final not available: {m:%Y-%m}")

        if not a.final_months:
            start = date.fromisoformat(a.start) if a.start else today - timedelta(days=30)
            end = date.fromisoformat(a.end) if a.end else today - timedelta(days=SETTLE_DAYS)
            have = set() if a.refresh else set(stored("preliminary").dt.strftime("%Y%m%d")) | set(
                stored("final").dt.strftime("%Y%m%d"))
            d = start
            while d <= end:
                ds = d.strftime("%Y%m%d")
                if ds not in have:
                    if c := download_preliminary(d):
                        frames.append(parse_preliminary(c))
                        print(f"fetched {ds}")
                    else:
                        print(f"not available: {ds}")
                d += timedelta(days=1)

    if frames:
        merge_into_files(pd.concat(frames, ignore_index=True))


if __name__ == "__main__":
    sys.exit(main())
