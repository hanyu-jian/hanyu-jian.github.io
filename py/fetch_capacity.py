#!/usr/bin/env python3
"""
ENTSOE API 数据更新脚本 — Installed Capacity per Production Type（A68）
- 文档类型：A68（14.1.A Installed Capacity per Production Type）
- processType：A33（Year Ahead）
- 每次请求覆盖一整年（ENTSOE 限制单次请求跨度 ≤ 1 年，正好一年一条）
- 输出与人工从 ENTSO-E 网站下载的 data/capacity.csv 格式完全一致：
    Year,Month,Country,Production Type,Installed Capacity (MW),Total Grand Capacity (MW)
  - Month 列固定为空（年度数据，无月度粒度）
  - API 未返回的发电类型标记为 "n/e"（与原始人工下载数据一致）
  - Total Grand Capacity (MW) 只出现在该国该年最后一行（Production Type 为空），
    值为该国当年所有已报送（非 n/e）类型之和
  - 每个国家/年份内按 Production Type 字母序排列，与原始文件一致

更新频率：
- Year-Ahead 容量预测数据变动很少，建议每月跑一次（见 workflow 中的月度 cron）
- 默认只刷新「当前年 → 当前年+1」两年数据，不会动已有的历史年份
- 也可用 --start-year / --end-year / --country 手动指定范围，独立运行：
    python py/fetch_capacity.py --country DE --start-year 2023 --end-year 2026

合并策略：
- 只替换本次实际抓到数据的 (Year, Country) 组合对应的行
- 某个 (Year, Country) 抓取为空（尚未发布/接口异常）时跳过，不覆盖已有记录
- 其余年份/国家的历史数据原样保留，不影响 capacity.html 的展示
"""

import argparse
import csv
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────
# 配置（与主脚本 fetch_entsoe.py 保持一致）
# ─────────────────────────────────────────────────────────────

ENTSOE_TOKEN      = os.environ["ENTSOE_TOKEN"]
API_URL           = "https://web-api.tp.entsoe.eu/api"
ENTSOE_DISPLAY_TZ = "Europe/Brussels"

# in_Domain EIC：安装容量按控制区（Control Area）统计，优先用 gen_eic，否则退回 bzn_eic
COUNTRY_CONFIG = {
    "de": {"bzn_eic": "10Y1001A1001A82H", "gen_eic": "10Y1001A1001A83F"},
    "fr": {"bzn_eic": "10YFR-RTE------C"},
    "es": {"bzn_eic": "10YES-REE------0"},
    "it": {"bzn_eic": "10Y1001A1001A73I", "gen_eic": "10YIT-GRTN-----B"},
    "gr": {"bzn_eic": "10YGR-HTSO-----Y"},
    "ro": {"bzn_eic": "10YRO-TEL------P"},
    "hu": {"bzn_eic": "10YHU-MAVIR----U"},
    "at": {"bzn_eic": "10YAT-APG------L"},
    "pl": {"bzn_eic": "10YPL-AREA-----S"},
    "sk": {"bzn_eic": "10YSK-SEPS-----K"},
    "rs": {"bzn_eic": "10YCS-SERBIATSOV"},
    "hr": {"bzn_eic": "10YHR-HEP------M"},
    "bg": {"bzn_eic": "10YCA-BULGARIA-R"},
}

# Production Type 文案与 data/capacity.csv 现有内容逐字对齐（大小写/拼写），
# 保证新旧年份在 capacity.html 里按同一字符串分组、legend 不分裂。
PSR_TYPE_MAP = {
    "B01": "Biomass",
    "B02": "Fossil Brown coal/Lignite",
    "B03": "Fossil Coal-derived gas",
    "B04": "Fossil Gas",
    "B05": "Fossil Hard coal",
    "B06": "Fossil Oil",
    "B07": "Fossil Oil shale",
    "B08": "Fossil Peat",
    "B09": "Geothermal",
    "B10": "Hydro Pumped Storage",
    "B11": "Hydro Run-of-river and pondage",
    "B12": "Hydro Water Reservoir",
    "B13": "Marine",
    "B14": "Nuclear",
    "B15": "Other renewable",
    "B16": "Solar",
    "B17": "Waste",
    "B18": "Wind Offshore",
    "B19": "Wind Onshore",
    "B20": "Other",
    "B25": "Energy storage",
}
PRODUCTION_TYPES_SORTED = sorted(PSR_TYPE_MAP.values(), key=str.lower)

COUNTRIES       = list(COUNTRY_CONFIG.keys())
REQUEST_DELAY   = 1.5
PAGE_SIZE       = 100
DEFAULT_TIMEOUT = 90
DATA_DIR        = Path("data")
ENTSOE_FMT      = "%Y%m%d%H%M"
CSV_FIELDS      = ["Year", "Month", "Country", "Production Type",
                   "Installed Capacity (MW)", "Total Grand Capacity (MW)"]


def _date_to_entsoe_utc_dt(date_str: str) -> str:
    local_midnight = pd.Timestamp(date_str).tz_localize(ENTSOE_DISPLAY_TZ)
    utc_dt = local_midnight.tz_convert("UTC")
    return utc_dt.strftime(ENTSOE_FMT)


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


def _capacity_params(in_domain: str, year: int) -> dict:
    return {
        "documentType": "A68",
        "processType":  "A33",
        "in_Domain":    in_domain,
        "periodStart":  _date_to_entsoe_utc_dt(f"{year}-01-01"),
        "periodEnd":    _date_to_entsoe_utc_dt(f"{year + 1}-01-01"),
    }


