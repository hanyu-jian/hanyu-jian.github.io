#!/usr/bin/env python3
"""
Energy Charts API 数据更新脚本
获取13个欧洲国家的电力数据并更新到CSV文件
输出：solar.csv, wind.csv, load.csv, residual_load.csv, price.csv, generation.csv
"""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# === 配置 ===
COUNTRIES = ["de", "fr", "es", "it", "gr", "ro", "hu", "at", "pl", "sk", "rs", "hr", "bg"]
START_DATE = "2024-01-01"
END_DATE = "2024-01-03"
REQUEST_DELAY = 1.5  # API请求间隔（秒）

# Bidding Zone 映射（用于价格 API）
BZN_MAP = {
    "de": "DE-LU", "fr": "FR", "es": "ES", "it": "IT-North", "gr": "GR",
    "ro": "RO", "hu": "HU", "at": "AT", "pl": "PL", "sk": "SK",
    "rs": "RS", "hr": "HR", "bg": "BG",
}

# 时区映射
TIMEZONE_MAP = {
    "de": "Europe/Berlin", "fr": "Europe/Paris", "es": "Europe/Madrid",
    "it": "Europe/Rome", "gr": "Europe/Athens", "ro": "Europe/Bucharest",
    "hu": "Europe/Budapest", "at": "Europe/Vienna", "pl": "Europe/Warsaw",
    "sk": "Europe/Bratislava", "rs": "Europe/Belgrade", "hr": "Europe/Zagreb", "bg": "Europe/Sofia",
}

# API 端点
API_BASE = "https://api.energy-charts.info"
DATA_DIR = Path("data")


def fetch_power_data(country: str, start: str, end: str) -> dict | None:
    """获取发电和负荷数据"""
    url = f"{API_BASE}/public_power"
    params = {"country": country, "start": start, "end": end}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  [ERROR] Power data for {country}: {e}")
        return None


def fetch_price_data(bzn: str, start: str, end: str) -> dict | None:
    """获取日前价格数据"""
    url = f"{API_BASE}/price"
    params = {"bzn": bzn, "start": start, "end": end}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  [ERROR] Price data for {bzn}: {e}")
        return None


def process_power_data(data: dict, country: str) -> pd.DataFrame:
    """
    处理发电/负荷数据，返回包含所有生产类型及负荷的宽表DataFrame。
    同时将 offshore 和 onshore 合并为 wind 列（如果两者都存在）。
    """
    if not data or "unix_seconds" not in data:
        return pd.DataFrame()

    tz = TIMEZONE_MAP[country]
    timestamps = pd.to_datetime(data["unix_seconds"], unit="s", utc=True)
    timestamps = timestamps.tz_convert(tz).tz_localize(None)

    df = pd.DataFrame({"datetime": timestamps})

    # 提取所有生产类型
    production_types = data.get("production_types", [])
    for pt in production_types:
        name = pt.get("name", "")
        values = pt.get("data", [])
        if name and values:
            df[name] = values

    # 如果同时有 wind offshore 和 wind onshore，合并为 wind
    if "wind offshore" in df.columns and "wind onshore" in df.columns:
        df["wind"] = df["wind offshore"].fillna(0) + df["wind onshore"].fillna(0)
        # 可选：删除原始列以保持整洁（不删除也可，但 generation 计算时需注意）
        # 我们保留原始列，因为 generation 处理时会自动匹配关键词
    elif "wind onshore" in df.columns:
        df["wind"] = df["wind onshore"]
    elif "wind offshore" in df.columns:
        df["wind"] = df["wind offshore"]

    # 重采样为小时均值
    df.set_index("datetime", inplace=True)
    df = df.resample("h").mean()
    return df


def process_price_data(data: dict, country: str) -> pd.DataFrame:
    """处理价格数据，返回小时均值的价格DataFrame"""
    if not data or "unix_seconds" not in data:
        return pd.DataFrame()

    tz = TIMEZONE_MAP[country]
    timestamps = pd.to_datetime(data["unix_seconds"], unit="s", utc=True)
    timestamps = timestamps.tz_convert(tz).tz_localize(None)

    prices = data.get("price", [])
    df = pd.DataFrame({"datetime": timestamps, "price": prices})
    df.set_index("datetime", inplace=True)
    df = df.resample("h").mean()
    return df


def col_match(columns: list, keywords: list) -> list:
    """匹配包含任意关键词的列名"""
    matched = []
    for col in columns:
        col_lower = col.lower()
        if any(kw.lower() in col_lower for kw in keywords):
            matched.append(col)
    return matched


