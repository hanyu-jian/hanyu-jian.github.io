#!/usr/bin/env python3
"""
Energy Charts API 数据更新脚本
获取13个欧洲国家的电力数据并更新到CSV文件

每国 API 调用（共2次）：
  1. /price?bzn=XX          → price.csv
  2. /public_power?country=xx → load / solar / wind / residual_load / generation

输出文件：
  price.csv         宽表 date × country  (EUR/MWh)
  load.csv          宽表 date × country  (MW)
  solar.csv         宽表 date × country  (MW)
  wind.csv          宽表 date × country  (MW, offshore+onshore合并)
  residual_load.csv 宽表 date × country  (MW)
  generation.csv    长表 date, country, category, value
"""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

COUNTRY_CONFIG = {
    "de": {"tz": "Europe/Berlin",     "bzn": "DE-LU"},
    "fr": {"tz": "Europe/Paris",      "bzn": "FR"},
    "es": {"tz": "Europe/Madrid",     "bzn": "ES"},
    "it": {"tz": "Europe/Rome",       "bzn": "IT-North"},
    "gr": {"tz": "Europe/Athens",     "bzn": "GR"},
    "ro": {"tz": "Europe/Bucharest",  "bzn": "RO"},
    "hu": {"tz": "Europe/Budapest",   "bzn": "HU"},
    "at": {"tz": "Europe/Vienna",     "bzn": "AT"},
    "pl": {"tz": "Europe/Warsaw",     "bzn": "PL"},
    "sk": {"tz": "Europe/Bratislava", "bzn": "SK"},
    "rs": {"tz": "Europe/Belgrade",   "bzn": "RS"},
    "hr": {"tz": "Europe/Zagreb",     "bzn": "HR"},
    "bg": {"tz": "Europe/Sofia",      "bzn": "BG"},
}

COUNTRIES     = list(COUNTRY_CONFIG.keys())
START_DATE    = "2024-01-01"
END_DATE      = "2025-01-01"      # ← 测试用，改为 None 则自动用明天
REQUEST_DELAY = 1.5
API_BASE      = "https://api.energy-charts.info"
DATA_DIR      = Path("data")


# ─────────────────────────────────────────────────────────────
# API 请求
# ─────────────────────────────────────────────────────────────

def _get(url: str, params: dict, label: str) -> dict | None:
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"    [ERROR] {label}: {e}")
        return None


def fetch_price(bzn: str, start: str, end: str) -> dict | None:
    return _get(f"{API_BASE}/price",
                {"bzn": bzn, "start": start, "end": end},
                f"price bzn={bzn}")


def fetch_power(country: str, start: str, end: str) -> dict | None:
    return _get(f"{API_BASE}/public_power",
                {"country": country, "start": start, "end": end},
                f"public_power country={country}")


# ─────────────────────────────────────────────────────────────
# 解析工具
# ─────────────────────────────────────────────────────────────

def _make_index(unix_seconds: list, tz: str) -> pd.DatetimeIndex:
    """UTC unix timestamps → 本地时区 → 去除时区信息"""
    return (pd.to_datetime(unix_seconds, unit="s", utc=True)
              .tz_convert(tz)
              .tz_localize(None))


def parse_price(data: dict, tz: str) -> pd.Series | None:
    if not data or "unix_seconds" not in data or "price" not in data:
        return None
    idx = _make_index(data["unix_seconds"], tz)
    return pd.Series(data["price"], index=idx, dtype=float).resample("h").mean()


def parse_power(data: dict, tz: str) -> dict:
    """
    解析 public_power，从 production_types 中提取：
      load          → load.csv
      solar         → solar.csv
      wind          → wind.csv  (offshore + onshore 求和)
      residual load → residual_load.csv
      其余全部      → generation.csv（长表，wind合并后写入）

    返回 dict:
      {
        "load":         pd.Series | None,
        "solar":        pd.Series | None,
        "wind":         pd.Series | None,
        "residual":     pd.Series | None,
        "generation":   pd.DataFrame,    # columns=category, index=datetime
      }
    """
    if not data or "unix_seconds" not in data:
        return {}

    idx = _make_index(data["unix_seconds"], tz)

    # 把 production_types 整理成 {lower_name: (orig_name, data_list)}
    raw: dict[str, tuple[str, list]] = {}
    for pt in data.get("production_types", []):
        orig  = pt.get("name", "").strip()
        lower = orig.lower()
        raw[lower] = (orig, pt.get("data", []))

    def to_series(key: str) -> pd.Series:
        orig, vals = raw[key]
        return pd.Series(vals, index=idx, dtype=float).resample("h").mean()

    def find_key(must_contain: list[str], must_exclude: list[str] = []) -> str | None:
        for k in raw:
            if all(m in k for m in must_contain) and all(e not in k for e in must_exclude):
                return k
        return None

    # ── 各指标的 key 查找 ────────────────────────────────────
    load_key      = find_key(["load"],            must_exclude=["residual"])
    solar_key     = find_key(["solar"],           must_exclude=["residual"])
    off_key       = find_key(["wind", "offshore"])
    on_key        = find_key(["wind", "onshore"])
    bare_wind_key = find_key(["wind"],            must_exclude=["offshore", "onshore", "residual"]) \
                    if not off_key and not on_key else None
    residual_key  = find_key(["residual", "load"])

    # ── Load ─────────────────────────────────────────────────
    load_s = to_series(load_key) if load_key else None

    # ── Solar ────────────────────────────────────────────────
    solar_s = to_series(solar_key) if solar_key else None

    # ── Wind（offshore + onshore 合并）───────────────────────
    if off_key and on_key:
        wind_s = to_series(off_key).add(to_series(on_key), fill_value=0)
    elif off_key:
        wind_s = to_series(off_key)
    elif on_key:
        wind_s = to_series(on_key)
    elif bare_wind_key:
        wind_s = to_series(bare_wind_key)
    else:
        wind_s = None

    # ── Residual Load ─────────────────────────────────────────
    residual_s = to_series(residual_key) if residual_key else None

    # ── Generation（长表）────────────────────────────────────
    # 排除 residual load；将 offshore+onshore 合并为单列 "Wind"
    skip = {residual_key, off_key, on_key} - {None}

    gen_dict: dict[str, pd.Series] = {}
    for lower, (orig, _) in raw.items():
        if lower in skip:
            continue
        gen_dict[orig] = to_series(lower)

    # 用合并后的 Wind 替换（如果原来是分开的）
    if (off_key or on_key) and wind_s is not None:
        for k in [off_key, on_key]:
            if k:
                orig_name = raw[k][0]
                gen_dict.pop(orig_name, None)
        gen_dict["Wind"] = wind_s

    gen_df = pd.DataFrame(gen_dict) if gen_dict else pd.DataFrame()

    return {
        "load":       load_s,
        "solar":      solar_s,
        "wind":       wind_s,
        "residual":   residual_s,
        "generation": gen_df,
    }