def _parse_capacity_timeseries(ts_list: list[ET.Element]) -> dict[str, float]:
    """每个 TimeSeries 对应一个 PsrType，Period 内通常只有 1 个 Point（整年一个值）。"""
    capacity_by_label: dict[str, float] = {}

    for ts in ts_list:
        psr_el = ts.find(".//{*}MktPSRType/{*}psrType")
        if psr_el is None or not psr_el.text:
            continue
        psr_code = psr_el.text.strip()
        label    = PSR_TYPE_MAP.get(psr_code)
        if label is None:
            continue

        for point in ts.findall(".//{*}Point"):
            qty_el = point.find("./{*}quantity")
            if qty_el is None or not qty_el.text:
                continue
            try:
                val = float(qty_el.text.strip())
            except ValueError:
                continue
            capacity_by_label[label] = capacity_by_label.get(label, 0.0) + val
            break  # 只取每个 TimeSeries/Period 的第一个 Point

    return capacity_by_label


def _fmt_num(x: float) -> str:
    x = round(x, 2)
    if x == int(x):
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def build_rows_for_country_year(year: int, country_code: str,
                                capacity_by_label: dict[str, float]) -> list[dict]:
    rows  = []
    total = 0.0

    for label in PRODUCTION_TYPES_SORTED:
        val = capacity_by_label.get(label)
        rows.append({
            "Year": str(year), "Month": "", "Country": country_code,
            "Production Type": label,
            "Installed Capacity (MW)": _fmt_num(val) if val is not None else "n/e",
            "Total Grand Capacity (MW)": "",
        })
        if val is not None:
            total += val

    rows.append({
        "Year": str(year), "Month": "", "Country": country_code,
        "Production Type": "", "Installed Capacity (MW)": "",
        "Total Grand Capacity (MW)": _fmt_num(total) if total > 0 else "",
    })
    return rows


def merge_and_save_capacity(new_rows_by_key: dict[tuple[str, str], list[dict]], path: Path):
    if not new_rows_by_key:
        print(f"  [SKIP] {path.name}: 无新数据")
        return

    existing_rows: list[dict] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))

    keys_to_replace = set(new_rows_by_key.keys())
    kept_rows = [
        r for r in existing_rows
        if (r.get("Year", ""), r.get("Country", "")) not in keys_to_replace
    ]

    new_rows = []
    for key in sorted(keys_to_replace, key=lambda k: (int(k[0]), k[1])):
        new_rows.extend(new_rows_by_key[key])

    all_rows = kept_rows + new_rows

    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  ✓ {path.name}: 共 {len(all_rows)} 行"
          f"（更新 {len(keys_to_replace)} 个 年份/国家 组合，"
          f"新增/替换 {len(new_rows)} 行，保留 {len(kept_rows)} 行）")


def main():
    parser = argparse.ArgumentParser(description="ENTSOE Installed Capacity (A68) 更新")
    parser.add_argument("--country", default="", help="只跑指定国家，例如 DE，为空则跑全部")
    parser.add_argument("--start-year", type=int, default=None,
                        help="起始年份（含），默认当前年")
    parser.add_argument("--end-year", type=int, default=None,
                        help="结束年份（含），默认当前年+1（Year-Ahead 预测通常已覆盖下一年）")
    args = parser.parse_args()

    current_year = datetime.now().year
    start_year   = args.start_year if args.start_year is not None else current_year
    end_year     = args.end_year   if args.end_year   is not None else current_year + 1
    years        = list(range(start_year, end_year + 1))

    if args.country:
        cc_filter = args.country.lower()
        if cc_filter not in COUNTRY_CONFIG:
            print(f"[ERROR] 未知国家: {args.country}")
            return
        countries_to_run = [cc_filter]
    else:
        countries_to_run = COUNTRIES

    print("=" * 62)
    print("ENTSOE Installed Capacity 更新  [A68 / Year Ahead]")
    print("=" * 62)
    print(f"年份: {years}")
    print(f"国家: {[c.upper() for c in countries_to_run]}\n")

    DATA_DIR.mkdir(exist_ok=True)

    new_rows_by_key: dict[tuple[str, str], list[dict]] = {}

    for year in years:
        for cc in countries_to_run:
            cfg       = COUNTRY_CONFIG[cc]
            in_domain = cfg.get("gen_eic", cfg["bzn_eic"])
            col       = cc.upper()
            print(f"[{year}] {col}  in_Domain={in_domain}")

            ts_list = _get_all_timeseries(
                _capacity_params(in_domain, year), f"capacity {col} {year}")
            time.sleep(REQUEST_DELAY)

            capacity_by_label = _parse_capacity_timeseries(ts_list)
            if not capacity_by_label:
                print(f"     [WARN] 无数据（可能尚未发布），跳过，不覆盖已有记录")
                continue

            new_rows_by_key[(str(year), col)] = build_rows_for_country_year(
                year, col, capacity_by_label)
            print(f"     ✓ {len(capacity_by_label)}/{len(PSR_TYPE_MAP)} 类型已报送")

    print("\n保存/合并文件...")
    merge_and_save_capacity(new_rows_by_key, DATA_DIR / "capacity.csv")

    print("=" * 62)
    print(f"完成！{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
