#!/usr/bin/env python3
"""
ENTSOE Intraday Price 数据更新脚本
- documentType=A44, contract_MarketAgreement.type=A07
- 时间分辨率：resample 到小时均值
- 输出：data/intraday.csv（宽表，列为国家，index 为本地时间）
- 格式与 price.csv 完全一致
- 数据截止到 today-1（避免当天数据不完整）
- 支持两种模式：
    --mode incremental  仅更新最近7天（默认）
    --mode full         全量拉取 FULL_START_DATE → yesterday
"""

import argparse
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

COUNTRIES       = list(COUNTRY_CONFIG.keys())
FULL_START_DATE = "2024-01-01"
LOOKBACK_DAYS   = 7
REQUEST_DELAY   = 1.0
DATA_DIR        = Path("data")
ENTSOE_FMT      = "%Y%m%d%H%M"


# ─────────────────────────────────────────────────────────────
# API 请求
# ─────────────────────────────────────────────────────────────

def _to_utc_str(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime(ENTSOE_FMT)


def fetch_intraday_xml(bzn_eic: str, start: str, end: str) -> ET.Element | None:
    """A44 + A07 = Intraday price"""
    params = {
        "securityToken":                  ENTSOE_TOKEN,
        "documentType":                   "A44",
        "contract_MarketAgreement.type":  "A07",
        "out_Domain":                     bzn_eic,
        "in_Domain":                      bzn_eic,
        "periodStart":                    _to_utc_str(start),
        "periodEnd":                      _to_utc_str(end),
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=60)
        resp.raise_for_status()
        if b"<Acknowledgement_MarketDocument" in resp.content:
            root   = ET.fromstring(resp.content)
            reason = root.findtext(".//{*}Reason/{*}text", default="unknown error")
            print(f"    [API ERROR] eic={bzn_eic}: {reason}")
            return None
        return ET.fromstring(resp.content)
    except requests.RequestException as e:
        print(f"    [ERROR] eic={bzn_eic}: {e}")
        return None
    except ET.ParseError as e:
        print(f"    [XML ERROR] eic={bzn_eic}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# XML 解析
# ─────────────────────────────────────────────────────────────

def _parse_period(period_el: ET.Element, tz: str) -> pd.Series:
    """
    解析单个 <Period>，返回本地时间 index 的 pd.Series。
    intraday 可能含 PT15M / PT30M / PT60M 分辨率，统一 resample 到小时均值。
    price 字段优先取 price.amount，回退到 quantity。
    """
    start_el = period_el.find(".//{*}start")
    res_el   = period_el.find(".//{*}resolution")
    points   = period_el.findall(".//{*}Point")

    if start_el is None or res_el is None or not points:
        return pd.Series(dtype=float)

    start_utc  = datetime.strptime(start_el.text.strip(), "%Y-%m-%dT%H:%MZ")
    resolution = res_el.text.strip()

    delta_map = {"PT15M": timedelta(minutes=15),
                 "PT30M": timedelta(minutes=30),
                 "PT60M": timedelta(hours=1)}
    delta = delta_map.get(resolution, timedelta(hours=1))

    records = {}
    for pt in points:
        pos_el = pt.find(".//{*}position")
        # price.amount 优先（A44 标准字段）
        val_el = pt.find(".//{*}price.amount")
        if val_el is None:
            val_el = pt.find(".//{*}quantity")
        if pos_el is None or val_el is None:
            continue
        pos = int(pos_el.text)
        try:
            val = float(val_el.text)
        except (TypeError, ValueError):
            val = float("nan")
        records[start_utc + (pos - 1) * delta] = val

    if not records:
        return pd.Series(dtype=float)

    s = pd.Series(records)
    s.index = pd.DatetimeIndex(s.index, tz="UTC").tz_convert(tz).tz_localize(None)
    return s.resample("h").mean()


def parse_intraday(root: ET.Element | None, tz: str) -> pd.Series | None:
    """
    合并 XML 内所有 TimeSeries/Period。
    intraday 一个响应里可能有多条 TimeSeries（不同合约时段），
    取各小时的均值作为代表价格。
    """
    if root is None:
        return None

    periods = root.findall(".//{*}Period")
    if not periods:
        return None

    parts = [_parse_period(p, tz) for p in periods]
    parts = [s for s in parts if not s.empty]
    if not parts:
        return None

    # 同一小时可能来自多条 TimeSeries（不同合约），取均值
    combined = pd.concat(parts).groupby(level=0).mean()
    return combined.sort_index().resample("h").mean()


# ─────────────────────────────────────────────────────────────
# CSV 合并写入（与主脚本逻辑完全一致）
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


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ENTSOE Intraday Price 更新")
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

    # ENTSOE periodEnd 多传一天（左闭右开）
    end_date_api = (
        datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print("=" * 62)
    print(f"ENTSOE Intraday Price 更新  [{mode_label}]")
    print("=" * 62)
    print(f"数据范围: {start_date} → {end_date}（含）")
    print(f"国家数量: {len(COUNTRIES)}")
    print(f"文档类型: A44 / contract_MarketAgreement.type=A07")
    print()

    DATA_DIR.mkdir(exist_ok=True)

    cutoff     = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    intra_cols: dict[str, pd.Series] = {}

    for cc in COUNTRIES:
        cfg     = COUNTRY_CONFIG[cc]
        tz      = cfg["tz"]
        bzn_eic = cfg["bzn_eic"]
        col     = cc.upper()

        print(f"[{col}]  eic={bzn_eic}")
        root = fetch_intraday_xml(bzn_eic, start_date, end_date_api)
        time.sleep(REQUEST_DELAY)

        s = parse_intraday(root, tz)
        if s is not None:
            s = s[s.index < cutoff]
            intra_cols[col] = s
            print(f"  ✓ {s.notna().sum()} 有效值  ({s.index.min()} → {s.index.max()})")
        else:
            print(f"  [WARN] 无 intraday 数据（该市场可能不支持）")

        print()

    print("保存/合并文件...")
    merge_and_save_wide(intra_cols, DATA_DIR / "intraday.csv", "intraday")

    print()
    print("=" * 62)
    print(f"完成！{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
