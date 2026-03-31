#!/usr/bin/env python3
"""
Energy Charts API 数据更新脚本（增量更新版）
- 每次获取最近 LOOKBACK_DAYS 天数据（默认7天）
- 与现有 CSV 合并，重叠部分以新数据覆盖（处理数据回溯纠错）
- 支持两种模式：
    --mode incremental  仅更新最近7天（默认，用于每日自动更新）
    --mode full         全量拉取 FULL_START_DATE → 今天（用于初始化/修复）
"""

import argparse
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

COUNTRIES       = list(COUNTRY_CONFIG.keys())
FULL_START_DATE = "2024-01-01"   # 全量模式起始日
LOOKBACK_DAYS   = 7              # 增量模式回溯天数
REQUEST_DELAY   = 1.5
API_BASE        = "https://api.energy-charts.info"
DATA_DIR        = Path("data")


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
    return (pd.to_datetime(unix_seconds, unit="s", utc=True)
              .tz_convert(tz)
              .tz_localize(None))


def parse_price(data: dict, tz: str) -> pd.Series | None:
    if not data or "unix_seconds" not in data or "price" not in data:
        return None
    idx = _make_index(data["unix_seconds"], tz)
    # ✅ 强制转换为数值型，防止API返回文本型数据
    prices = pd.to_numeric(data["price"], errors="coerce")
    return pd.Series(prices, index=idx, dtype=float).resample("h").mean()


def parse_power(data: dict, tz: str) -> dict:
    if not data or "unix_seconds" not in data:
        return {}

    idx = _make_index(data["unix_seconds"], tz)

    raw: dict[str, tuple[str, list]] = {}
    for pt in data.get("production_types", []):
        orig  = pt.get("name", "").strip()
        lower = orig.lower()
        raw[lower] = (orig, pt.get("data", []))

    def to_series(key: str) -> pd.Series:
        orig, vals = raw[key]
        # ✅ 强制转换为数值型，防止API返回文本型数据
        vals = pd.to_numeric(vals, errors="coerce")
        return pd.Series(vals, index=idx, dtype=float).resample("h").mean()

    def find_key(must_contain: list[str], must_exclude: list[str] = []) -> str | None:
        for k in raw:
            if all(m in k for m in must_contain) and all(e not in k for e in must_exclude):
                return k
        return None

    load_key      = find_key(["load"],  must_exclude=["residual"])
    solar_key     = find_key(["solar"], must_exclude=["residual"])
    off_key       = find_key(["wind", "offshore"])
    on_key        = find_key(["wind", "onshore"])
    bare_wind_key = find_key(["wind"],  must_exclude=["offshore", "onshore", "residual"]) \
                    if not off_key and not on_key else None
    residual_key  = find_key(["residual", "load"])

    load_s     = to_series(load_key)     if load_key     else None
    solar_s    = to_series(solar_key)    if solar_key    else None
    residual_s = to_series(residual_key) if residual_key else None

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

    skip = {residual_key, off_key, on_key} - {None}
    gen_dict: dict[str, pd.Series] = {}
    for lower, (orig, _) in raw.items():
        if lower in skip:
            continue
        gen_dict[orig] = to_series(lower)

    if (off_key or on_key) and wind_s is not None:
        for k in [off_key, on_key]:
            if k:
                gen_dict.pop(raw[k][0], None)
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
# CSV 合并写入（核心：新数据覆盖旧数据）
# ─────────────────────────────────────────────────────────────

def fmt_index(index: pd.DatetimeIndex) -> list[str]:
    return [dt.strftime("%Y/%-m/%-d %-H:00") for dt in index]


