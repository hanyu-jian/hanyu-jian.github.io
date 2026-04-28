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

COUNTRIES       = list(COUNTRY_CONFIG.keys())
FULL_START_DATE = "2024-01-01"
LOOKBACK_DAYS    = 7
REQUEST_DELAY    = 1.5      # 每次请求后等待（秒）
CHUNK_DAYS       = 365      # 每段最多天数（ENTSOE 限制1年）
PRICE_CHUNK_DAYS = 30       # 防止price 503 error
PAGE_SIZE        = 100      # ENTSOE 每页最多 TimeSeries 数
DEFAULT_TIMEOUT  = 90       # price/load 超时（秒）
GEN_TIMEOUT      = 120      # A75 per-psrType 超时（单类型数据量小，120秒足够）
DATA_DIR         = Path("data")
ENTSOE_FMT       = "%Y%m%d%H%M"


# ─────────────────────────────────────────────────────────────
# 日期分段
# ─────────────────────────────────────────────────────────────

def date_chunks(start: str, end_inclusive: str,
                chunk_days: int = CHUNK_DAYS) -> list[tuple[str, str]]:
    """
    将 [start, end_inclusive] 切成最多 chunk_days 天的子区间。
    返回 [(chunk_start, chunk_end_exclusive), ...]，end 已 +1 天（ENTSOE 左闭右开）。
    """
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
    """
    翻页拉取所有 TimeSeries 元素（offset=0, 100, 200, ...）。
    返回所有页的 <TimeSeries> 列表合并结果。
    """
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

        # ENTSOE 错误响应（Acknowledgement 文档）
        if "Acknowledgement_MarketDocument" in root.tag:
            reason = root.findtext(".//{*}Reason/{*}text", default="unknown")
            print(f"      [API ERROR] {label} offset={offset}: {reason}")
            break

        page_ts = root.findall(".//{*}TimeSeries")
        all_ts.extend(page_ts)


        if len(page_ts) == 0:
            break
        if len(page_ts) < PAGE_SIZE:
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
        "in_Domain":    in_domain,   # Control Area / Bidding Zone / Country
        "psrType":      psr_type,    # 按能源类型单独请求，避免大数据量 timeout
        "periodStart":  _to_entsoe_dt(start),
        "periodEnd":    _to_entsoe_dt(end),
    }


# ─────────────────────────────────────────────────────────────
# XML Period 解析
# ─────────────────────────────────────────────────────────────

def _parse_period(period_el: ET.Element, tz: str, value_tag: str = "quantity") -> pd.Series:
    """
    解析单个 <Period>，支持 PT15M / PT30M / PT60M 分辨率。
    value_tag: "price.amount"（A44）或 "quantity"（A65 / A75）
    """
    start_el = period_el.find(".//{*}start")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")

    if start_el is None or res_el is None or not points:
        return pd.Series(dtype=float)

    start_utc = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    delta_map  = {"PT15M": timedelta(minutes=15),
                  "PT30M": timedelta(minutes=30),
                  "PT60M": timedelta(hours=1)}
    delta = delta_map.get(res_el.text.strip(), timedelta(hours=1))

    records = {}
    for pt in points:
        pos_el = pt.find(".//{*}position")
        # 尝试指定 tag，再回退到另一个
        val_el = pt.find(f"./{{*}}{value_tag}")
        if val_el is None:
            fallback = "quantity" if value_tag == "price.amount" else "price.amount"
            val_el = pt.find(f"./{{*}}{fallback}")
        if pos_el is None or val_el is None:
            continue
        try:
            records[start_utc + (int(pos_el.text) - 1) * delta] = float(val_el.text)
        except (TypeError, ValueError):
            pass

    if not records:
        return pd.Series(dtype=float)

    s = pd.Series(records)
    s.index = pd.DatetimeIndex(s.index, tz="UTC").tz_convert(tz).tz_localize(None)
    return s.resample("h").mean()


