#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本 — Imbalance Price（A85）
- 文档类型：A85（Imbalance prices）
- 只抓取 <imbalance_Price.amount>（category=A04，即 Generation 侧失衡价）
- 原始 15min 数据 → raw_data/{YEAR}/A85.csv（按年拆分，格式与 A44/A65 完全一致）
- 小时均值   → data/imbalance_price.csv（格式与 price.csv 完全一致）
- 响应可能是 ZIP 包，自动解压；只处理文件名含 "IMBALANCE_PRICES" 的条目
- 支持模式：
    --mode incremental  最近 7 天（默认）
    --mode full         FULL_START_DATE → yesterday
- 支持 --country / --start / --end 参数（与主脚本一致）
"""

import argparse
import io
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────
# 配置（与主脚本保持一致）
# ─────────────────────────────────────────────────────────────

ENTSOE_TOKEN      = os.environ["ENTSOE_TOKEN"]
API_URL           = "https://web-api.tp.entsoe.eu/api"
ENTSOE_DISPLAY_TZ = "Europe/Brussels"

# controlArea_Domain EIC（Scheduling Area / Control Area）
# 与 COUNTRY_CONFIG 中的 bzn_eic 相同；部分国家的 CA EIC 不同，按实际填写
IMBALANCE_COUNTRY_CONFIG = {
    "de": "10Y1001A1001A83F",   # DE Control Area
    "fr": "10YFR-RTE------C",
    "es": "10YES-REE------0",
    "it": "10YIT-GRTN-----B",
    "gr": "10YGR-HTSO-----Y",
    "ro": "10YRO-TEL------P",
    "hu": "10YHU-MAVIR----U",
    "at": "10YAT-APG------L",
    "pl": "10YPL-AREA-----S",
    "sk": "10YSK-SEPS-----K",
    "rs": "10YCS-SERBIATSOV",
    "hr": "10YHR-HEP------M",
    "bg": "10YCA-BULGARIA-R",
}

COUNTRIES             = list(IMBALANCE_COUNTRY_CONFIG.keys())
FULL_START_DATE       = "2024-01-01"
LOOKBACK_DAYS         = 7
REQUEST_DELAY         = 1.5
CHUNK_DAYS            = 365          # A85 每请求最多 1 年
PAGE_SIZE             = 100
DEFAULT_TIMEOUT       = 90
DATA_DIR              = Path("data")
RAW_DIR               = Path("raw_data")
ENTSOE_FMT            = "%Y%m%d%H%M"
HOURLY_ROUND_DECIMALS = 6

# ─────────────────────────────────────────────────────────────
# 通用工具（与主脚本相同逻辑，独立复制避免循环依赖）
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


def _date_to_entsoe_utc_dt(date_str: str) -> str:
    """CET/CEST 00:00 → UTC 字符串（ENTSOE API 格式）"""
    local_midnight = pd.Timestamp(date_str).tz_localize(ENTSOE_DISPLAY_TZ)
    utc_dt = local_midnight.tz_convert("UTC")
    return utc_dt.strftime(ENTSOE_FMT)


DELTA_MAP = {
    "PT15M": timedelta(minutes=15),
    "PT30M": timedelta(minutes=30),
    "PT60M": timedelta(hours=1),
}
RES_MINUTES = {"PT15M": 15, "PT30M": 30, "PT60M": 60}


def _clean_numeric_text(x):
    if pd.isna(x):
        return x
    if isinstance(x, str):
        y = x.strip().lstrip("'").strip()
        y = re.sub(r"[^0-9eE+\-.]", "", y)
        return y if y != "" else None
    return x


def _coerce_df_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(_clean_numeric_text)
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _fmt_dt_hourly(dt: pd.Timestamp) -> str:
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:00"


def _fmt_dt_15min(dt: pd.Timestamp) -> str:
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"


def fmt_index(index: pd.DatetimeIndex) -> list[str]:
    return [_fmt_dt_hourly(dt) for dt in index]


def fmt_index_15min(index: pd.DatetimeIndex) -> list[str]:
    return [_fmt_dt_15min(dt) for dt in index]


def _sort_index_by_time(df: pd.DataFrame) -> pd.DataFrame:
    dt_idx = pd.to_datetime(df.index, errors="coerce")
    df = df[dt_idx.notna()].copy()
    df.index = dt_idx[dt_idx.notna()]
    df.sort_index(inplace=True)
    return df


def normalize_raw_series_to_15min(s: pd.Series) -> pd.Series:
    """将原始序列统一展开为 15min 间隔（1h 数据四等分，15min 数据保持原样）"""
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
# A85 专用：XML 解析
# ─────────────────────────────────────────────────────────────

def _parse_imbalance_period(period_el: ET.Element) -> pd.Series:
    """
    解析 A85 Period，提取 <imbalance_Price.amount>。
    每个 Point 包含：
        <imbalance_Price.amount>  ← 我们要的值
        <imbalance_Price.category> A04=Generation / A05=Load
    只取 A04（Generation）；若某点无 category 标签则也纳入。
    返回：UTC → CET/CEST 本地时间的 pd.Series（naive）
    """
    start_el = period_el.find(".//{*}start")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")
    end_el   = period_el.find(".//{*}end")

    if start_el is None or res_el is None:
        return pd.Series(dtype=float)

    start_utc = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    res_str   = res_el.text.strip()
    delta     = DELTA_MAP.get(res_str, timedelta(hours=1))

    if end_el is not None:
        end_utc     = datetime.strptime(end_el.text.strip(), "%Y-%m-%dT%H:%MZ")
        total_slots = int((end_utc - start_utc) / delta)
    else:
        total_slots = max(
            (int(pt.findtext(".//{*}position", "0")) for pt in points),
            default=0,
        )

    if total_slots <= 0:
        return pd.Series(dtype=float)

    point_values: dict[int, float] = {}
    for pt in points:
        pos_el  = pt.find(".//{*}position")
        amt_el  = pt.find(".//{*}imbalance_Price.amount")
        cat_el  = pt.find(".//{*}imbalance_Price.category")

        if pos_el is None or amt_el is None:
            continue

        # 只保留 A04（Generation）；无 category 标签的也保留
        if cat_el is not None and cat_el.text and cat_el.text.strip() != "A04":
            continue

        try:
            pos = int(pos_el.text)
            val = float(amt_el.text.strip())
            point_values[pos] = val
        except (TypeError, ValueError):
            pass

    records: dict = {}
    last_val = None
    for pos in range(1, total_slots + 1):
        ts = start_utc + (pos - 1) * delta
        if pos in point_values:
            last_val = point_values[pos]
        if last_val is not None:
            records[ts] = last_val

    if not records:
        return pd.Series(dtype=float)

    s = pd.Series(records)
    # UTC → CET/CEST naive（与主脚本一致）
    s.index = (
        pd.DatetimeIndex(s.index)
          .tz_localize("UTC")
          .tz_convert(ENTSOE_DISPLAY_TZ)
          .tz_localize(None)
    )
    return s


def _parse_imbalance_xml(xml_bytes: bytes) -> pd.Series | None:
    """解析单个 A85 XML，返回合并后的 Series（15min 或 1h）"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"      [XML ERROR] {e}")
        return None

    if "Acknowledgement_MarketDocument" in root.tag:
        reason = root.findtext(".//{*}Reason/{*}text", default="unknown")
        print(f"      [API ERROR] {reason}")
        return None

    # 只取 A85 文档；A86（volumes）跳过
    doc_type = root.findtext(".//{*}type", default="")
    if doc_type not in ("A85", ""):
        return None

    parts = []
    for ts in root.findall(".//{*}TimeSeries"):
        for period in ts.findall(".//{*}Period"):
            s = _parse_imbalance_period(period)
            if not s.empty:
                parts.append(s)

    if not parts:
        return None

    combined = pd.concat(parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined


def _fetch_raw_bytes(params: dict, label: str,
                     timeout: int = DEFAULT_TIMEOUT) -> list[bytes]:
    """
    请求 ENTSOE API，返回所有 XML bytes（自动处理 ZIP 包）。
    A85 不支持 offset 翻页（每次响应通常为单 ZIP），直接返回。
    """
    full_params = {**params, "securityToken": ENTSOE_TOKEN}

    for attempt in range(5):
        try:
            resp = requests.get(API_URL, params=full_params, timeout=timeout)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt < 4:
                sleep = 2 ** attempt
                print(f"      [RETRY] {label} in {sleep}s: {e}")
                time.sleep(sleep)
            else:
                print(f"      [ERROR] {label}: {e}")
                return []

    content_type = resp.headers.get("Content-Type", "")

    # ZIP 响应：解压，只取 IMBALANCE_PRICES 文件
    if "zip" in content_type or resp.content[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_list = []
                for name in zf.namelist():
                    if "IMBALANCE_PRICES" in name.upper() and name.endswith(".xml"):
                        xml_list.append(zf.read(name))
                        print(f"      → ZIP entry: {name}")
                if not xml_list:
                    # 没有 IMBALANCE_PRICES 条目时返回所有 xml（兜底）
                    xml_list = [
                        zf.read(n) for n in zf.namelist() if n.endswith(".xml")
                    ]
                return xml_list
        except zipfile.BadZipFile as e:
            print(f"      [ZIP ERROR] {label}: {e}")
            return []

    # 普通 XML 响应
    return [resp.content]


# ─────────────────────────────────────────────────────────────
# 核心抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_imbalance_price(
    ca_eic: str, start: str, end_inclusive: str
) -> tuple[pd.Series | None, pd.Series | None]:
    """
    抓取单个控制区的 A85 Imbalance Price（A04）。
    返回：(hourly_series, raw_15min_series)
    """
    raw_parts = []
    chunks = date_chunks(start, end_inclusive, chunk_days=CHUNK_DAYS)

    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"     chunk {i}/{len(chunks)}: {cs} → {ce}")
        params = {
            "documentType":      "A85",
            "controlArea_Domain": ca_eic,
            "periodStart":        _date_to_entsoe_utc_dt(cs),
            "periodEnd":          _date_to_entsoe_utc_dt(ce),
        }
        xml_bytes_list = _fetch_raw_bytes(params, f"imbalance {ca_eic}")
        time.sleep(REQUEST_DELAY)

        for xml_bytes in xml_bytes_list:
            s = _parse_imbalance_xml(xml_bytes)
            if s is not None:
                raw_parts.append(s)

    if not raw_parts:
        return None, None

    raw_combined = pd.concat(raw_parts).sort_index()
    raw_combined = raw_combined[~raw_combined.index.duplicated(keep="last")]
    hourly = raw_combined.resample("h").mean()
    return hourly, raw_combined


