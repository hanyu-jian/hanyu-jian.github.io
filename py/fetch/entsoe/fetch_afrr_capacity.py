#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本 — 罗马尼亚 aFRR 容量（Contracted reserves，A81 / B95），只取日度（A01）
- 请求：documentType=A81, businessType=B95, Type_MarketAgreement.Type=A01, processType=A51（aFRR），
  controlArea_Domain=10YRO-TEL------P；按 offset 每 100 个 TimeSeries 翻页，单次请求不超过 1 年
- 方向：flowDirection.direction A01 = up，A02 = down，分别写
    data/entsoe/afrr/<up|down>/hourly/RO_<年>.csv（按 CET/CEST 的年份分文件）
- 列名和匈牙利 data/enappsys/afrr/capacity/<up|down>/15min/HU_<年>.csv 里的 DAILY 两列一致：
    Date（CET/CEST 墙钟，小时起点，格式 "YYYY/M/D H:00"，和 data/entsoe/imbalance_price.csv 等其他 ENTSOE 小时表一致）、
    WEIGHTED AVERAGE PRICE <UP|DOWN> (DAILY) (EUR/MW/h)、VOLUME <UP|DOWN> (DAILY) (MW)
- 价格：ENTSOE 给的是 RON，按 MNB 汇率（data/fx/MNB.csv，HUF/EUR 和 HUF/RON）折成 EUR：
    EUR = RON × MNB_RON / MNB_EUR；用交付日当天或之前最近一个有牌价的日期（周末 / 节假日沿用前一个工作日）。
    XML 里币种本来就是 EUR 的 TimeSeries 不转换。
- 时间处理和其他 fetch_entsoe 脚本一致：请求边界、日期、年份文件都按 Europe/Brussels；Date 是墙钟小时，
  每个当地小时一行：秋季回拨日 02:00 取两遍的平均，夏令时开始日 02:00 不存在，不写这一行
- 同一方向同一时刻有多个 TimeSeries 时：容量相加，价格按容量加权平均
- 原始是 1 小时就保持 1 小时，不展开；若出现 30 / 15min 的点则按整点汇总（容量取平均，价格按容量加权）。
  Point 省略的位置沿用前一个值（ENTSOE 的压缩规则）
- 模式：
    --days-back 3            取 [今天-3 天, 明天]（默认，每日更新用，覆盖事后修正）
    --start/--end YYYY-MM-DD 补数：按交付日，起止都含
    --direction up|down      只抓一个方向（缺省两个都抓）
