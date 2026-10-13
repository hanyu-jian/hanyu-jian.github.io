#!/usr/bin/env python3
"""
由 raw_data（15min 原始数据）生成 capture-price.html 使用的 15min 宽表：
  data/price_15min.csv   <- raw_data/{YEAR}/A44.csv
  data/solar_15min.csv   <- raw_data/{YEAR}/generation/{CC}.csv (Solar)
  data/wind_15min.csv    <- raw_data/{YEAR}/generation/{CC}.csv (Wind Onshore + Wind Offshore)
时间轴格式与原 raw_data 一致："YYYY/M/D H:MM"。缺失值保留为空。
"""
from pathlib import Path
import pandas as pd

RAW_DIR  = Path("raw_data")
DATA_DIR = Path("data")
FMT = "%Y/%m/%d %H:%M"


def fmt_index(idx: pd.DatetimeIndex) -> list[str]:
    return [f"{d.year}/{d.month}/{d.day} {d.hour}:{d.minute:02d}" for d in idx]


def save(df: pd.DataFrame, path: Path) -> None:
    df = df.sort_index()
    df.index = fmt_index(df.index)
    df.index.name = "Date"
    df.round(6).to_csv(path)
    print(f"{path}: {len(df)} rows, {len(df.columns)} cols")


def build_price() -> None:
    frames = []
    for f in sorted(RAW_DIR.glob("*/A44.csv")):
        d = pd.read_csv(f)
        d["Date"] = pd.to_datetime(d["Date"], format=FMT)
        frames.append(d.set_index("Date"))
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")]
    # 个别国家/时段 raw A44 缺 15min 数据（如 IT 2024-2025）：回退到 data/price.csv 的小时价，
    # 展开为 4 个 15min 点
    hourly = pd.read_csv(DATA_DIR / "price.csv")
    hourly["Date"] = pd.to_datetime(hourly["Date"], format=FMT)
    hourly = hourly.drop_duplicates("Date", keep="last").set_index("Date")
    hour_key = df.index.floor("h")
    for col in df.columns:
        if col in hourly.columns:
            fb = pd.Series(hourly[col].reindex(hour_key).to_numpy(), index=df.index)
            df[col] = df[col].fillna(fb)
    save(df, DATA_DIR / "price_15min.csv")


def build_gen() -> None:
    solar, wind = {}, {}
    for f in sorted(RAW_DIR.glob("*/generation/*.csv")):
        cc = f.stem
        d = pd.read_csv(f)
        d = d[d["category"].isin(["Solar", "Wind Onshore", "Wind Offshore"])]
        d["date"] = pd.to_datetime(d["date"], format=FMT)
        p = d.pivot_table(index="date", columns="category", values="value", aggfunc="mean")
        solar.setdefault(cc, []).append(p["Solar"] if "Solar" in p else None)
        w = [p[c] for c in ("Wind Onshore", "Wind Offshore") if c in p]
        wind.setdefault(cc, []).append(pd.concat(w, axis=1).sum(axis=1, min_count=1) if w else None)
    for name, store in (("solar", solar), ("wind", wind)):
        cols = {}
        for cc, parts in store.items():
            parts = [s for s in parts if s is not None]
            if parts:
                s = pd.concat(parts)
                cols[cc] = s[~s.index.duplicated(keep="last")]
        save(pd.DataFrame(cols).sort_index(axis=1), DATA_DIR / f"{name}_15min.csv")


if __name__ == "__main__":
    build_price()
    build_gen()
