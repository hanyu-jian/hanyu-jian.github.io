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
"""

import argparse
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

ENTSOE_TOKEN = os.environ["ENTSOE_TOKEN"]
API_URL      = "https://web-api.tp.entsoe.eu/api"

# ENTSOE EIC 
# documentType=A44 (price): out_Domain = in_Domain = bidding zone EIC
# documentType=A65 (load):  outBiddingZone_Domain
# documentType=A75 (generation per type): in_Domain = control area EIC
COUNTRY_CONFIG = {
    "de": {
        "tz":           "Europe/Berlin",
        "bzn_eic":      "10Y1001A1001A82H",   # DE-LU bidding zone
        "ca_eic":       "10Y1001A1001A83F",   # DE control area (for generation)
    },
    "fr": {
        "tz":           "Europe/Paris",
        "bzn_eic":      "10YFR-RTE------C",
        "ca_eic":       "10YFR-RTE------C",
    },
    "es": {
        "tz":           "Europe/Madrid",
        "bzn_eic":      "10YES-REE------0",
        "ca_eic":       "10YES-REE------0",
    },
    "it": {
        "tz":           "Europe/Rome",
        "bzn_eic":      "10YIT-GRTN-----B",   # IT overall (use North for price if needed)
        "ca_eic":       "10YIT-GRTN-----B",
    },
    "gr": {
        "tz":           "Europe/Athens",
        "bzn_eic":      "10YGR-HTSO-----Y",
        "ca_eic":       "10YGR-HTSO-----Y",
    },
    "ro": {
        "tz":           "Europe/Bucharest",
        "bzn_eic":      "10YRO-TEL------P",
        "ca_eic":       "10YRO-TEL------P",
    },
    "hu": {
        "tz":           "Europe/Budapest",
        "bzn_eic":      "10YHU-MAVIR----U",
        "ca_eic":       "10YHU-MAVIR----U",
    },
    "at": {
        "tz":           "Europe/Vienna",
        "bzn_eic":      "10YAT-APG------L",
        "ca_eic":       "10YAT-APG------L",
    },
    "pl": {
        "tz":           "Europe/Warsaw",
        "bzn_eic":      "10YPL-AREA-----S",
        "ca_eic":       "10YPL-AREA-----S",
    },
    "sk": {
        "tz":           "Europe/Bratislava",
        "bzn_eic":      "10YSK-SEPS-----K",
        "ca_eic":       "10YSK-SEPS-----K",
    },
    "rs": {
        "tz":           "Europe/Belgrade",
        "bzn_eic":      "10YCS-SERBIATSOV",
        "ca_eic":       "10YCS-SERBIATSOV",
    },
    "hr": {
        "tz":           "Europe/Zagreb",
        "bzn_eic":      "10YHR-HEP------M",
        "ca_eic":       "10YHR-HEP------M",
    },
    "bg": {
        "tz":           "Europe/Sofia",
        "bzn_eic":      "10YCA-BULGARIA-R",
        "ca_eic":       "10YCA-BULGARIA-R",
    },
}

# ENTSOE generation psrType → 可读名称（与 Energy Charts 风格对齐）
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
FULL_START_DATE = "2023-01-01"
LOOKBACK_DAYS   = 7
REQUEST_DELAY   = 1.0    # ENTSOE 限速较严，保守一些
DATA_DIR        = Path("data")

# ENTSOE 时间格式：YYYYMMDDHHmm（UTC）
ENTSOE_FMT = "%Y%m%d%H%M"
NS = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}


# ─────────────────────────────────────────────────────────────
# ENTSOE API 请求
# ─────────────────────────────────────────────────────────────

def _entsoe_get(params: dict, label: str) -> ET.Element | None:
    """通用 ENTSOE GET，返回解析后的 XML 根节点"""
    params["securityToken"] = ENTSOE_TOKEN
    try:
        resp = requests.get(API_URL, params=params, timeout=60)
        resp.raise_for_status()
        # 检查是否为错误响应
        if b"<Acknowledgement_MarketDocument" in resp.content:
            # ENTSOE 返回的错误文档
            root = ET.fromstring(resp.content)
            reason = root.findtext(".//{*}Reason/{*}text", default="unknown error")
            print(f"    [API ERROR] {label}: {reason}")
            return None
        return ET.fromstring(resp.content)
    except requests.RequestException as e:
        print(f"    [ERROR] {label}: {e}")
        return None
    except ET.ParseError as e:
        print(f"    [XML ERROR] {label}: {e}")
        return None


def _to_utc_str(date_str: str, hour_offset: int = 0) -> str:

    dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(hours=hour_offset)
    return dt.strftime(ENTSOE_FMT)


def fetch_price_xml(bzn_eic: str, start: str, end: str) -> ET.Element | None:
    """A44 - Day-ahead prices"""
    return _entsoe_get({
        "documentType":  "A44",
        "out_Domain":    bzn_eic,
        "in_Domain":     bzn_eic,
        "periodStart":   _to_utc_str(start),
        "periodEnd":     _to_utc_str(end),
    }, f"price eic={bzn_eic}")


def fetch_load_xml(bzn_eic: str, start: str, end: str) -> ET.Element | None:
    """A65 - Total load (actual)"""
    return _entsoe_get({
        "documentType":             "A65",
        "processType":              "A16",   # Realised
        "outBiddingZone_Domain":    bzn_eic,
        "periodStart":              _to_utc_str(start),
        "periodEnd":                _to_utc_str(end),
    }, f"load eic={bzn_eic}")


def fetch_generation_xml(ca_eic: str, start: str, end: str) -> ET.Element | None:
    """A75 - Actual generation per production type"""
    return _entsoe_get({
        "documentType":  "A75",
        "processType":   "A16",   # Realised
        "in_Domain":     ca_eic,
        "periodStart":   _to_utc_str(start),
        "periodEnd":     _to_utc_str(end),
    }, f"generation eic={ca_eic}")


# ─────────────────────────────────────────────────────────────
# XML 
# ─────────────────────────────────────────────────────────────

def _parse_period(period_el: ET.Element, tz: str) -> pd.Series:
    """
    解析单个 <Period> 元素 → pd.Series（本地时间 index，float values）
    支持 PT15M / PT30M / PT60H 分辨率，统一 resample 到小时均值
    """
    start_str = period_el.findtext("{*}timeInterval/{*}start") or \
                period_el.findtext(".//{urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3}timeInterval/{urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3}start")

    # 通配符写法更健壮
    start_el = period_el.find(".//{*}start")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")

    if start_el is None or res_el is None:
        return pd.Series(dtype=float)

    start_utc = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    resolution = res_el.text.strip()

    # 分辨率 → timedelta
    if resolution == "PT60M":
        delta = timedelta(hours=1)
    elif resolution == "PT30M":
        delta = timedelta(minutes=30)
    elif resolution == "PT15M":
        delta = timedelta(minutes=15)
    else:
        delta = timedelta(hours=1)

    records = {}
    for pt in points:
        pos_el = pt.find("{*}position")
        # 尝试两种命名空间写法
        if pos_el is None:
            pos_el = pt.find(".//{urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3}position")
        val_el = pt.find("{*}price.amount") or pt.find("{*}quantity")
        if val_el is None:
            # 对于price用price.amount，对于其他用quantity——统一搜索
            for tag in ["{*}price.amount", "{*}quantity"]:
                val_el = pt.find(tag)
                if val_el is not None:
                    break

        if pos_el is None or val_el is None:
            continue

        pos = int(pos_el.text)
        try:
            val = float(val_el.text)
        except (TypeError, ValueError):
            val = float("nan")

        dt_utc = start_utc + (pos - 1) * delta
        records[dt_utc] = val

    if not records:
        return pd.Series(dtype=float)

    s = pd.Series(records)
    s.index = pd.DatetimeIndex(s.index, tz="UTC").tz_convert(tz).tz_localize(None)
    return s.resample("h").mean()


def parse_price(root: ET.Element | None, tz: str) -> pd.Series | None:
    if root is None:
        return None
    periods = root.findall(".//{*}Period")
    if not periods:
        return None
    parts = [_parse_period(p, tz) for p in periods]
    parts = [s for s in parts if not s.empty]
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.resample("h").mean()


def parse_load(root: ET.Element | None, tz: str) -> pd.Series | None:
    """A65 load 解析"""
    if root is None:
        return None
    # A65 的 quantity 字段
    periods = root.findall(".//{*}Period")
    if not periods:
        return None
    parts = []
    for period in periods:
        # 只取 quantity（不是 price.amount）
        s = _parse_period_quantity(period, tz)
        if not s.empty:
            parts.append(s)
    if not parts:
        return None
    combined = pd.concat(parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.resample("h").mean()


def _parse_period_quantity(period_el: ET.Element, tz: str) -> pd.Series:

    start_el = period_el.find(".//{*}start")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")

    if start_el is None or res_el is None:
        return pd.Series(dtype=float)

    start_utc = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    resolution = res_el.text.strip()

    if resolution == "PT60M":
        delta = timedelta(hours=1)
    elif resolution == "PT30M":
        delta = timedelta(minutes=30)
    elif resolution == "PT15M":
        delta = timedelta(minutes=15)
    else:
        delta = timedelta(hours=1)

    records = {}
    for pt in points:
        pos_el = pt.find(".//{*}position")
        qty_el = pt.find(".//{*}quantity")
        if pos_el is None or qty_el is None:
            continue
        pos = int(pos_el.text)
        try:
            val = float(qty_el.text)
        except (TypeError, ValueError):
            val = float("nan")
        dt_utc = start_utc + (pos - 1) * delta
        records[dt_utc] = val

    if not records:
        return pd.Series(dtype=float)

    s = pd.Series(records)
    s.index = pd.DatetimeIndex(s.index, tz="UTC").tz_convert(tz).tz_localize(None)
    return s.resample("h").mean()


def parse_generation(root: ET.Element | None, tz: str) -> dict:
    """
    A75 generation per type 解析
    返回与原脚本一致的结构：
    {
      "load":       None,   # A75 不含 load
      "solar":      pd.Series | None,
      "wind":       pd.Series | None,
      "residual":   None,   # A75 不含 residual，单独计算
      "generation": pd.DataFrame,
    }
    """
    if root is None:
        return {}

    # 按 psrType 聚合
    type_series: dict[str, list[pd.Series]] = {}

    for ts in root.findall(".//{*}TimeSeries"):
        psr_el = ts.find(".//{*}MktPSRType/{*}psrType")
        if psr_el is None:
            continue
        psr_code = psr_el.text.strip()
        psr_name = PSR_TYPE_MAP.get(psr_code, psr_code)

        for period in ts.findall(".//{*}Period"):
            s = _parse_period_quantity(period, tz)
            if not s.empty:
                type_series.setdefault(psr_name, []).append(s)

    if not type_series:
        return {}

    gen_dict: dict[str, pd.Series] = {}
    for name, series_list in type_series.items():
        combined = pd.concat(series_list).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        gen_dict[name] = combined.resample("h").mean()

    gen_df = pd.DataFrame(gen_dict) if gen_dict else pd.DataFrame()

    # 提取 solar / wind
    solar_s = gen_dict.get("Solar")

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

    # 合并 Wind Offshore + Wind Onshore → "Wind"（与原脚本一致）
    if not gen_df.empty:
        cols_to_drop = [c for c in ["Wind Offshore", "Wind Onshore"] if c in gen_df.columns]
        if cols_to_drop:
            gen_df = gen_df.drop(columns=cols_to_drop)
            if wind_s is not None:
                gen_df["Wind"] = wind_s

    return {
        "load":       None,
        "solar":      solar_s,
        "wind":       wind_s,
        "residual":   None,     # 由 load - (solar+wind+...) 可在外部计算，此处留 None
        "generation": gen_df,
    }


# ─────────────────────────────────────────────────────────────
# Write to CSV
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
        print(f"  ✓ {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 国")
    else:
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df.sort_index(inplace=True)
        new_df.to_csv(path)
        print(f"  ✓ {path.name}: 新建 {len(new_df)} 行 × {len(new_df.columns)} 国")


def merge_and_save_generation(new_rows: list[dict], gen_dir: Path):
    if not new_rows:
        print("  [SKIP] generation: 无新数据")
        return

    gen_dir.mkdir(exist_ok=True)
    df_new = pd.DataFrame(new_rows, columns=["date", "country", "category", "value"])
    df_new["value"] = pd.to_numeric(df_new["value"], errors="coerce")

    for country, grp_new in df_new.groupby("country"):
        path = gen_dir / f"{country}.csv"
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
# Main flow
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
    end_date  = yesterday.strftime("%Y-%m-%d")   # ← 截止到 today-1，避免当天数据不完整

    if args.mode == "full":
        start_date = FULL_START_DATE
        mode_label = "全量模式"
    else:
        start_date = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        mode_label = f"增量模式（最近 {LOOKBACK_DAYS} 天）"

    # ENTSOE end 需要多加一天（区间为左闭右开）
    end_date_api = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

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
        ca_eic  = cfg["ca_eic"]
        col     = cc.upper()

        print(f"[{col}]")

        # ── 价格 (A44) ──────────────────────────────────────
        print(f"  → A44 price  eic={bzn_eic}")
        price_root = fetch_price_xml(bzn_eic, start_date, end_date_api)
        time.sleep(REQUEST_DELAY)

        s = parse_price(price_root, tz)
        if s is not None:
            # 截断到 end_date 23:59
            cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            s = s[s.index < cutoff]
            price_cols[col] = s
            print(f"     {s.notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无价格数据")

        # ── load (A65) ──────────────────────────────────────
        print(f"  → A65 load   eic={bzn_eic}")
        load_root = fetch_load_xml(bzn_eic, start_date, end_date_api)
        time.sleep(REQUEST_DELAY)

        s = parse_load(load_root, tz)
        if s is not None:
            cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            s = s[s.index < cutoff]
            load_cols[col] = s
            print(f"     {s.notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无负荷数据")

        # ── 发电结构 (A75) ──────────────────────────────────
        print(f"  → A75 gen    eic={ca_eic}")
        gen_root = fetch_generation_xml(ca_eic, start_date, end_date_api)
        time.sleep(REQUEST_DELAY)

        result = parse_generation(gen_root, tz) if gen_root is not None else {}

        # solar / wind 写入宽表
        for field, target_dict, lbl in [
            ("solar",  solar_cols, "solar"),
            ("wind",   wind_cols,  "wind"),
        ]:
            s = result.get(field)
            if s is not None:
                cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                s = s[s.index < cutoff]
                target_dict[col] = s
                print(f"     {lbl:<12} {s.notna().sum()} 有效值")
            else:
                print(f"     {lbl:<12} [WARN] 无数据")

        # residual load = load - 可再生发电（若 load 和 gen 均可用则计算）
        load_s  = load_cols.get(col)
        solar_s = solar_cols.get(col)
        wind_s  = wind_cols.get(col)
        if load_s is not None and (solar_s is not None or wind_s is not None):
            renewables = pd.Series(0.0, index=load_s.index)
            for rs in [solar_s, wind_s]:
                if rs is not None:
                    renewables = renewables.add(rs.reindex(load_s.index, fill_value=0), fill_value=0)
            residual_s = load_s - renewables
            residual_cols[col] = residual_s
            print(f"     residual_load {residual_s.notna().sum()} 有效值（计算值）")

        # generation 明细 → 长表
        gen_df: pd.DataFrame = result.get("generation", pd.DataFrame())
        if not gen_df.empty:
            cutoff = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            gen_df = gen_df[gen_df.index < cutoff]
            for cat in gen_df.columns:
                for dt, val in gen_df[cat].items():
                    gen_rows.append({
                        "date":     dt.strftime("%Y/%-m/%-d %-H:00"),
                        "country":  col,
                        "category": cat,
                        "value":    val,
                    })
            print(f"     generation   {len(gen_df.columns)} 类型, {len(gen_df)} 行")

        print()

    # ── 写文件（合并模式）────────────────────────────────────
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
