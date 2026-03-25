#!/usr/bin/env python3
"""
Energy Charts API 数据更新脚本
从 Energy Charts API 获取欧洲各国电力数据并保存为 CSV
"""

import requests
import pandas as pd
import time
from pathlib import Path

# 配置
COUNTRIES = ["de", "fr", "es", "it", "gr", "ro", "hu", "at", "pl", "sk", "rs", "hr", "bg"]
START_DATE = "2024-01-01"
END_DATE = "2024-01-03"
SAVE_PATH = Path("data")
REQUEST_DELAY = 1.5

# 国家时区映射
TIMEZONE_MAP = {
    "de": "Europe/Berlin",
    "fr": "Europe/Paris",
    "es": "Europe/Madrid",
    "it": "Europe/Rome",
    "gr": "Europe/Athens",
    "ro": "Europe/Bucharest",
    "hu": "Europe/Budapest",
    "at": "Europe/Vienna",
    "pl": "Europe/Warsaw",
    "sk": "Europe/Bratislava",
    "rs": "Europe/Belgrade",
    "hr": "Europe/Zagreb",
    "bg": "Europe/Sofia",
}

# Bidding Zone 映射（用于价格 API）
BZN_MAP = {
    "de": "DE-LU",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "gr": "GR",
    "ro": "RO",
    "hu": "HU",
    "at": "AT",
    "pl": "PL",
    "sk": "SK",
    "rs": "RS",
    "hr": "HR",
    "bg": "BG",
}

# 发电类型映射
GENERATION_MAP = {
    "nuclear": "Nuclear",
    "lignite": "Fossil Coal",
    "hard coal": "Fossil Coal",
    "coal": "Fossil Coal",
    "fossil gas": "Fossil Gas",
    "natural gas": "Fossil Gas",
    "fossil oil": "Fossil Oil",
    "oil": "Fossil Oil",
    "hydro": "Hydro",
    "biomass": "Biomass",
    "geothermal": "Other",
    "waste": "Other",
    "other": "Other",
}


def fetch_data(country, start_date, end_date, data_type="public_power"):
    """获取指定国家的数据，返回当地时间"""
    if data_type == "price":
        # 价格 API 使用 bzn 参数
        bzn = BZN_MAP.get(country, country.upper())
        base_url = "https://api.energy-charts.info/price"
        params = {
            "bzn": bzn,
            "start": start_date,
            "end": end_date
        }
    else:
        base_url = f"https://api.energy-charts.info/{data_type}"
        params = {
            "country": country,
            "start": start_date,
            "end": end_date
        }
    
    response = requests.get(base_url, params=params)
    response.raise_for_status()
    data = response.json()
    
    # 转换为当地时间
    tz = TIMEZONE_MAP.get(country, "Europe/Berlin")
    timestamps = (
        pd.to_datetime(data["unix_seconds"], unit="s", utc=True)
        .tz_convert(tz)
        .tz_localize(None)  # 去掉时区标记
    )
    
    return data, timestamps


def extract_series(data, timestamps, keyword):
    """从 API 数据中提取指定类型的序列"""
    for pt in data.get("production_types", []):
        name = pt.get("name", "").lower()
        if keyword in name:
            values = pt.get("data", [])
            return pd.Series(values, index=timestamps, name=keyword)
    return None


def extract_price(data, timestamps):
    """从价格 API 数据中提取 Day-Ahead 价格"""
    # 价格数据结构不同，直接在 price 字段
    if "price" in data:
        return pd.Series(data["price"], index=timestamps, name="price")
    return None


def aggregate_to_hourly(series):
    """将15分钟数据聚合为小时平均"""
    if series is None:
        return None
    return series.resample("h").mean()


def format_datetime_index(df):
    """格式化日期索引为 YYYY/M/D H:00"""
    df = df.copy()
    df.index = df.index.strftime("%Y/%-m/%-d %-H:00")
    df.index.name = "Date"
    return df


def process_power_data(country):
    """处理单个国家的发电数据"""
    print(f"  获取 {country.upper()} 发电数据...")
    
    try:
        data, timestamps = fetch_data(country, START_DATE, END_DATE, "public_power")
    except Exception as e:
        print(f"    错误: {e}")
        return None
    
    result = {"timestamps": timestamps}
    
    # 提取各类数据
    for pt in data.get("production_types", []):
        name = pt.get("name", "")
        values = pt.get("data", [])
        result[name] = values
    
    return result


def process_price_data(country):
    """处理单个国家的价格数据"""
    print(f"  获取 {country.upper()} 价格数据...")
    
    try:
        data, timestamps = fetch_data(country, START_DATE, END_DATE, "price")
    except Exception as e:
        print(f"    错误: {e}")
        return None
    
    if "price" not in data:
        print(f"    警告: 无价格数据")
        return None
    
    return {
        "timestamps": timestamps,
        "price": data["price"]
    }