# ─────────────────────────────────────────────────────────────
# CSV 写入
# ─────────────────────────────────────────────────────────────

def fmt_index(index: pd.DatetimeIndex) -> list[str]:
    return [dt.strftime("%Y/%-m/%-d %-H:00") for dt in index]


def write_wide_csv(cols: dict[str, pd.Series], path: Path, label: str):
    if not cols:
        print(f"  [SKIP] {label}: 无数据")
        return
    df = pd.DataFrame(cols)
    df.index = fmt_index(df.index)
    df.index.name = "Date"
    df.to_csv(path)
    print(f"  ✓ {path.name}: {len(df)} 行 × {len(df.columns)} 国")


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("Energy Charts 数据更新")
    print("=" * 62)

    DATA_DIR.mkdir(exist_ok=True)

    # END_DATE 为 None 时自动用明天，否则用配置值
    end_date = END_DATE if END_DATE else (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"数据范围: {START_DATE} → {end_date}")
    print(f"国家数量: {len(COUNTRIES)}")
    print()

    price_cols:    dict[str, pd.Series] = {}
    load_cols:     dict[str, pd.Series] = {}
    solar_cols:    dict[str, pd.Series] = {}
    wind_cols:     dict[str, pd.Series] = {}
    residual_cols: dict[str, pd.Series] = {}
    gen_rows:      list[dict]           = []

    for cc in COUNTRIES:
        cfg      = COUNTRY_CONFIG[cc]
        tz       = cfg["tz"]
        bzn      = cfg["bzn"]
        col_name = cc.upper()

        print(f"[{col_name}]")

        # ── 请求1：价格 ───────────────────────────────────────
        print(f"  → /price?bzn={bzn}")
        price_data = fetch_price(bzn, START_DATE, end_date)
        time.sleep(REQUEST_DELAY)

        s = parse_price(price_data, tz)
        if s is not None:
            price_cols[col_name] = s
            print(f"     {s.notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无价格数据")

        # ── 请求2：发电（含负荷）─────────────────────────────
        print(f"  → /public_power?country={cc}")
        power_data = fetch_power(cc, START_DATE, end_date)
        time.sleep(REQUEST_DELAY)

        result = parse_power(power_data, tz) if power_data else {}

        for field, target_dict, label in [
            ("load",     load_cols,     "load"),
            ("solar",    solar_cols,    "solar"),
            ("wind",     wind_cols,     "wind"),
            ("residual", residual_cols, "residual_load"),
        ]:
            s = result.get(field)
            if s is not None:
                target_dict[col_name] = s
                print(f"     {label:<14} {s.notna().sum()} 有效值")
            else:
                print(f"     {label:<14} [WARN] 无数据")

        gen_df: pd.DataFrame = result.get("generation", pd.DataFrame())
        if not gen_df.empty:
            for cat in gen_df.columns:
                for dt, val in gen_df[cat].items():
                    gen_rows.append({
                        "date":     dt.strftime("%Y/%-m/%-d %-H:00"),
                        "country":  col_name,
                        "category": cat,
                        "value":    val,
                    })
            print(f"     generation     {len(gen_df.columns)} 类型, {len(gen_df)} 行")

        print()

    # ── 写文件 ────────────────────────────────────────────────
    print("保存文件...")
    write_wide_csv(price_cols,    DATA_DIR / "price.csv",         "price")
    write_wide_csv(load_cols,     DATA_DIR / "load.csv",          "load")
    write_wide_csv(solar_cols,    DATA_DIR / "solar.csv",         "solar")
    write_wide_csv(wind_cols,     DATA_DIR / "wind.csv",          "wind")
    write_wide_csv(residual_cols, DATA_DIR / "residual_load.csv", "residual_load")

    if gen_rows:
        df_gen = pd.DataFrame(gen_rows, columns=["date", "country", "category", "value"])
        df_gen.to_csv(DATA_DIR / "generation.csv", index=False)
        print(f"  ✓ generation.csv: {len(df_gen)} 行")
        print(f"     国家: {sorted(df_gen['country'].unique())}")
        print(f"     类型: {sorted(df_gen['category'].unique())}")
    else:
        print("  [SKIP] generation.csv: 无数据")

    print()
    print("=" * 62)
    print("完成!")
    print("=" * 62)


if __name__ == "__main__":
    main()