def merge_and_save_wide(new_cols: dict[str, pd.Series], path: Path, label: str):
    """
    宽表合并逻辑：
      1. 读取已有 CSV（若存在）
      2. 用新数据的行覆盖旧数据的对应行（combine_first 反向：新优先）
      3. 保存
    """
    if not new_cols:
        print(f"  [SKIP] {label}: 无新数据")
        return

    # 新数据 DataFrame（datetime index）
    new_df = pd.DataFrame(new_cols)

    if path.exists():
        old_df = pd.read_csv(path, index_col=0)
        # ✅ 读取旧CSV后强制所有列转为数值型，防止文本型数字污染计算
        old_df = old_df.apply(pd.to_numeric, errors="coerce")

        # 将新 df 的 index 格式化为字符串，与旧 df 对齐
        new_df.index = fmt_index(new_df.index)

        # 确保列对齐
        all_cols = old_df.columns.union(new_df.columns)
        old_df   = old_df.reindex(columns=all_cols)
        new_df   = new_df.reindex(columns=all_cols)

        old_df.update(new_df)                 # 覆盖重叠行
        merged = old_df.combine_first(new_df) # 追加新行
        merged.index.name = "Date"
        merged.sort_index(inplace=True)
        merged.to_csv(path)
        print(f"  ✓ {path.name}: 合并后 {len(merged)} 行 × {len(merged.columns)} 国")
    else:
        # 首次写入
        new_df.index = fmt_index(new_df.index)
        new_df.index.name = "Date"
        new_df.sort_index(inplace=True)
        new_df.to_csv(path)
        print(f"  ✓ {path.name}: 新建 {len(new_df)} 行 × {len(new_df.columns)} 国")


def merge_and_save_generation(new_rows: list[dict], gen_dir: Path):
    """
    长表合并：按 country 分文件，date+category 为联合主键，新值覆盖旧值
    """
    if not new_rows:
        print("  [SKIP] generation: 无新数据")
        return

    gen_dir.mkdir(exist_ok=True)
    df_new = pd.DataFrame(new_rows, columns=["date", "country", "category", "value"])
    # ✅ 确保新数据value列为数值型
    df_new["value"] = pd.to_numeric(df_new["value"], errors="coerce")

    for country, grp_new in df_new.groupby("country"):
        path = gen_dir / f"{country}.csv"
        grp_new = grp_new.drop(columns="country").reset_index(drop=True)

        if path.exists():
            grp_old = pd.read_csv(path)
            # ✅ 读取旧CSV后强制value列为数值型
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
    parser = argparse.ArgumentParser(description="Energy Charts 数据更新")
    parser.add_argument(
        "--mode", choices=["incremental", "full"], default="incremental",
        help="incremental=仅最近7天(默认), full=全量拉取"
    )
    args = parser.parse_args()

    today    = datetime.now()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    if args.mode == "full":
        start_date = FULL_START_DATE
        mode_label = "全量模式"
    else:
        start_date = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        mode_label = f"增量模式（最近 {LOOKBACK_DAYS} 天）"

    print("=" * 62)
    print(f"Energy Charts 数据更新  [{mode_label}]")
    print("=" * 62)
    print(f"数据范围: {start_date} → {tomorrow}")
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
        cfg      = COUNTRY_CONFIG[cc]
        tz       = cfg["tz"]
        bzn      = cfg["bzn"]
        col_name = cc.upper()

        print(f"[{col_name}]")

        # ── 价格 ──────────────────────────────────────────────
        print(f"  → /price?bzn={bzn}")
        price_data = fetch_price(bzn, start_date, tomorrow)
        time.sleep(REQUEST_DELAY)

        s = parse_price(price_data, tz)
        if s is not None:
            price_cols[col_name] = s
            print(f"     {s.notna().sum()} 有效值")
        else:
            print(f"     [WARN] 无价格数据")

        # ── 发电 / 负荷 ───────────────────────────────────────
        print(f"  → /public_power?country={cc}")
        power_data = fetch_power(cc, start_date, tomorrow)
        time.sleep(REQUEST_DELAY)

        result = parse_power(power_data, tz) if power_data else {}

        for field, target_dict, lbl in [
            ("load",     load_cols,     "load"),
            ("solar",    solar_cols,    "solar"),
            ("wind",     wind_cols,     "wind"),
            ("residual", residual_cols, "residual_load"),
        ]:
            s = result.get(field)
            if s is not None:
                target_dict[col_name] = s
                print(f"     {lbl:<14} {s.notna().sum()} 有效值")
            else:
                print(f"     {lbl:<14} [WARN] 无数据")

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
