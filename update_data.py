import requests
import pandas as pd
from datetime import datetime
import time
import os

# ============ 配置 ============
START_DATE = "2024-01-01"
END_DATE = "2024-01-03"  # 测试用短日期

COUNTRIES = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]
DATA_DIR = "data"

os.makedirs(DATA_DIR, exist_ok=True)

print("=" * 60)
print(f"下载数据: {START_DATE} to {END_DATE}")
print("=" * 60)

# ============ Generation 类型映射 ============
GENERATION_CATEGORIES = {
    "Nuclear": ["nuclear"],
    "Fossil Coal": ["lignite", "hard coal", "coal"],
    "Fossil Gas": ["fossil gas", "natural gas"],
    "Fossil Oil": ["fossil oil", "oil"],
    "Hydro": ["hydro"],
    "Biomass": ["biomass"],
}

# ============ 辅助函数 ============
def process_timestamp(unix_ts):
    """Unix时间戳转为 YYYY/M/D H:00 格式"""
    dt = datetime.utcfromtimestamp(unix_ts)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:00"

def aggregate_hourly_dict(values_15min, timestamps):
    """15分钟数据聚合成小时平均，返回 {date_str: value} 字典"""
    result = {}
    n = len(values_15min)
    hours = n // 4
    
    for h in range(hours):
        i = h * 4
        chunk = values_15min[i:i+4]
        valid = [v for v in chunk if v is not None]
        
        if valid:
            avg = sum(valid) / len(valid)
        else:
            avg = 0.0
        
        # 用该小时第一个时间戳作为时间标识
        date_str = process_timestamp(timestamps[i])
        result[date_str] = avg
    
    return result

# ============ 下载所有国家 ============
print("\n下载所有国家数据...")

url = "https://api.energy-charts.info/public_power"

# 存储数据
all_solar = {}      # {country: {date_str: value}}
all_wind = {}       # {country: {date_str: value}}
all_residual = {}   # {country: {date_str: value}}
all_generation = [] # [{Date, country, category, value_MW}, ...]

for country in COUNTRIES:
    print(f"\n{country}...", end=" ")
    
    params = {"country": country.lower(), "start": START_DATE, "end": END_DATE}
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            print(f"错误 {response.status_code}")
            continue
            
        data = response.json()
        
        if "unix_seconds" not in data:
            print("无数据")
            continue
        
        timestamps = data["unix_seconds"]
        n = len(timestamps)
        print(f"{n} 条原始数据", end=" ")
        
        # 提取各类型数据
        production_types = {pt.get("name", ""): pt.get("data", []) for pt in data.get("production_types", [])}
        
        # Solar
        solar_raw = production_types.get("Solar", [0.0] * n)
        solar_raw = [v if v is not None else 0.0 for v in solar_raw]
        all_solar[country] = aggregate_hourly_dict(solar_raw, timestamps)
        
        # Wind (onshore + offshore)
        wind_on = production_types.get("Wind onshore", [0.0] * n)
        wind_off = production_types.get("Wind offshore", [0.0] * n)
        wind_raw = []
        for i in range(n):
            w = 0.0
            if i < len(wind_on) and wind_on[i] is not None:
                w += wind_on[i]
            if i < len(wind_off) and wind_off[i] is not None:
                w += wind_off[i]
            wind_raw.append(w)
        all_wind[country] = aggregate_hourly_dict(wind_raw, timestamps)
        
        # Residual Load
        residual_raw = production_types.get("Residual load", [0.0] * n)
        residual_raw = [v if v is not None else 0.0 for v in residual_raw]
        all_residual[country] = aggregate_hourly_dict(residual_raw, timestamps)
        
        hours = len(all_solar[country])
        print(f"-> {hours} 小时", end=" ")
        
        # Generation 数据 (长格式)
        for pt_name, pt_data in production_types.items():
            for cat, keywords in GENERATION_CATEGORIES.items():
                if any(kw.lower() in pt_name.lower() for kw in keywords):
                    # 聚合成小时
                    hourly = aggregate_hourly_dict(pt_data, timestamps)
                    for date_str, value in hourly.items():
                        if value != 0.0:
                            all_generation.append({
                                "Date": date_str,
                                "country": country,
                                "category": cat,
                                "value_MW": value
                            })
                    break
        
        print(f"Gen:{len([g for g in all_generation if g['country']==country])}")
        
    except Exception as e:
        print(f"错误: {e}")
    
    time.sleep(1)

# ============ 创建宽格式 DataFrame ============
print("\n" + "=" * 60)
print("创建 DataFrame...")

def create_wide_df(data_dict, countries):
    """从 {country: {date: value}} 创建宽格式 DataFrame"""
    all_dates = set()
    for d in data_dict.values():
        all_dates.update(d.keys())
    
    if not all_dates:
        return pd.DataFrame(columns=["Date"] + countries)
    
    all_dates = sorted(all_dates, key=lambda x: datetime.strptime(x, "%Y/%m/%d %H:%M"))
    
    df = pd.DataFrame({"Date": all_dates})
    for c in countries:
        df[c] = df["Date"].map(lambda x, c=c: data_dict.get(c, {}).get(x, 0.0))
    
    return df

# Solar (宽格式)
solar_df = create_wide_df(all_solar, COUNTRIES)
print(f"\nSolar: {len(solar_df)} 行")
print(solar_df.head())

# Wind (宽格式)
wind_df = create_wide_df(all_wind, COUNTRIES)
print(f"\nWind: {len(wind_df)} 行")
print(wind_df.head())

# Residual Load (宽格式)
residual_df = create_wide_df(all_residual, COUNTRIES)
print(f"\nResidual Load: {len(residual_df)} 行")
print(residual_df.head())

# Generation (长格式)
if all_generation:
    gen_df = pd.DataFrame(all_generation)
    # 同一时间、国家、类别的数据合并
    gen_df = gen_df.groupby(["Date", "country", "category"])["value_MW"].sum().reset_index()
    print(f"\nGeneration: {len(gen_df)} 行")
    print(gen_df.head(15))
else:
    gen_df = pd.DataFrame(columns=["Date", "country", "category", "value_MW"])

# ============ 保存 CSV ============
print("\n" + "=" * 60)
print("保存 CSV 文件...")

solar_df.to_csv(os.path.join(DATA_DIR, "solar.csv"), index=False)
print("  solar.csv")

wind_df.to_csv(os.path.join(DATA_DIR, "wind.csv"), index=False)
print("  wind.csv")

residual_df.to_csv(os.path.join(DATA_DIR, "residual_load.csv"), index=False)
print("  residual_load.csv")

gen_df.to_csv(os.path.join(DATA_DIR, "generation.csv"), index=False)
print("  generation.csv")

print("\n完成!")
print("=" * 60)
