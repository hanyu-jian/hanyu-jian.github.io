#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本 — aFRR 容量（Contracted reserves，A81 / B95），只取日度（A01）
- 国家（--country，缺省 RO）：
    RO  Transelectrica（10YRO-TEL------P），1h 数据，写 hourly；交付日按 Europe/Bucharest，价格是 RON，按 MNB 折成 EUR
    DE  4 个 TSO：50Hertz、Amprion、TenneT GER、TransnetBW 各自请求，再按时间合并——
        容量相加、价格按容量加权平均；15min 数据，写 15min；交付日按 Europe/Berlin；价格本来就是 EUR
        （价格数值就是 ENTSOE 给的 EUR/MW/ISP，不再乘系数）
- 请求：documentType=A81, businessType=B95, Type_MarketAgreement.Type=A01, processType=A51（aFRR），
  controlArea_Domain=各 TSO 的 EIC；按 offset 每 100 个 TimeSeries 翻页，单次请求不超过 1 年
- 方向：flowDirection.direction A01 = up，A02 = down，分别写
    data/entsoe/afrr/<up|down>/<hourly|15min>/<国家>_<年>.csv（按 CET/CEST 的年份分文件）
- 列名和匈牙利 data/enappsys/afrr/capacity/<up|down>/15min/HU_<年>.csv 里的 DAILY 两列一致：
    Date（CET/CEST 墙钟，时间段起点，格式 "YYYY/M/D H:MM"，和 data/entsoe/imbalance_price.csv 等其他 ENTSOE 小时表一致）、
    WEIGHTED AVERAGE PRICE <UP|DOWN> (DAILY) (EUR/MW/h)、VOLUME <UP|DOWN> (DAILY) (MW)
- 价格：XML 里币种是 RON 的（罗马尼亚）按 MNB 汇率（data/fx/MNB.csv，HUF/EUR 和 HUF/RON）折成 EUR：
    EUR = RON × MNB_RON / MNB_EUR；用交付日当天或之前最近一个有牌价的日期（周末 / 节假日沿用前一个工作日）。
    币种本来就是 EUR 的 TimeSeries（德国）不转换。
- 时间：Date 的标签和其他 fetch_entsoe 脚本一致，是 Europe/Brussels（CET/CEST）墙钟，年份文件也按这个标签；
  和 ENTSOE 网页（CET/CEST）上的 ISP 起点逐行对得上。
  交付日（--start/--end、请求窗口、MNB 牌价日期）按该国当地时区：例如罗马尼亚 10/1 这一天
  从 Brussels 的 9/30 23:00 开始，所以文件第一行是前一天 23:00；德国和 Brussels 同一时区，无偏移。
- 不合并、不去重：原始数据是什么就写什么。秋季回拨日 02:00~02:59 出现两遍（先 CEST 后 CET），两遍都保留且按真实先后排，
  夏令时开始日 02:00 本来就没有，不补。已有文件按「Date + 第几次出现」对齐覆盖。
- 同一方向同一时刻有多个 TimeSeries 时：容量相加，价格按容量加权平均
- 保持原始分辨率，不展开：RO 是 1 小时、DE 是 15min；比该国分辨率更细的点按所在时间段汇总（容量取平均，价格按容量加权）；
  若出现跨多个小时的点（如 4h 块、整天），按小时拆成多行，每行容量和价格与该点相同。
  Point 省略的位置沿用前一个值（ENTSOE 的压缩规则）
- 模式：
    --days-back 3            取 [今天-3 天, 明天]（默认，每日更新用，覆盖事后修正）
    --start/--end YYYY-MM-DD 补数：按交付日，起止都含
    --direction up|down      只抓一个方向（缺省两个都抓）