def _ts_list_to_series(ts_list: list[ET.Element], tz: str,
                       value_tag: str = "quantity") -> pd.Series | None:
    """将一批 TimeSeries 的所有 Period 合并为单条 Series。"""
    parts = []
    for ts in ts_list:
        for period in ts.findall(".//{*}Period"):
            s = _parse_period(period, tz, value_tag)
            if not s.empty:
                parts.append(s)
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.resample("h").mean()


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
# CSV 合并写入（与原脚本完全一致）
# ─────────────────────────────────────────────────────────────

def fmt_index(index: pd.DatetimeIndex) -> list[str]:
    return [dt.strftime("%Y/%-m/%-d %-H:00") for dt in index]


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
        merged.sort_index(inplace=True)
        merged.to_csv(path)
        print(f"  ✓ {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 列")
    else:
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df.sort_index(inplace=True)
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
                  .sort_values(["date", "category"])
                  .reset_index(drop=True)
            )
            merged.to_csv(path, index=False)
            print(f"  ✓ generation/{country}.csv: 合并后 {len(merged)} 行")
        else:
            grp_new.sort_values(["date", "category"]).to_csv(path, index=False)
            print(f"  ✓ generation/{country}.csv: 新建 {len(grp_new)} 行")


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
    end_date  = yesterday.strftime("%Y-%m-%d")   # 截止 today-1

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

    price_cols:    dict[str, pd.Series] = {}
    load_cols:     dict[str, pd.Series] = {}
    solar_cols:    dict[str, pd.Series] = {}
    wind_cols:     dict[str, pd.Series] = {}
    residual_cols: dict[str, pd.Series] = {}
    gen_rows:      list[dict]           = []

    for cc in COUNTRIES:
        cfg     = COUNTRY_CONFIG[cc]
        tz      = cfg["tz"]
        bzn_eic = cfg["bzn_eic"]
        col     = cc.upper()

        print(f"[{col}]")

        # ── 价格 A44 ────────────────────────────────────────
        print(f"  → A44 price  eic={bzn_eic}")
        s = fetch_price(bzn_eic, start_date, end_date, tz)
        if s is not None:
            price_cols[col] = s[s.index < cutoff]
            print(f"     ✓ {price_cols[col].notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无价格数据")

        # ── 负荷 A65 ────────────────────────────────────────
        print(f"  → A65 load   eic={bzn_eic}")
        s = fetch_load(bzn_eic, start_date, end_date, tz)
        if s is not None:
            load_cols[col] = s[s.index < cutoff]
            print(f"     ✓ {load_cols[col].notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无负荷数据")

        # ── 发电结构 A75 ────────────────────────────────────
        print(f"  → A75 gen    in_Domain={bzn_eic}")
        gen_dict = fetch_generation(bzn_eic, start_date, end_date, tz)
        result   = build_generation_result(gen_dict)

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

        # generation 明细 → 长表
        gen_df: pd.DataFrame = result.get("generation", pd.DataFrame())
        if not gen_df.empty:
            gen_df = gen_df[gen_df.index < cutoff]
            for cat in gen_df.columns:
                for dt, val in gen_df[cat].items():
                    gen_rows.append({
                        "date":     dt.strftime("%Y/%-m/%-d %-H:00"),
                        "country":  col,
                        "category": cat,
                        "value":    val,
                    })
            print(f"     ✓ generation {len(gen_df.columns)} 类型, {len(gen_df)} 行")

        print()

    # ── 写文件 ───────────────────────────────────────────────
    print("保存/合并文件...")
    merge_and_save_wide(price_cols,    DATA_DIR / "price.csv",         "price")
    merge_and_save_wide(load_cols,     DATA_DIR / "load.csv",          "load")
    merge_and_save_wide(solar_cols,    DATA_DIR / "solar.csv",         "solar")
    merge_and_save_wide(wind_cols,     DATA_DIR / "wind.csv",          "wind")
    merge_and_save_wide(residual_cols, DATA_DIR / "residual_load.csv", "residual_load")
    merge_and_save_generation(gen_rows, DATA_DIR / "generation")

    print()
    print("=" * 62)
    print(f"完成！{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
    