"""

import argparse
import io
import os
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://web-api.tp.entsoe.eu/api"
RO_EIC = "10YRO-TEL------P"
LOCAL_TZ = "Europe/Brussels"   # 与其他 fetch_entsoe 脚本的 ENTSOE_DISPLAY_TZ 相同
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

def local_midnight_utc(d):
    return pd.Timestamp(d).tz_localize(LOCAL_TZ).tz_convert("UTC").strftime(ENTSOE_FMT)


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

DELTA = {"PT15M": 15, "PT30M": 30, "PT60M": 60}


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
            res = DELTA.get(period.findtext(".//{*}resolution", default="").strip())
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
            slots = int((end - start) / timedelta(minutes=res))
            last = None
            for pos in range(1, slots + 1):
                last = points.get(pos, last)
                if last is None:
                    continue
                rows.append({"start": start + timedelta(minutes=res * (pos - 1)),
                             "direction": direction, "qty": last[0], "price": last[1], "currency": currency})
    return n_ts, rows


def fetch_rows(start, end):
    rows, chunk_start = [], start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        offset = 0
        while True:
            params = {"documentType": "A81", "businessType": "B95", "Type_MarketAgreement.Type": "A01",
                      "processType": "A51", "controlArea_Domain": RO_EIC,
                      "periodStart": local_midnight_utc(chunk_start),
                      "periodEnd": local_midnight_utc(chunk_end + timedelta(days=1)), "offset": offset}
            n_total = 0
            for xml in fetch_bytes(params, f"A81 {chunk_start}..{chunk_end} offset={offset}"):
                n_ts, part = parse_xml(xml)
                n_total += n_ts
                rows += part
            print(f"     {chunk_start}..{chunk_end} offset={offset}: {n_total} TimeSeries")
            time.sleep(REQUEST_DELAY)
            if n_total < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        chunk_start = chunk_end + timedelta(days=1)
    return rows


# ── 整理成小时、折算欧元 ──────────────────────────────────

def build_frames(rows, ron_per_eur):
    """rows → {direction: DataFrame[Date, price col, volume col]}，整点、CET/CEST 墙钟。"""
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    local = pd.DatetimeIndex(df["start"]).tz_localize("UTC").tz_convert(LOCAL_TZ)
    # 当地墙钟小时 "YYYY/M/D H:00"；1h 原样，更细的点归到所在整点，回拨日 02:00 的两遍落在同一行后取平均
    df[TIME_COL] = [f"{t.year}/{t.month}/{t.day} {t.hour}:00" for t in local]
    # RON → EUR：交付日（当地）当天或之前最近一个 MNB 牌价
    factor = ron_per_eur.reindex(pd.DatetimeIndex(local.tz_localize(None).normalize()), method="ffill").to_numpy()
    df["price_eur"] = df["price"].where(df["currency"] == "EUR", df["price"] * factor)
    if df.loc[df["currency"] == "RON", "price"].notna().any() and pd.isna(factor[(df["currency"] == "RON").to_numpy()]).any():
        sys.exit("MNB.csv 缺少部分交付日的 RON / EUR 牌价")
    unknown = set(df["currency"]) - {"RON", "EUR"}
    if unknown:
        sys.exit(f"未知币种 {unknown}，不会按 RON 折算")
    df["slot"] = [t.isoformat() for t in local]
    df["pq"] = df["price_eur"] * df["qty"]
    out = {}
    for direction, part in df.groupby("direction"):
        # 同一时刻多个 TimeSeries：容量相加，价格按容量加权
        g = part.groupby([TIME_COL, "slot"], sort=False)
        slot = pd.DataFrame({"vol": g["qty"].sum(min_count=1), "pq": g["pq"].sum(min_count=1),
                             "p": g["price_eur"].mean()}).reset_index()
        slot["price"] = (slot["pq"] / slot["vol"].where(slot["vol"] > 0)).fillna(slot["p"])  # 容量为 0 时退化成简单平均
        # 同一整点多个时刻（仅当原始比 1h 细）：容量取平均，价格按容量加权
        slot["pq"] = slot["price"] * slot["vol"]
        h = slot.groupby(TIME_COL, sort=False)
        vol = h["vol"].mean()
        wavg = (h["pq"].sum(min_count=1) / h["vol"].sum().where(h["vol"].sum() > 0)).fillna(h["price"].mean())
        frame = pd.DataFrame({price_col(direction): wavg.round(PRICE_DECIMALS), volume_col(direction): vol})
        out[direction] = frame.reset_index()
    return out


def merge(old, new):
    if old is None or old.empty:
        out = new
    else:
        out = pd.concat([old.reindex(columns=new.columns), new])
    out = out[~out[TIME_COL].duplicated(keep="last")]
    key = pd.to_datetime(out[TIME_COL], format="%Y/%m/%d %H:%M")
    return out.assign(_k=key).sort_values("_k").drop(columns="_k").reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--start", help="YYYY-MM-DD（交付日，含）")
    ap.add_argument("--end", help="YYYY-MM-DD（交付日，含），缺省为明天")
    ap.add_argument("--direction", choices=["up", "down"], help="缺省 up 和 down 都抓")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT_DEFAULT,
                    help="输出到 <out-root>/<up|down>/hourly/RO_<年>.csv")
    ap.add_argument("--mnb", type=Path, default=MNB_PATH_DEFAULT)
    a = ap.parse_args(argv)

    start = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else date.today() - timedelta(days=a.days_back)
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today() + timedelta(days=1)
    print(f"RO aFRR capacity (A81/B95/A01/A51)  {start} .. {end}")
    frames = build_frames(fetch_rows(start, end), load_ron_per_eur(a.mnb))
    if not frames:
        sys.exit("no data rows returned")

    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end + timedelta(days=1))
    for direction in ([a.direction] if a.direction else sorted(frames)):
        if direction not in frames:
            print(f"[{direction}] no data")
            continue
        new = frames[direction]
        t = pd.to_datetime(new[TIME_COL], format="%Y/%m/%d %H:%M")
        new = new[(t >= lo) & (t < hi)]   # 只留请求的交付日
        out_dir = a.out_root / direction / "hourly"
        out_dir.mkdir(parents=True, exist_ok=True)
        for year, part in new.groupby(new[TIME_COL].str.split("/").str[0]):
            path = out_dir / f"RO_{year}.csv"
            old = pd.read_csv(path, dtype={TIME_COL: str}) if path.exists() else None
            out = merge(old, part)
            out.to_csv(path, index=False)
            print(f"{path}: +{len(part)} rows [{part[TIME_COL].iloc[0]} .. {part[TIME_COL].iloc[-1]}], "
                  f"file now {len(out)} rows")


if __name__ == "__main__":
    main()
