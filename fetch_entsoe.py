#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本（并发增强版 - 最小改动）
- 替换原 Energy Charts API
- 数据输出格式与原脚本完全一致
- 支持 incremental / full 模式
- 国家级并发 + 全局限流锁
"""

import argparse
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

ENTSOE_TOKEN = os.environ["ENTSOE_TOKEN"]
API_URL      = "https://web-api.tp.entsoe.eu/api"

COUNTRY_CONFIG = {
    "de": {"tz": "Europe/Berlin",     "bzn_eic": "10Y1001A1001A82H"},
    "fr": {"tz": "Europe/Paris",      "bzn_eic": "10YFR-RTE------C"},
    "es": {"tz": "Europe/Madrid",     "bzn_eic": "10YES-REE------0"},
    "it": {"tz": "Europe/Rome",       "bzn_eic": "10YIT-GRTN-----B"},
    "gr": {"tz": "Europe/Athens",     "bzn_eic": "10YGR-HTSO-----Y"},
    "ro": {"tz": "Europe/Bucharest",  "bzn_eic": "10YRO-TEL------P"},
    "hu": {"tz": "Europe/Budapest",   "bzn_eic": "10YHU-MAVIR----U"},
    "at": {"tz": "Europe/Vienna",     "bzn_eic": "10YAT-APG------L"},
    "pl": {"tz": "Europe/Warsaw",     "bzn_eic": "10YPL-AREA-----S"},
    "sk": {"tz": "Europe/Bratislava", "bzn_eic": "10YSK-SEPS-----K"},
    "rs": {"tz": "Europe/Belgrade",   "bzn_eic": "10YCS-SERBIATSOV"},
    "hr": {"tz": "Europe/Zagreb",     "bzn_eic": "10YHR-HEP------M"},
    "bg": {"tz": "Europe/Sofia",      "bzn_eic": "10YCA-BULGARIA-R"},
}

PSR_TYPE_MAP = {
    "B01": "Biomass",
    "B02": "Fossil Brown Coal/Lignite",
    "B03": "Fossil Coal-derived Gas",
    "B04": "Fossil Gas",
    "B05": "Fossil Hard Coal",
    "B06": "Fossil Oil",
    "B07": "Fossil Oil Shale",
    "B08": "Fossil Peat",
    "B09": "Geothermal",
    "B10": "Hydro Pumped Storage",
    "B11": "Hydro Run-of-river and Poundage",
    "B12": "Hydro Water Reservoir",
    "B13": "Marine",
    "B14": "Nuclear",
    "B15": "Other Renewable",
    "B16": "Solar",
    "B17": "Waste",
    "B18": "Wind Offshore",
    "B19": "Wind Onshore",
    "B20": "Other",
}

COUNTRIES       = list(COUNTRY_CONFIG.keys())
FULL_START_DATE = "2024-01-01"
LOOKBACK_DAYS   = 7

CHUNK_DAYS       = 365
PRICE_CHUNK_DAYS = 30
PAGE_SIZE        = 100
REQUEST_DELAY    = 1.5

DEFAULT_TIMEOUT  = 90
GEN_TIMEOUT      = 120

DATA_DIR = Path("data")
ENTSOE_FMT = "%Y%m%d%H%M"

# ─────────────────────────────────────────────────────────────
# ⭐ 并发控制（关键新增）
# ─────────────────────────────────────────────────────────────

REQUEST_LOCK = Lock()   # 全局限流锁（防429）

# ─────────────────────────────────────────────────────────────
# 日期分段
# ─────────────────────────────────────────────────────────────

def date_chunks(start: str, end_inclusive: str, chunk_days: int = CHUNK_DAYS):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end_inclusive, "%Y-%m-%d")

    chunks, cur = [], s
    while cur <= e:
        nxt = min(cur + timedelta(days=chunk_days), e + timedelta(days=1))
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt
    return chunks


def _to_entsoe_dt(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime(ENTSOE_FMT)

# ─────────────────────────────────────────────────────────────
# API 请求（加锁 + 限流）
# ─────────────────────────────────────────────────────────────

def _get_all_timeseries(base_params: dict, label: str, timeout=DEFAULT_TIMEOUT):
    all_ts = []
    offset = 0

    while True:
        params = {**base_params, "securityToken": ENTSOE_TOKEN}
        if offset > 0:
            params["offset"] = offset

        for attempt in range(5):
            try:
                with REQUEST_LOCK:
                    resp = requests.get(API_URL, params=params, timeout=timeout)
                    time.sleep(REQUEST_DELAY)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                else:
                    print(f"[ERROR] {label}: {e}")
                    return all_ts

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            break

        if "Acknowledgement_MarketDocument" in root.tag:
            break

        page_ts = root.findall(".//{*}TimeSeries")
        all_ts.extend(page_ts)

        if len(page_ts) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return all_ts

# ─────────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────────

def _price_params(bzn, start, end):
    return {
        "documentType": "A44",
        "out_Domain": bzn,
        "in_Domain": bzn,
        "periodStart": _to_entsoe_dt(start),
        "periodEnd": _to_entsoe_dt(end),
    }


def _load_params(bzn, start, end):
    return {
        "documentType": "A65",
        "processType": "A16",
        "outBiddingZone_Domain": bzn,
        "periodStart": _to_entsoe_dt(start),
        "periodEnd": _to_entsoe_dt(end),
    }


def _gen_params(domain, start, end, psr):
    return {
        "documentType": "A75",
        "processType": "A16",
        "in_Domain": domain,
        "psrType": psr,
        "periodStart": _to_entsoe_dt(start),
        "periodEnd": _to_entsoe_dt(end),
    }

# ─────────────────────────────────────────────────────────────
# fetch
# ─────────────────────────────────────────────────────────────

def fetch_price(bzn_eic: str, start: str, end_inclusive: str, tz: str) -> pd.Series | None:
    parts = []
    chunks = date_chunks(start, end_inclusive, chunk_days=PRICE_CHUNK_DAYS)  
    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"     chunk {i}/{len(chunks)}: {cs} → {ce}")
        ts_list = _get_all_timeseries(
            _price_params(bzn_eic, cs, ce),
            f"price {bzn_eic}",
        )
        time.sleep(REQUEST_DELAY)
        s = _ts_list_to_series(ts_list, tz, value_tag="price.amount")
        if s is not None:
            parts.append(s)
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    return combined[~combined.index.duplicated(keep="last")].resample("h").mean()


def fetch_load(bzn_eic: str, start: str, end_inclusive: str, tz: str) -> pd.Series | None:
    parts = []
    chunks = date_chunks(start, end_inclusive)
    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"     chunk {i}/{len(chunks)}: {cs} → {ce}")
        ts_list = _get_all_timeseries(_load_params(bzn_eic, cs, ce),
                                      f"load {bzn_eic}")
        time.sleep(REQUEST_DELAY)
        s = _ts_list_to_series(ts_list, tz, value_tag="quantity")
        if s is not None:
            parts.append(s)
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    return combined[~combined.index.duplicated(keep="last")].resample("h").mean()


def fetch_generation(in_domain: str, start: str, end_inclusive: str,
                     tz: str) -> dict[str, pd.Series]:
    """
    按 psrType 逐一请求 A75，每种类型再按年分段。
    单次请求只含一种能源类型，数据量小，彻底避免 timeout。
    返回 {psr_name: pd.Series}。
    """
    type_series: dict[str, list[pd.Series]] = {}
    chunks = date_chunks(start, end_inclusive, chunk_days=CHUNK_DAYS)

    for psr_code, psr_name in PSR_TYPE_MAP.items():
        psr_parts: list[pd.Series] = []

        for i, (cs, ce) in enumerate(chunks, 1):
            ts_list = _get_all_timeseries(
                _gen_params(in_domain, cs, ce, psr_code),
                f"gen {in_domain} {psr_code}",
                timeout=GEN_TIMEOUT,
            )
            time.sleep(REQUEST_DELAY)

            for ts in ts_list:
                for period in ts.findall(".//{*}Period"):
                    s = _parse_period(period, tz, value_tag="quantity")
                    if not s.empty:
                        psr_parts.append(s)

        if psr_parts:
            combined = pd.concat(psr_parts).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
            type_series[psr_name] = combined.resample("h").mean()
            print(f"     ✓ {psr_name:<35} {type_series[psr_name].notna().sum()} 有效值")
        else:
            # 该国不存在此能源类型，静默跳过
            pass

    return type_series


def build_generation_result(gen_dict: dict[str, pd.Series]) -> dict:
    """从 gen_dict 提取 solar/wind 并构建输出 DataFrame。"""
    if not gen_dict:
        return {}

    solar_s  = gen_dict.get("Solar")
    wind_off = gen_dict.get("Wind Offshore")
    wind_on  = gen_dict.get("Wind Onshore")

    if wind_off is not None and wind_on is not None:
        wind_s = wind_off.add(wind_on, fill_value=0)
    elif wind_off is not None:
        wind_s = wind_off
    elif wind_on is not None:
        wind_s = wind_on
    else:
        wind_s = None

    gen_df = pd.DataFrame(gen_dict)
    cols_to_drop = [c for c in ["Wind Offshore", "Wind Onshore"] if c in gen_df.columns]
    if cols_to_drop:
        gen_df = gen_df.drop(columns=cols_to_drop)
    if wind_s is not None:
        gen_df["Wind"] = wind_s

    return {"solar": solar_s, "wind": wind_s, "generation": gen_df}


# ─────────────────────────────────────────────────────────────
# ⭐ 单国家任务（新增并发核心）
# ─────────────────────────────────────────────────────────────

def process_country(cc, start_date, end_date, cutoff):
    cfg = COUNTRY_CONFIG[cc]
    tz = cfg["tz"]
    bzn = cfg["bzn_eic"]
    col = cc.upper()

    price_cols = {}
    load_cols = {}
    solar_cols = {}
    wind_cols = {}
    residual_cols = {}
    gen_rows = []

    print(f"[{col}]")

    # price
    s = fetch_price(bzn, start_date, end_date, tz)
    if s is not None:
        price_cols[col] = s[s.index < cutoff]

    # load
    s = fetch_load(bzn, start_date, end_date, tz)
    if s is not None:
        load_cols[col] = s[s.index < cutoff]

    # generation
    gen_dict = fetch_generation(bzn, start_date, end_date, tz)
    result = build_generation_result(gen_dict)

    solar_s = result.get("solar")
    wind_s  = result.get("wind")

    if solar_s is not None:
        solar_cols[col] = solar_s[solar_s.index < cutoff]
    if wind_s is not None:
        wind_cols[col] = wind_s[wind_s.index < cutoff]

    load_s = load_cols.get(col)
    if load_s is not None:
        renewables = pd.Series(0.0, index=load_s.index)
        for rs in [solar_s, wind_s]:
            if rs is not None:
                renewables = renewables.add(rs.reindex(load_s.index, fill_value=0), fill_value=0)
        residual_cols[col] = load_s - renewables

    gen_df = result.get("generation", pd.DataFrame())
    if not gen_df.empty:
        gen_df = gen_df[gen_df.index < cutoff]
        for cat in gen_df.columns:
            for dt, val in gen_df[cat].items():
                gen_rows.append({
                    "date": dt.strftime("%Y/%-m/%-d %-H:00"),
                    "country": col,
                    "category": cat,
                    "value": val,
                })

    return price_cols, load_cols, solar_cols, wind_cols, residual_cols, gen_rows

# ─────────────────────────────────────────────────────────────
# main（并发替换）
# ─────────────────────────────────────────────────────────────

def main():
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    start_date = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end_date = yesterday.strftime("%Y-%m-%d")
    cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    print(f"{start_date} → {end_date}")

    results = []

    # ⭐ 并发执行国家
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(process_country, cc, start_date, end_date, cutoff): cc
            for cc in COUNTRIES
        }

        for f in as_completed(futures):
            results.append(f.result())

    # 汇总
    price_cols = {}
    load_cols = {}
    solar_cols = {}
    wind_cols = {}
    residual_cols = {}
    gen_rows = []

    for p, l, s, w, r, g in results:
        price_cols.update(p)
        load_cols.update(l)
        solar_cols.update(s)
        wind_cols.update(w)
        residual_cols.update(r)
        gen_rows.extend(g)

    # 写文件（保持原逻辑）
    merge_and_save_wide(price_cols, DATA_DIR / "price.csv", "price")
    merge_and_save_wide(load_cols, DATA_DIR / "load.csv", "load")
    merge_and_save_wide(solar_cols, DATA_DIR / "solar.csv", "solar")
    merge_and_save_wide(wind_cols, DATA_DIR / "wind.csv", "wind")
    merge_and_save_wide(residual_cols, DATA_DIR / "residual_load.csv", "residual")

    merge_and_save_generation(gen_rows, DATA_DIR / "generation")

    print("DONE")

# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