"""

import argparse
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://web-api.tp.entsoe.eu/api"
LOCAL_TZ = "Europe/Brussels"      # Date 标签，与其他 fetch_entsoe 脚本的 ENTSOE_DISPLAY_TZ 相同
COUNTRIES = {
    # areas: controlArea_Domain EIC；delivery_tz: 交付日 / 请求窗口 / MNB 牌价日期所用的当地时区；step / folder: 输出分辨率（分钟）和目录
    "RO": {"delivery_tz": "Europe/Bucharest", "step": 60, "folder": "hourly",
           "areas": {"Transelectrica": "10YRO-TEL------P"}},
    "DE": {"delivery_tz": "Europe/Berlin", "step": 15, "folder": "15min", "areas": {
        "50Hertz": "10YDE-VE-------2", "Amprion": "10YDE-RWENET---I",
        "TenneT GER": "10YDE-EON------1", "TransnetBW": "10YDE-ENBW-----N"}},
}
DIRECTIONS = {"A01": "up", "A02": "down"}
PAGE_SIZE = 100
CHUNK_DAYS = 365
REQUEST_DELAY = 1.5
TIMEOUT = 90
TIME_COL = "Date"
OUT_ROOT_DEFAULT = Path("data/entsoe/afrr")
MNB_PATH_DEFAULT = Path("data/fx/MNB.csv")
ENTSOE_FMT = "%Y%m%d%H%M"
PRICE_DECIMALS = 4


def price_col(direction):
    return f"WEIGHTED AVERAGE PRICE {direction.upper()} (DAILY) (EUR/MW/h)"


def volume_col(direction):
    return f"VOLUME {direction.upper()} (DAILY) (MW)"


# ── MNB 汇率 ────────────────────────────────────────────────

def load_ron_per_eur(path):
    """MNB.csv 的 EUR / RON 两列都是 HUF，RON→EUR 的系数 = RON / EUR；按自然日向后填充。"""
    fx = pd.read_csv(path)
    fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
    rate = (fx["RON"] / fx["EUR"]).set_axis(fx["Date"]).sort_index()
    rate = rate[~rate.index.duplicated(keep="last")]
    return rate.reindex(pd.date_range(rate.index.min(), date.today() + timedelta(days=2), freq="D")).ffill()


# ── 请求 ────────────────────────────────────────────────────

def local_midnight_utc(d, tz):
    return pd.Timestamp(d).tz_localize(tz).tz_convert("UTC").strftime(ENTSOE_FMT)


def fetch_bytes(params, label):
    token = os.environ.get("ENTSOE_TOKEN")
    if not token:
        sys.exit("ENTSOE_TOKEN not set")
    for attempt in range(5):
        try:
            resp = requests.get(API_URL, params={**params, "securityToken": token}, timeout=TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == 4:
                sys.exit(f"[ERROR] {label}: {e}")
            time.sleep(2 ** attempt)
    if resp.content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            return [zf.read(n) for n in zf.namelist() if n.endswith(".xml")]
    return [resp.content]


# ── 解析 ────────────────────────────────────────────────────

def res_minutes(text):
    """ISO 8601 持续时间 → 分钟（PT15M / PT60M / PT4H / P1D …），认不出返回 None。"""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi


def parse_xml(xml_bytes):
    """返回 (n_timeseries, rows)；rows 每项是一个原始分辨率的点：UTC 起点、方向、容量、价格、币种。"""
    root = ET.fromstring(xml_bytes)
    if "Acknowledgement_MarketDocument" in root.tag:
        print(f"      [API] {root.findtext('.//{*}Reason/{*}text', default='unknown')}")
        return 0, []
    rows, n_ts = [], 0
    for ts in root.findall(".//{*}TimeSeries"):
        n_ts += 1
        direction = DIRECTIONS.get(ts.findtext("{*}flowDirection.direction", default="").strip())
        currency = ts.findtext("{*}currency_Unit.name", default="").strip()
        if direction is None:
            continue
        for period in ts.findall("{*}Period"):
            res = res_minutes(period.findtext(".//{*}resolution", default=""))
            start = datetime.strptime(period.findtext(".//{*}start").strip(), "%Y-%m-%dT%H:%MZ")
            end = datetime.strptime(period.findtext(".//{*}end").strip(), "%Y-%m-%dT%H:%MZ")
            if res is None:
                print(f"      [WARN] 跳过未知分辨率 {period.findtext('.//{*}resolution')}")
                continue
            points = {}
            for pt in period.findall("{*}Point"):
                q = pt.findtext("{*}quantity")
                p = pt.findtext("{*}procurement_Price.amount")
                points[int(pt.findtext("{*}position"))] = (
                    float(q) if q not in (None, "") else None,
                    float(p) if p not in (None, "") else None,
                )
            step = timedelta(minutes=res)
            slots = -((start - end) // step)   # 向上取整
            last = None
            for pos in range(1, slots + 1):
                last = points.get(pos, last)
                if last is None:
                    continue
                t0 = start + step * (pos - 1)
                t1 = min(t0 + step, end)
                # 不超过 1 小时的点原样一行；更长的点（4h 块、整天）按小时拆开
                hours = [t0] if res <= 60 else [t0 + timedelta(hours=k) for k in range(int((t1 - t0) / timedelta(hours=1)))]
                for t in hours:
                    rows.append({"start": t, "direction": direction, "qty": last[0], "price": last[1], "currency": currency})
    return n_ts, rows


def fetch_rows(start, end, cfg):
    rows = []
    for area, eic in cfg["areas"].items():
        rows += fetch_area_rows(start, end, area, eic, cfg["delivery_tz"])
    return rows


def fetch_area_rows(start, end, area, eic, tz):
    rows, chunk_start = [], start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        offset = 0
        while True:
            params = {"documentType": "A81", "businessType": "B95", "Type_MarketAgreement.Type": "A01",
                      "processType": "A51", "controlArea_Domain": eic,
                      "periodStart": local_midnight_utc(chunk_start, tz),
                      "periodEnd": local_midnight_utc(chunk_end + timedelta(days=1), tz), "offset": offset}
            n_total = 0
            for xml in fetch_bytes(params, f"A81 {area} {chunk_start}..{chunk_end} offset={offset}"):
                n_ts, part = parse_xml(xml)
                n_total += n_ts
                rows += part
            print(f"     {area} {chunk_start}..{chunk_end} offset={offset}: {n_total} TimeSeries")
            time.sleep(REQUEST_DELAY)
            if n_total < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        chunk_start = chunk_end + timedelta(days=1)
    return rows


# ── 整理成小时、折算欧元 ──────────────────────────────────

def build_frames(rows, ron_per_eur, delivery_tz, step=60):
    """rows → {direction: DataFrame[Date, price col, volume col, _utc]}，每个真实时间段（UTC，step 分钟）一行、Date 是 CET/CEST 墙钟。
    回拨日 02:00~02:59 的两遍是不同的 UTC 时间段，各占一行，不合并。"""
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    utc = pd.DatetimeIndex(df["start"]).tz_localize("UTC")
    hour = utc.floor(f"{step}min")               # 原始分辨率原样；更细的点归到所在的时间段
    label = hour.tz_convert(LOCAL_TZ)
    df["_utc"] = hour.tz_localize(None)
    df[TIME_COL] = [f"{t.year}/{t.month}/{t.day} {t.hour}:{t.minute:02d}" for t in label]
    # RON → EUR：交付日（该国当地）当天或之前最近一个 MNB 牌价
    deliv = utc.tz_convert(delivery_tz).tz_localize(None).normalize()
    factor = ron_per_eur.reindex(pd.DatetimeIndex(deliv), method="ffill").to_numpy()
    df["price_eur"] = df["price"].where(df["currency"] == "EUR", df["price"] * factor)
    if df.loc[df["currency"] == "RON", "price"].notna().any() and pd.isna(factor[(df["currency"] == "RON").to_numpy()]).any():
        sys.exit("MNB.csv 缺少部分交付日的 RON / EUR 牌价")
    unknown = set(df["currency"]) - {"RON", "EUR"}
    if unknown:
        sys.exit(f"未知币种 {unknown}，不会按 RON 折算")
    df["slot"] = df["start"]
    df["pq"] = df["price_eur"] * df["qty"]
    out = {}
    for direction, part in df.groupby("direction"):
        # 同一时刻多个 TimeSeries（德国还包括 4 个 TSO）：容量相加，价格按容量加权
        g = part.groupby(["_utc", TIME_COL, "slot"], sort=False)
        slot = pd.DataFrame({"vol": g["qty"].sum(min_count=1), "pq": g["pq"].sum(min_count=1),
                             "p": g["price_eur"].mean()}).reset_index()
        slot["price"] = (slot["pq"] / slot["vol"].where(slot["vol"] > 0)).fillna(slot["p"])  # 容量为 0 时退化成简单平均
        # 同一时间段多个时刻（仅当原始比输出分辨率细）：容量取平均，价格按容量加权
        slot["pq"] = slot["price"] * slot["vol"]
        h = slot.groupby(["_utc", TIME_COL], sort=True)
        vol = h["vol"].mean()
        wavg = (h["pq"].sum(min_count=1) / h["vol"].sum().where(h["vol"].sum() > 0)).fillna(h["price"].mean())
        frame = pd.DataFrame({price_col(direction): wavg.round(PRICE_DECIMALS), volume_col(direction): vol})
        out[direction] = frame.reset_index().loc[:, [TIME_COL, price_col(direction), volume_col(direction), "_utc"]]
    return out


def merge(old, new):
    """按 (Date, 第几次出现) 对齐：回拨日重复的 02:xx 各自独立；新数据覆盖旧数据，不做平均。"""
    parts = []
    for f in (old, new):
        if f is not None and not f.empty:
            parts.append(f.reindex(columns=new.columns).assign(_occ=lambda x: x.groupby(TIME_COL).cumcount()))
    out = pd.concat(parts)
    out = out[~out.duplicated([TIME_COL, "_occ"], keep="last")]
    # 按真实时间先后排：回拨日重复的 02:xx，第一遍（CEST）整块在前，第二遍（CET）整块在后
    local = pd.DatetimeIndex(pd.to_datetime(out[TIME_COL], format="%Y/%m/%d %H:%M"))
    utc = local.tz_localize(LOCAL_TZ, ambiguous=(out["_occ"] == 0).to_numpy()).tz_convert("UTC")
    return out.assign(_k=utc.asi8).sort_values("_k", kind="stable").drop(columns=["_k", "_occ"]).reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", choices=sorted(COUNTRIES), default="RO")
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--start", help="YYYY-MM-DD（交付日，含）")
    ap.add_argument("--end", help="YYYY-MM-DD（交付日，含），缺省为明天")
    ap.add_argument("--direction", choices=["up", "down"], help="缺省 up 和 down 都抓")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT_DEFAULT,
                    help="输出到 <out-root>/<up|down>/<hourly|15min>/<国家>_<年>.csv")
    ap.add_argument("--mnb", type=Path, default=MNB_PATH_DEFAULT)
    a = ap.parse_args(argv)

    start = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else date.today() - timedelta(days=a.days_back)
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today() + timedelta(days=1)
    cfg, tz = COUNTRIES[a.country], COUNTRIES[a.country]["delivery_tz"]
    print(f"{a.country} aFRR capacity (A81/B95/A01/A51)  {start} .. {end}  areas: {list(cfg['areas'])}")
    frames = build_frames(fetch_rows(start, end, cfg), load_ron_per_eur(a.mnb), tz, cfg["step"])
    if not frames:
        sys.exit("no data rows returned")

    lo = pd.Timestamp(start).tz_localize(tz).tz_convert("UTC").tz_localize(None)       # 该国交付日
    hi = pd.Timestamp(end + timedelta(days=1)).tz_localize(tz).tz_convert("UTC").tz_localize(None)
    for direction in ([a.direction] if a.direction else sorted(frames)):
        if direction not in frames:
            print(f"[{direction}] no data")
            continue
        new = frames[direction]
        new = new[(new["_utc"] >= lo) & (new["_utc"] < hi)].drop(columns="_utc")   # 只留请求的交付日
        out_dir = a.out_root / direction / cfg["folder"]
        out_dir.mkdir(parents=True, exist_ok=True)
        for year, part in new.groupby(new[TIME_COL].str.split("/").str[0]):
            path = out_dir / f"{a.country}_{year}.csv"
            old = pd.read_csv(path, dtype={TIME_COL: str}) if path.exists() else None
            out = merge(old, part)
            out.to_csv(path, index=False)
            print(f"{path}: +{len(part)} rows [{part[TIME_COL].iloc[0]} .. {part[TIME_COL].iloc[-1]}], "
                  f"file now {len(out)} rows")


if __name__ == "__main__":
    main()