def process_generation_data(power_df: pd.DataFrame, price_df: pd.DataFrame, country: str) -> pd.DataFrame:
    """
    从功率宽表和价格表中生成发电结构长表。
    power_df: 包含所有生产类型及负荷的宽表，索引为小时时间戳
    price_df: 价格表，索引为小时时间戳
    """
    if power_df.empty:
        return pd.DataFrame()

    cols = power_df.columns.tolist()

    # 计算各分类
    nuclear = power_df[col_match(cols, ["nuclear"])].sum(axis=1)
    hydro = power_df[col_match(cols, ["hydro"])].sum(axis=1)
    thermal = power_df[col_match(cols, ["fossil", "others"])].sum(axis=1)
    wind = power_df[col_match(cols, ["wind"])].sum(axis=1)
    solar = power_df[col_match(cols, ["solar"])].sum(axis=1)
    xborder = power_df[col_match(cols, ["cross border", "x-border"])].sum(axis=1)
    load = power_df[col_match(cols, ["load"])].sum(axis=1)

    # Other = 剩余发电（不在上述分类中的列）
    used_cols = set(col_match(cols, [
        "nuclear", "hydro", "fossil", "others", "wind", "solar",
        "cross border", "x-border", "load"
    ]))
    other_cols = [c for c in cols if c not in used_cols]
    other = power_df[other_cols].sum(axis=1) if other_cols else pd.Series(0, index=power_df.index)

    # DA Price（与功率表对齐）
    if not price_df.empty:
        da_price = price_df["price"].reindex(power_df.index)
    else:
        da_price = pd.Series(index=power_df.index, dtype=float)

    # 构建长格式 DataFrame
    country_upper = country.upper()
    categories = {
        "Nuclear": nuclear,
        "Hydro": hydro,
        "Thermal": thermal,
        "Wind": wind,
        "Solar": solar,
        "Other": other,
        "X-Border": xborder,
        "Load": load,
        "DA Price": da_price,
    }

    rows = []
    for cat_name, cat_series in categories.items():
        for dt, val in cat_series.items():
            rows.append({
                "Date": dt,
                "Country": country_upper,
                "Category": cat_name,
                "Value": val
            })

    return pd.DataFrame(rows)


def format_date_index(df: pd.DataFrame) -> pd.DataFrame:
    """格式化日期索引为 YYYY/M/D H:00"""
    df = df.copy()
    df.index = df.index.strftime("%-Y/%-m/%-d %-H:00").str.replace("^0", "", regex=True)
    df.index.name = "Date"
    return df


def format_date_column(df: pd.DataFrame, col: str = "Date") -> pd.DataFrame:
    """格式化 Date 列为 YYYY/M/D H:00"""
    df = df.copy()
    df[col] = pd.to_datetime(df[col]).dt.strftime("%-Y/%-m/%-d %-H:00").str.replace("^0", "", regex=True)
    return df


def main():
    """主函数"""
    print("=" * 60)
    print("Energy Charts 数据更新")
    print("=" * 60)

    DATA_DIR.mkdir(exist_ok=True)
    end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"数据范围: {START_DATE} 至 {end_date}")
    print(f"国家列表: {', '.join(COUNTRIES)}\n")

    # 存储各国数据
    all_solar = {}
    all_wind = {}
    all_load = {}
    all_price = {}
    all_generation = []

    for country in COUNTRIES:
        print(f"[{country.upper()}] 获取数据...")

        # 获取功率数据（宽表）
        power_data = fetch_power_data(country, START_DATE, end_date)
        time.sleep(REQUEST_DELAY)

        # 获取价格数据
        bzn = BZN_MAP[country]
        price_data = fetch_price_data(bzn, START_DATE, end_date)
        time.sleep(REQUEST_DELAY)

        if power_data is not None:
            power_df = process_power_data(power_data, country)

            if not power_df.empty:
                # 提取 solar, wind, load 列（如果存在）
                if "solar" in power_df.columns:
                    all_solar[country.upper()] = power_df["solar"]
                if "wind" in power_df.columns:
                    all_wind[country.upper()] = power_df["wind"]
                if "load" in power_df.columns:
                    all_load[country.upper()] = power_df["load"]

                # 处理价格数据（用于 generation）
                price_df = process_price_data(price_data, country) if price_data else pd.DataFrame()
                df_gen = process_generation_data(power_df, price_df, country)
                if not df_gen.empty:
                    all_generation.append(df_gen)

        if price_data is not None:
            price_df = process_price_data(price_data, country)
            if not price_df.empty and "price" in price_df.columns:
                all_price[country.upper()] = price_df["price"]

    print("\n处理数据...")

    # 创建宽格式 DataFrame
    df_solar = pd.DataFrame(all_solar)
    df_wind = pd.DataFrame(all_wind)
    df_load = pd.DataFrame(all_load)
    df_price = pd.DataFrame(all_price)

    # 合并 generation 数据
    if all_generation:
        df_generation = pd.concat(all_generation, ignore_index=True)
    else:
        df_generation = pd.DataFrame()

    # 计算 Residual Load = Load - Solar - Wind
    df_residual = pd.DataFrame(index=df_load.index)
    for country in df_load.columns:
        load_val = df_load[country] if country in df_load.columns else 0
        solar_val = df_solar[country] if country in df_solar.columns else 0
        wind_val = df_wind[country] if country in df_wind.columns else 0
        df_residual[country] = load_val - solar_val - wind_val

    # 格式化索引和日期列
    df_solar = format_date_index(df_solar)
    df_wind = format_date_index(df_wind)
    df_load = format_date_index(df_load)
    df_residual = format_date_index(df_residual)
    df_price = format_date_index(df_price)
    if not df_generation.empty:
        df_generation = format_date_column(df_generation)

    # 保存 CSV
    print("保存 CSV 文件...")
    df_solar.to_csv(DATA_DIR / "solar.csv")
    df_wind.to_csv(DATA_DIR / "wind.csv")
    df_load.to_csv(DATA_DIR / "load.csv")
    df_residual.to_csv(DATA_DIR / "residual_load.csv")
    df_price.to_csv(DATA_DIR / "price.csv")
    if not df_generation.empty:
        df_generation.to_csv(DATA_DIR / "generation.csv", index=False)
        print(f"  - generation.csv: {len(df_generation)} 行")
    print(f"  - solar.csv: {len(df_solar)} 行")
    print(f"  - wind.csv: {len(df_wind)} 行")
    print(f"  - load.csv: {len(df_load)} 行")
    print(f"  - residual_load.csv: {len(df_residual)} 行")
    print(f"  - price.csv: {len(df_price)} 行")

    print("\n" + "=" * 60)
    print("完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