# ─────────────────────────────────────────────────────────────
# 保存 / 合并（与主脚本一致）
# ─────────────────────────────────────────────────────────────

def merge_and_save_wide(new_cols: dict[str, pd.Series], path: Path, label: str):
    if not new_cols:
        print(f"  [SKIP] {label}: 无新数据")
        return

    new_df = pd.DataFrame(new_cols)
    new_df = _coerce_df_numeric(new_df)
    new_df = new_df.round(HOURLY_ROUND_DECIMALS)

    if path.exists():
        old_df = pd.read_csv(path, index_col=0)
        old_df = _coerce_df_numeric(old_df)
        new_df.index = fmt_index(new_df.index)
        all_cols = old_df.columns.union(new_df.columns)
        old_df   = old_df.reindex(columns=all_cols)
        new_df   = new_df.reindex(columns=all_cols)
        old_df.update(new_df)
        merged = old_df.combine_first(new_df)
        merged = _coerce_df_numeric(merged)
        merged = merged.round(HOURLY_ROUND_DECIMALS)
        merged.index.name = "Date"
        merged = _sort_index_by_time(merged)
        merged.index = fmt_index(merged.index)
        merged.index.name = "Date"
        merged.to_csv(path)
        print(f"  ✓ {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 列")
    else:
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df = _sort_index_by_time(new_df)
        new_df = _coerce_df_numeric(new_df)
        new_df = new_df.round(HOURLY_ROUND_DECIMALS)
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df.to_csv(path)
        print(f"  ✓ {path.name}: 新建 {len(new_df)} 行 × {len(new_df.columns)} 列")


