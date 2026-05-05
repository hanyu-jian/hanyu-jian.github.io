#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本（增量更新版）
- 替换原 Energy Charts API
- 数据输出格式与原脚本完全一致
- 每次获取最近 LOOKBACK_DAYS 天数据（默认7天）
- 数据更新截止到 today-1（避免当天数据不完整）
- 与现有 CSV 合并，重叠部分以新数据覆盖
- 支持两种模式：
    --mode incremental  仅更新最近7天（默认）
    --mode full         全量拉取 FULL_START_DATE → yesterday

原始数据额外保存至 /raw_data/：
  - /raw_data/A44.csv          价格原始数据（宽表，列=国家）
  - /raw_data/A65.csv          负荷原始数据（宽表，列=国家）
  - /raw_data/generation/{CC}.csv  发电结构原始数据（长表）
  时间轴规则：
  - 原始 15min 数据 → 保持 15min，index 格式 "YYYY/M/D H:MM"
  - 原始 1h 数据    → 展开为 15min（:00/:15/:30/:45 四点值相同）
  - 历史上如果某时段只有 1h 精度，则仍保留 1h 粒度，
    只对真实存在 15min 原始点的时段用 15min 轴。

ENTSOE API 限制：
- 每次请求时间跨度 ≤ 1 年 → 按年分段请求
- 每次响应最多 100 条 TimeSeries → 用 offset 参数翻页
"""

import argparse
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

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

COUNTRIES        = list(COUNTRY_CONFIG.keys())
FULL_START_DATE  = "2026-04-25"
LOOKBACK_DAYS    = 7
REQUEST_DELAY    = 1.5
CHUNK_DAYS       = 365
PRICE_CHUNK_DAYS = 30
PAGE_SIZE        = 100
DEFAULT_TIMEOUT  = 90
GEN_TIMEOUT      = 120
DATA_DIR         = Path("data")
RAW_DIR          = Path("raw_data")
ENTSOE_FMT       = "%Y%m%d%H%M"


# ─────────────────────────────────────────────────────────────
# 日期分段
# ─────────────────────────────────────────────────────────────

def date_chunks(start: str, end_inclusive: str,
                chunk_days: int = CHUNK_DAYS) -> list[tuple[str, str]]:
    s = datetime.strptime(start,         "%Y-%m-%d")
    e = datetime.strptime(end_inclusive, "%Y-%m-%d")
    chunks, cur = [], s
    while cur <= e:
        nxt = min(cur + timedelta(days=chunk_days), e + timedelta(days=1))
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt
    return chunks


# ─────────────────────────────────────────────────────────────
# 底层 API 请求（含 offset 翻页）
# ─────────────────────────────────────────────────────────────

def _to_entsoe_dt(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime(ENTSOE_FMT)


def _get_all_timeseries(base_params: dict, label: str,
                        timeout: int = DEFAULT_TIMEOUT) -> list[ET.Element]:
    all_ts = []
    offset = 0

    while True:
        params = {**base_params, "securityToken": ENTSOE_TOKEN}
        if offset > 0:
            params["offset"] = offset

        for attempt in range(5):
            try:
                resp = requests.get(API_URL, params=params, timeout=timeout)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < 4:
                    sleep = 2 ** attempt
                    print(f"      [RETRY] {label} offset={offset} in {sleep}s")
                    time.sleep(sleep)
                else:
                    print(f"      [ERROR] {label} offset={offset}: {e}")
                    return all_ts

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f"      [XML ERROR] {label} offset={offset}: {e}")
            break

        if "Acknowledgement_MarketDocument" in root.tag:
            reason = root.findtext(".//{*}Reason/{*}text", default="unknown")
            print(f"      [API ERROR] {label} offset={offset}: {reason}")
            break

        page_ts = root.findall(".//{*}TimeSeries")
        all_ts.extend(page_ts)

        if len(page_ts) == 0 or len(page_ts) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        print(f"      → 翻页 offset={offset}（本页 {len(page_ts)} 条）")
        time.sleep(REQUEST_DELAY)

    return all_ts


# ─────────────────────────────────────────────────────────────
# 各类型请求参数
# ─────────────────────────────────────────────────────────────

def _price_params(bzn_eic: str, start: str, end: str) -> dict:
    return {
        "documentType": "A44",
        "out_Domain":   bzn_eic,
        "in_Domain":    bzn_eic,
        "periodStart":  _to_entsoe_dt(start),
        "periodEnd":    _to_entsoe_dt(end),
    }


def _load_params(bzn_eic: str, start: str, end: str) -> dict:
    return {
        "documentType":          "A65",
        "processType":           "A16",
        "outBiddingZone_Domain": bzn_eic,
        "periodStart":           _to_entsoe_dt(start),
        "periodEnd":             _to_entsoe_dt(end),
    }


def _gen_params(in_domain: str, start: str, end: str, psr_type: str) -> dict:
    return {
        "documentType": "A75",
        "processType":  "A16",
        "in_Domain":    in_domain,
        "psrType":      psr_type,
        "periodStart":  _to_entsoe_dt(start),
        "periodEnd":    _to_entsoe_dt(end),
    }


# ─────────────────────────────────────────────────────────────
# XML Period 解析（前向填充缺失 position）
# ─────────────────────────────────────────────────────────────

DELTA_MAP = {
    "PT15M": timedelta(minutes=15),
    "PT30M": timedelta(minutes=30),
    "PT60M": timedelta(hours=1),
}
RES_MINUTES = {
    "PT15M": 15,
    "PT30M": 30,
    "PT60M": 60,
}


def _parse_period_raw(period_el: ET.Element, tz: str,
                      value_tag: str = "quantity") -> tuple[pd.Series, int]:
    """
    解析单个 <Period>，返回 (Series[本地时间 → float], 分辨率分钟数)。
    使用 <end> 标签推算完整时间轴，缺失 position 前向填充。
    正确处理 ENTSOE 规范中省略重复值的情况（如价格连续 -500 段）。
    """
    start_el = period_el.find(".//{*}start")
    end_el   = period_el.find(".//{*}end")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")

    if start_el is None or res_el is None:
        return pd.Series(dtype=float), 60

    start_utc = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    res_str   = res_el.text.strip()
    delta     = DELTA_MAP.get(res_str, timedelta(hours=1))
    res_min   = RES_MINUTES.get(res_str, 60)

    point_values: dict[int, float] = {}
    for pt in points:
        pos_el = pt.find(".//{*}position")
        val_el = pt.find(f"./{{*}}{value_tag}")
        if val_el is None:
            fallback = "quantity" if value_tag == "price.amount" else "price.amount"
            val_el = pt.find(f"./{{*}}{fallback}")
        if pos_el is None:
            continue
        try:
            pos = int(pos_el.text)
            raw = val_el.text.strip() if (val_el is not None and val_el.text) else None
            if raw is not None:
                point_values[pos] = float(raw)
        except (TypeError, ValueError):
            pass

    # 用 <end> 推算完整 slot 数，避免因省略重复值导致时间轴截断
    if end_el is not None:
        end_utc = datetime.strptime(end_el.text.strip(), "%Y-%m-%dT%H:%MZ")
        total_slots = int((end_utc - start_utc) / delta)
    elif point_values:
        total_slots = max(point_values.keys())
    else:
        return pd.Series(dtype=float), res_min

    if total_slots <= 0:
        return pd.Series(dtype=float), res_min

    # 构建完整时间轴，缺失 position 前向填充
    records: dict = {}
    last_val = None
    for pos in range(1, total_slots + 1):
        ts = start_utc + (pos - 1) * delta
        if pos in point_values:
            last_val = point_values[pos]
        if last_val is not None:
            records[ts] = last_val

    if not records:
        return pd.Series(dtype=float), res_min

    s = pd.Series(records)
    s.index = pd.DatetimeIndex(s.index, tz="UTC").tz_convert(tz).tz_localize(None)
    return s, res_min


# ─────────────────────────────────────────────────────────────
# Series 构建
# ─────────────────────────────────────────────────────────────

def _ts_list_to_raw_series(ts_list: list[ET.Element], tz: str,
                           value_tag: str = "quantity") -> pd.Series | None:
    """合并为原始分辨率 Series，不做 resample。"""
    parts = []
    for ts in ts_list:
        for period in ts.findall(".//{*}Period"):
            s, _res = _parse_period_raw(period, tz, value_tag)
            if not s.empty:
                parts.append(s)
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined


def _ts_list_to_series(ts_list: list[ET.Element], tz: str,
                       value_tag: str = "quantity") -> pd.Series | None:
    """合并后 resample 到 1h（用于 data/ 目录写入）。"""
    raw = _ts_list_to_raw_series(ts_list, tz, value_tag)
    if raw is None:
        return None
    return raw.resample("h").mean()


# ─────────────────────────────────────────────────────────────
# 统一到 15min 轴
# ─────────────────────────────────────────────────────────────

def _detect_resolution_minutes(s: pd.Series) -> int:
    if len(s) < 2:
        return 60
    diffs = pd.Series(s.index).diff().dropna()
    min_minutes = int(diffs.min().total_seconds() / 60)
    if min_minutes <= 15:
        return 15
    if min_minutes <= 30:
        return 30
    return 60


def normalize_raw_series_to_15min(s: pd.Series) -> pd.Series:
    """
    混合分辨率 Series → 统一 15min 轴。
    - 与前一点间隔 ≤ 15min → 直接保留（真实 15min 数据）
    - 与前一点间隔 > 15min → 展开为 :00/:15/:30/:45 四点（值相同）
    """
    if s.empty:
        return s

    s = s.sort_index()
    if len(s) < 2:
        return s

    idx   = pd.DatetimeIndex(s.index)
    diffs = pd.Series(idx, index=idx).diff().dt.total_seconds().div(60)
    fine_mask = (diffs <= 15)

    records: dict = {}
    for i, (ts, val) in enumerate(s.items()):
        if i == 0:
            # 第一个点：看后一点间隔判断
            if len(diffs) > 1 and diffs.iloc[1] <= 15:
                records[ts] = val
            else:
                for q in range(4):
                    records[ts + timedelta(minutes=15 * q)] = val
        elif fine_mask.iloc[i]:
            records[ts] = val
        else:
            for q in range(4):
                records[ts + timedelta(minutes=15 * q)] = val

    result = pd.Series(records).sort_index()
    return result[~result.index.duplicated(keep="last")]


# ─────────────────────────────────────────────────────────────
# 时间排序工具
# 用 pd.to_datetime 自动推断格式，避免 %-m 等平台相关符号解析失败
# ─────────────────────────────────────────────────────────────

def _sort_index_by_time(df: pd.DataFrame) -> pd.DataFrame:
    """字符串 index → datetime 排序 → 返回带 datetime index 的 df（调用方负责转回字符串）。"""
    dt_idx = pd.to_datetime(df.index, errors="coerce")
    df = df[dt_idx.notna()].copy()
    df.index = dt_idx[dt_idx.notna()]
    df.sort_index(inplace=True)
    return df


def _sort_col_by_time(df: pd.DataFrame, date_col: str,
                      secondary: str | None = None) -> pd.DataFrame:
    """长表按时间列排序，避免字符串排序错误。"""
    df = df.copy()
    df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
    sort_cols = ["_dt"] + ([secondary] if secondary else [])
    df = df[df["_dt"].notna()].sort_values(sort_cols).drop(columns="_dt").reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────
# 时间戳格式化（用 Python datetime，不依赖 strftime %-m 平台符号）
# ─────────────────────────────────────────────────────────────

def _fmt_dt_hourly(dt: pd.Timestamp) -> str:
    """2024/1/5 8:00"""
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:00"


def _fmt_dt_15min(dt: pd.Timestamp) -> str:
    """2024/1/5 8:15"""
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"


def fmt_index(index: pd.DatetimeIndex) -> list[str]:
    return [_fmt_dt_hourly(dt) for dt in index]


def fmt_index_15min(index: pd.DatetimeIndex) -> list[str]:
    return [_fmt_dt_15min(dt) for dt in index]


# ─────────────────────────────────────────────────────────────
# fetch
# ─────────────────────────────────────────────────────────────

def fetch_price(bzn_eic: str, start: str, end_inclusive: str,
                tz: str) -> tuple[pd.Series | None, pd.Series | None]:
    h_parts, raw_parts = [], []
    chunks = date_chunks(start, end_inclusive, chunk_days=PRICE_CHUNK_DAYS)
    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"     chunk {i}/{len(chunks)}: {cs} → {ce}")
        ts_list = _get_all_timeseries(_price_params(bzn_eic, cs, ce), f"price {bzn_eic}")
        time.sleep(REQUEST_DELAY)
        raw = _ts_list_to_raw_series(ts_list, tz, value_tag="price.amount")
        if raw is not None:
            raw_parts.append(raw)
            h_parts.append(raw.resample("h").mean())

    def _combine(parts):
        if not parts:
            return None
        c = pd.concat(parts).sort_index()
        return c[~c.index.duplicated(keep="last")]

    hourly = _combine(h_parts)
    if hourly is not None:
        hourly = hourly.resample("h").mean()
    return hourly, _combine(raw_parts)


def fetch_load(bzn_eic: str, start: str, end_inclusive: str,
               tz: str) -> tuple[pd.Series | None, pd.Series | None]:
    h_parts, raw_parts = [], []
    chunks = date_chunks(start, end_inclusive)
    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"     chunk {i}/{len(chunks)}: {cs} → {ce}")
        ts_list = _get_all_timeseries(_load_params(bzn_eic, cs, ce), f"load {bzn_eic}")
        time.sleep(REQUEST_DELAY)
        raw = _ts_list_to_raw_series(ts_list, tz, value_tag="quantity")
        if raw is not None:
            raw_parts.append(raw)
            h_parts.append(raw.resample("h").mean())

    def _combine(parts):
        if not parts:
            return None
        c = pd.concat(parts).sort_index()
        return c[~c.index.duplicated(keep="last")]

    hourly = _combine(h_parts)
    if hourly is not None:
        hourly = hourly.resample("h").mean()
    return hourly, _combine(raw_parts)


def fetch_generation(in_domain: str, start: str, end_inclusive: str,
                     tz: str) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    hourly_series: dict[str, pd.Series] = {}
    raw_series:    dict[str, pd.Series] = {}
    chunks = date_chunks(start, end_inclusive, chunk_days=CHUNK_DAYS)

    for psr_code, psr_name in PSR_TYPE_MAP.items():
        h_parts, r_parts = [], []

        for cs, ce in chunks:
            ts_list = _get_all_timeseries(
                _gen_params(in_domain, cs, ce, psr_code),
                f"gen {in_domain} {psr_code}",
                timeout=GEN_TIMEOUT,
            )
            time.sleep(REQUEST_DELAY)
            for ts in ts_list:
                for period in ts.findall(".//{*}Period"):
                    s, _res = _parse_period_raw(period, tz, value_tag="quantity")
                    if not s.empty:
                        r_parts.append(s)
                        h_parts.append(s.resample("h").mean())

        if r_parts:
            raw_c = pd.concat(r_parts).sort_index()
            raw_c = raw_c[~raw_c.index.duplicated(keep="last")]
            raw_series[psr_name] = raw_c

            h_c = pd.concat(h_parts).sort_index()
            h_c = h_c[~h_c.index.duplicated(keep="last")].resample("h").mean()
            hourly_series[psr_name] = h_c
            print(f"     ✓ {psr_name:<35} {h_c.notna().sum()} 有效值(1h) | "
                  f"{raw_c.notna().sum()} 原始点")

    return hourly_series, raw_series


def build_generation_result(gen_dict: dict[str, pd.Series]) -> dict:
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
# CSV 合并写入（data/ 目录）
# ─────────────────────────────────────────────────────────────

def merge_and_save_wide(new_cols: dict[str, pd.Series], path: Path, label: str):
    if not new_cols:
        print(f"  [SKIP] {label}: 无新数据")
        return

    new_df = pd.DataFrame(new_cols)

    if path.exists():
        old_df = pd.read_csv(path, index_col=0)
        old_df = old_df.apply(pd.to_numeric, errors="coerce")
        new_df.index = fmt_index(new_df.index)
        all_cols = old_df.columns.union(new_df.columns)
        old_df   = old_df.reindex(columns=all_cols)
        new_df   = new_df.reindex(columns=all_cols)
        old_df.update(new_df)
        merged = old_df.combine_first(new_df)
        merged.index.name = "Date"
        # 按时间排序（不能用字符串排序，"1/9" 会排在 "1/10" 后面）
        merged = _sort_index_by_time(merged)
        merged.index = fmt_index(merged.index)
        merged.index.name = "Date"
        merged.to_csv(path)
        print(f"  ✓ {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 列")
    else:
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df = _sort_index_by_time(new_df)
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df.to_csv(path)
        print(f"  ✓ {path.name}: 新建 {len(new_df)} 行 × {len(new_df.columns)} 列")


def merge_and_save_generation(new_rows: list[dict], gen_dir: Path):
    if not new_rows:
        print("  [SKIP] generation: 无新数据")
        return

    gen_dir.mkdir(exist_ok=True)
    df_new = pd.DataFrame(new_rows, columns=["date", "country", "category", "value"])
    df_new["value"] = pd.to_numeric(df_new["value"], errors="coerce")

    for country, grp_new in df_new.groupby("country"):
        path    = gen_dir / f"{country}.csv"
        grp_new = grp_new.drop(columns="country").reset_index(drop=True)

        if path.exists():
            grp_old = pd.read_csv(path)
            grp_old["value"] = pd.to_numeric(grp_old["value"], errors="coerce")
            merged = (
                pd.concat([grp_old, grp_new])
                  .drop_duplicates(subset=["date", "category"], keep="last")
            )
            merged = _sort_col_by_time(merged, "date", secondary="category")
            merged.to_csv(path, index=False)
            print(f"  ✓ generation/{country}.csv: 合并后 {len(merged)} 行")
        else:
            grp_new = _sort_col_by_time(grp_new, "date", secondary="category")
            grp_new.to_csv(path, index=False)
            print(f"  ✓ generation/{country}.csv: 新建 {len(grp_new)} 行")


# ─────────────────────────────────────────────────────────────
# 原始数据写入 raw_data/
# ─────────────────────────────────────────────────────────────

def merge_and_save_raw_wide(new_raw_cols: dict[str, pd.Series],
                            path: Path, label: str):
    if not new_raw_cols:
        print(f"  [SKIP RAW] {label}: 无新数据")
        return

    normalized: dict[str, pd.Series] = {}
    for col, s in new_raw_cols.items():
        if s is None or s.empty:
            continue
        normalized[col] = normalize_raw_series_to_15min(s)

    if not normalized:
        print(f"  [SKIP RAW] {label}: 标准化后无数据")
        return

    all_idx = pd.concat(
        [s.rename("v") for s in normalized.values()]
    ).index.unique().sort_values()

    new_df = pd.DataFrame({col: s.reindex(all_idx) for col, s in normalized.items()})
    new_df.index = fmt_index_15min(all_idx)
    new_df.index.name = "Date"

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        old_df = pd.read_csv(path, index_col=0)
        old_df = old_df.apply(pd.to_numeric, errors="coerce")
        all_cols = old_df.columns.union(new_df.columns)
        old_df   = old_df.reindex(columns=all_cols)
        new_df   = new_df.reindex(columns=all_cols)
        old_df.update(new_df)
        merged = old_df.combine_first(new_df)
        merged.index.name = "Date"
        merged = _sort_index_by_time(merged)
        merged.index = fmt_index_15min(merged.index)
        merged.index.name = "Date"
        merged.to_csv(path)
        print(f"  ✓ RAW {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 列")
    else:
        new_df = _sort_index_by_time(new_df)
        new_df.index = fmt_index_15min(new_df.index)
        new_df.index.name = "Date"
        new_df.to_csv(path)
        print(f"  ✓ RAW {path.name}: 新建 {len(new_df)} 行 × {len(new_df.columns)} 列")


def merge_and_save_raw_generation(raw_gen_by_country: dict[str, dict[str, pd.Series]],
                                  raw_gen_dir: Path):
    if not raw_gen_by_country:
        print("  [SKIP RAW] generation: 无新数据")
        return

    raw_gen_dir.mkdir(parents=True, exist_ok=True)

    for cc_upper, gen_raw_dict in raw_gen_by_country.items():
        if not gen_raw_dict:
            continue

        rows = []
        for psr_name, s in gen_raw_dict.items():
            if s is None or s.empty:
                continue
            s15 = normalize_raw_series_to_15min(s)
            for ts, val in s15.items():
                rows.append({
                    "date":     _fmt_dt_15min(ts),
                    "category": psr_name,
                    "value":    val,
                })

        if not rows:
            continue

        df_new = pd.DataFrame(rows, columns=["date", "category", "value"])
        df_new["value"] = pd.to_numeric(df_new["value"], errors="coerce")

        path = raw_gen_dir / f"{cc_upper}.csv"
        if path.exists():
            df_old = pd.read_csv(path)
            df_old["value"] = pd.to_numeric(df_old["value"], errors="coerce")
            merged = (
                pd.concat([df_old, df_new])
                  .drop_duplicates(subset=["date", "category"], keep="last")
            )
            merged = _sort_col_by_time(merged, "date", secondary="category")
            merged.to_csv(path, index=False)
            print(f"  ✓ RAW generation/{cc_upper}.csv: 合并后 {len(merged)} 行")
        else:
            df_new = _sort_col_by_time(df_new, "date", secondary="category")
            df_new.to_csv(path, index=False)
            print(f"  ✓ RAW generation/{cc_upper}.csv: 新建 {len(df_new)} 行")


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ENTSOE 数据更新")
    parser.add_argument(
        "--mode", choices=["incremental", "full"], default="incremental",
        help="incremental=仅最近7天(默认), full=全量拉取"
    )
    args = parser.parse_args()

    today     = datetime.now()
    yesterday = today - timedelta(days=1)
    end_date  = yesterday.strftime("%Y-%m-%d")

    if args.mode == "full":
        start_date = FULL_START_DATE
        mode_label = "全量模式"
    else:
        start_date = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        mode_label = f"增量模式（最近 {LOOKBACK_DAYS} 天）"

    cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    print("=" * 62)
    print(f"ENTSOE 数据更新  [{mode_label}]")
    print("=" * 62)
    print(f"数据范围: {start_date} → {end_date}（含）")
    print(f"国家数量: {len(COUNTRIES)}")
    print()

    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    price_cols:    dict[str, pd.Series] = {}
    load_cols:     dict[str, pd.Series] = {}
    solar_cols:    dict[str, pd.Series] = {}
    wind_cols:     dict[str, pd.Series] = {}
    residual_cols: dict[str, pd.Series] = {}
    gen_rows:      list[dict]           = []

    raw_price_cols:     dict[str, pd.Series]            = {}
    raw_load_cols:      dict[str, pd.Series]            = {}
    raw_gen_by_country: dict[str, dict[str, pd.Series]] = {}

    for cc in COUNTRIES:
        cfg     = COUNTRY_CONFIG[cc]
        tz      = cfg["tz"]
        bzn_eic = cfg["bzn_eic"]
        col     = cc.upper()

        print(f"[{col}]")

        # ── 价格 A44 ────────────────────────────────────────
        print(f"  → A44 price  eic={bzn_eic}")
        s_hourly, s_raw = fetch_price(bzn_eic, start_date, end_date, tz)
        if s_hourly is not None:
            price_cols[col]     = s_hourly[s_hourly.index < cutoff]
            raw_price_cols[col] = s_raw[s_raw.index < cutoff]
            print(f"     ✓ {price_cols[col].notna().sum()} 有效值(1h) | "
                  f"{raw_price_cols[col].notna().sum()} 原始点")
        else:
            print(f"     [WARN] 无价格数据")

        # ── 负荷 A65 ────────────────────────────────────────
        print(f"  → A65 load   eic={bzn_eic}")
        s_hourly, s_raw = fetch_load(bzn_eic, start_date, end_date, tz)
        if s_hourly is not None:
            load_cols[col]     = s_hourly[s_hourly.index < cutoff]
            raw_load_cols[col] = s_raw[s_raw.index < cutoff]
            print(f"     ✓ {load_cols[col].notna().sum()} 有效值(1h) | "
                  f"{raw_load_cols[col].notna().sum()} 原始点")
        else:
            print(f"     [WARN] 无负荷数据")

        # ── 发电结构 A75 ────────────────────────────────────
        print(f"  → A75 gen    in_Domain={bzn_eic}")
        hourly_dict, raw_dict = fetch_generation(bzn_eic, start_date, end_date, tz)
        result = build_generation_result(hourly_dict)

        if raw_dict:
            raw_gen_by_country[col] = {
                k: v[v.index < cutoff] for k, v in raw_dict.items()
            }

        for field, target_dict, lbl in [
            ("solar", solar_cols, "solar"),
            ("wind",  wind_cols,  "wind"),
        ]:
            s = result.get(field)
            if s is not None:
                target_dict[col] = s[s.index < cutoff]
                print(f"     ✓ {lbl:<8} {target_dict[col].notna().sum()} 有效值")
            else:
                print(f"     [WARN] {lbl} 无数据")

        # residual load = load − (solar + wind)
        load_s  = load_cols.get(col)
        solar_s = solar_cols.get(col)
        wind_s  = wind_cols.get(col)
        if load_s is not None and (solar_s is not None or wind_s is not None):
            renewables = pd.Series(0.0, index=load_s.index)
            for rs in [solar_s, wind_s]:
                if rs is not None:
                    renewables = renewables.add(
                        rs.reindex(load_s.index, fill_value=0), fill_value=0)
            residual_cols[col] = load_s - renewables
            print(f"     ✓ residual  {residual_cols[col].notna().sum()} 有效值（计算值）")

        # generation 明细 → 长表（data/generation/）
        gen_df: pd.DataFrame = result.get("generation", pd.DataFrame())
        if not gen_df.empty:
            gen_df = gen_df[gen_df.index < cutoff]
            for cat in gen_df.columns:
                for dt, val in gen_df[cat].items():
                    gen_rows.append({
                        "date":     _fmt_dt_hourly(dt),
                        "country":  col,
                        "category": cat,
                        "value":    val,
                    })
            print(f"     ✓ generation {len(gen_df.columns)} 类型, {len(gen_df)} 行")

        print()

    # ── 写 data/ 文件 ────────────────────────────────────────
    print("保存/合并文件 [data/]...")
    merge_and_save_wide(price_cols,    DATA_DIR / "price.csv",         "price")
    merge_and_save_wide(load_cols,     DATA_DIR / "load.csv",          "load")
    merge_and_save_wide(solar_cols,    DATA_DIR / "solar.csv",         "solar")
    merge_and_save_wide(wind_cols,     DATA_DIR / "wind.csv",          "wind")
    merge_and_save_wide(residual_cols, DATA_DIR / "residual_load.csv", "residual_load")
    merge_and_save_generation(gen_rows, DATA_DIR / "generation")
    print()

    # ── 写 raw_data/ 文件 ────────────────────────────────────
    print("保存/合并原始数据 [raw_data/]...")
    merge_and_save_raw_wide(raw_price_cols, RAW_DIR / "A44.csv", "A44 price")
    merge_and_save_raw_wide(raw_load_cols,  RAW_DIR / "A65.csv", "A65 load")
    merge_and_save_raw_generation(raw_gen_by_country, RAW_DIR / "generation")
    print()

    print("=" * 62)
    print(f"完成！{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