def build_wide_df(country_data_dict, value_key, keyword_match=None):
    """
    构建宽格式 DataFrame
    country_data_dict: {country: {timestamps, key1: [...], key2: [...]}}
    value_key: 精确匹配的键名，或 None 使用 keyword_match
    keyword_match: 关键词匹配（小写）
    """
    series_list = []
    
    for country, data in country_data_dict.items():
        if data is None:
            continue
        
        timestamps = data["timestamps"]
        values = None
        
        if value_key and value_key in data:
            values = data[value_key]
        elif keyword_match:
            # 关键词匹配
            for key, val in data.items():
                if key == "timestamps":
                    continue
                if keyword_match in key.lower():
                    values = val
                    break
        
        if values is not None:
            series = pd.Series(values, index=timestamps, name=country.upper())
            series = aggregate_to_hourly(series)
            series_list.append(series)
    
    if not series_list:
        return None
    
    df = pd.concat(series_list, axis=1)
    df = df.sort_index()
    
    # 过滤起始日期
    df = df[df.index >= START_DATE]
    
    return format_datetime_index(df)


def build_generation_long_df(country_data_dict):
    """构建发电数据的长格式 DataFrame"""
    records = []
    
    for country, data in country_data_dict.items():
        if data is None:
            continue
        
        timestamps = data["timestamps"]
        
        for key, values in data.items():
            if key == "timestamps":
                continue
            
            # 匹配发电类型
            key_lower = key.lower()
            category = None
            
            for pattern, cat in GENERATION_MAP.items():
                if pattern in key_lower:
                    category = cat
                    break
            
            if category is None:
                continue
            
            # 创建 Series 并聚合
            series = pd.Series(values, index=timestamps)
            series = aggregate_to_hourly(series)
            
            # 过滤起始日期
            series = series[series.index >= START_DATE]
            
            for ts, val in series.items():
                if pd.notna(val):
                    records.append({
                        "Date": ts.strftime("%Y/%-m/%-d %-H:00"),
                        "country": country.upper(),
                        "category": category,
                        "value_MW": val
                    })
    
    if not records:
        return None
    
    df = pd.DataFrame(records)
    
    # 合并相同 Date/country/category 的值
    df = df.groupby(["Date", "country", "category"], as_index=False)["value_MW"].sum()
    
    return df


def build_residual_load(solar_df, wind_df, load_df):
    """计算残余负荷 = Load - Solar - Wind"""
    if load_df is None:
        return None
    
    residual = load_df.copy()
    
    for col in residual.columns:
        if solar_df is not None and col in solar_df.columns:
            residual[col] = residual[col] - solar_df[col].fillna(0)
        if wind_df is not None and col in wind_df.columns:
            residual[col] = residual[col] - wind_df[col].fillna(0)
    
    return residual


def main():
    print("=" * 50)
    print("Energy Charts 数据更新")
    print(f"日期范围: {START_DATE} 至 {END_DATE}")
    print(f"国家: {', '.join(c.upper() for c in COUNTRIES)}")
    print("=" * 50)
    
    # 确保输出目录存在
    SAVE_PATH.mkdir(exist_ok=True)
    
    # 获取所有国家的发电数据
    print("\n[1/2] 获取发电数据...")
    power_data = {}
    for country in COUNTRIES:
        power_data[country] = process_power_data(country)
        time.sleep(REQUEST_DELAY)
    
    # 获取所有国家的价格数据
    print("\n[2/2] 获取价格数据...")
    price_data = {}
    for country in COUNTRIES:
        price_data[country] = process_price_data(country)
        time.sleep(REQUEST_DELAY)
    
    # 构建各类 DataFrame
    print("\n处理数据...")
    
    solar_df = build_wide_df(power_data, None, keyword_match="solar")
    wind_df = build_wide_df(power_data, None, keyword_match="wind")
    load_df = build_wide_df(power_data, None, keyword_match="load")
    price_df = build_wide_df(price_data, "price")
    
    residual_df = build_residual_load(solar_df, wind_df, load_df)
    generation_df = build_generation_long_df(power_data)
    
    # 保存 CSV
    print("\n保存文件...")
    
    files = [
        ("solar.csv", solar_df),
        ("wind.csv", wind_df),
        ("load.csv", load_df),
        ("residual_load.csv", residual_df),
        ("price.csv", price_df),
        ("generation.csv", generation_df),
    ]
    
    for filename, df in files:
        if df is not None:
            filepath = SAVE_PATH / filename
            df.to_csv(filepath, index=(filename != "generation.csv"))
            print(f"  ✓ {filename}: {len(df)} 行")
        else:
            print(f"  ✗ {filename}: 无数据")
    
    print("\n完成！")


if __name__ == "__main__":
    main()