def _year_path(base_path: Path, year: int) -> Path:
    """把逻辑路径 raw_data/A85.csv 映射到按年拆分后的实际路径 raw_data/{year}/A85.csv。"""
    return base_path.parent / str(int(year)) / base_path.name


def merge_and_save_raw_wide(new_raw_cols: dict[str, pd.Series],
                            path: Path, label: str):
    """
    按年拆分写入宽表原始数据（raw_data/{YEAR}/{path.name}），
    避免单个原始 CSV 随时间无限增长撞到 GitHub 单文件体积限制。
    """
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
    new_df = _coerce_df_numeric(new_df)
    new_df = new_df.round(HOURLY_ROUND_DECIMALS)
    new_df.index = pd.DatetimeIndex(all_idx)

    for year, year_df in new_df.groupby(new_df.index.year):
        year_path = _year_path(path, year)
        year_path.parent.mkdir(parents=True, exist_ok=True)

        year_df = year_df.copy()
        year_df.index = fmt_index_15min(year_df.index)
        year_df.index.name = "Date"

        if year_path.exists():
            old_df = pd.read_csv(year_path, index_col=0)
            old_df = _coerce_df_numeric(old_df)
            all_cols = old_df.columns.union(year_df.columns)
            old_df   = old_df.reindex(columns=all_cols)
            year_df  = year_df.reindex(columns=all_cols)
            old_df.update(year_df)
            merged = old_df.combine_first(year_df)
            merged = _coerce_df_numeric(merged)
            merged = merged.round(HOURLY_ROUND_DECIMALS)
            merged.index.name = "Date"
            merged = _sort_index_by_time(merged)
            merged.index = fmt_index_15min(merged.index)
            merged.index.name = "Date"
            merged.to_csv(year_path)
            print(f"  ✓ RAW {year_path}: 合并后 {len(merged)} 行 × {len(merged.columns)} 列")
        else:
            year_df = _sort_index_by_time(year_df)
            year_df = _coerce_df_numeric(year_df)
            year_df = year_df.round(HOURLY_ROUND_DECIMALS)
            year_df.index = fmt_index_15min(year_df.index)
            year_df.index.name = "Date"
            year_df.to_csv(year_path)
            print(f"  ✓ RAW {year_path}: 新建 {len(year_df)} 行 × {len(year_df.columns)} 列")


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ENTSOE A85 Imbalance Price 更新")
    parser.add_argument(
        "--mode", choices=["incremental", "full"], default="incremental",
    )
    parser.add_argument("--country", default="",
                        help="只跑指定国家，例如 AT，为空则跑全部")
    parser.add_argument("--start", default="", help="自定义起始日期 YYYY-MM-DD")
    parser.add_argument("--end",   default="", help="自定义结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    today     = datetime.now()
    yesterday = today - timedelta(days=1)
    end_date  = args.end if args.end else yesterday.strftime("%Y-%m-%d")

    if args.mode == "full":
        start_date = args.start if args.start else FULL_START_DATE
        mode_label = "全量模式"
    else:
        start_date = args.start if args.start else (
            today - timedelta(days=LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        mode_label = f"增量模式（最近 {LOOKBACK_DAYS} 天）"

    # 截止时间（naive，CET/CEST）
    cutoff_local = pd.Timestamp(end_date).tz_localize(ENTSOE_DISPLAY_TZ) + timedelta(days=1)
    cutoff_naive = cutoff_local.tz_localize(None)

    # 国家过滤
    if args.country:
        cc_filter = args.country.lower()
        if cc_filter not in IMBALANCE_COUNTRY_CONFIG:
            print(f"[ERROR] 未知国家: {args.country}，可用: {list(IMBALANCE_COUNTRY_CONFIG.keys())}")
            return
        countries_to_run = [cc_filter]
    else:
        countries_to_run = COUNTRIES

    print("=" * 62)
    print(f"ENTSOE A85 Imbalance Price 更新  [{mode_label}]")
    print("=" * 62)
    print(f"数据范围: {start_date} → {end_date}（含，CET/CEST）")
    print(f"国家: {[c.upper() for c in countries_to_run]}\n")

    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    hourly_cols: dict[str, pd.Series] = {}
    raw_cols:    dict[str, pd.Series] = {}

    for cc in countries_to_run:
        ca_eic = IMBALANCE_COUNTRY_CONFIG[cc]
        col    = cc.upper()
        print(f"[{col}]")
        print(f"  → A85 imbalance price  controlArea={ca_eic}")

        s_hourly, s_raw = fetch_imbalance_price(ca_eic, start_date, end_date)

        if s_hourly is not None:
            s_hourly = s_hourly[s_hourly.index < cutoff_naive]
            s_raw    = s_raw[s_raw.index < cutoff_naive]
            hourly_cols[col] = s_hourly
            raw_cols[col]    = s_raw
            print(f"     ✓ {s_hourly.notna().sum()} 有效值(1h) | "
                  f"{s_raw.notna().sum()} 原始点")
        else:
            print(f"     [WARN] 无 imbalance price 数据")
        print()

    print("保存/合并文件...")
    merge_and_save_wide(hourly_cols, DATA_DIR / "imbalance_price.csv", "imbalance_price")
    merge_and_save_raw_wide(raw_cols, RAW_DIR / "A85.csv", "A85 imbalance price")

    print()
    print("=" * 62)
    print(f"完成！{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
