import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import os

# ============ 配置 ============
START_DATE = "2024-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")
COUNTRIES = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]
DATA_DIR = "data"
REQUEST_DELAY = 0.5

os.makedirs(DATA_DIR, exist_ok=True)

print(f"下载范围: {START_DATE} → {END_DATE}")
print(f"国家: {COUNTRIES}")
print("=" * 60)

# ============ API 请求函数 ============
def fetch_power_data(country, start, end):
    """获取 public_power 数据"""
    url = "https://api.energy-charts.info/public_power"
    params = {
        "country": country.lower(),  # ✅ 转小写
        "start": start,
        "end": end
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None

def generate_date_ranges(start_date, end_date, days=90):
    """生成90天分批的日期范围"""
    ranges = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    while current < end:
        batch_end = min(current + timedelta(days=days), end)
        ranges.append((current.strftime("%Y-%m-%d"), batch_end.strftime("%Y-%m-%d")))
        current = batch_end
    
    return ranges

def process_timestamp(unix_seconds):
    """Unix 秒转换为 Europe/Berlin 时区"""
    dt = datetime.utcfromtimestamp(unix_seconds) + timedelta(hours=1)
    return dt.strftime("%Y/%m/%d %H:%M")

# ============ 下载数据 ============
all_solar = {}
all_wind = {}
all_residual = {}
all_generation = []

date_ranges = generate_date_ranges(START_DATE, END_DATE)
print(f"分 {len(date_ranges)} 批下载\n")

for country in COUNTRIES:
    print(f"🌍 {country}")
    
    country_solar = {}
    country_wind = {}
    country_residual = {}
    
    for i, (start, end) in enumerate(date_ranges):
        print(f"  📅 批次 {i+1}/{len(date_ranges)}: {start} → {end}")
        
        data = fetch_power_data(country, start, end)
        time.sleep(REQUEST_DELAY)
        
        if not data or "unix_seconds" not in data:
            print(f"  ⚠️ 无数据")
            continue
        
        timestamps = data["unix_seconds"]
        
        for key, values in data.items():
            if key in ["unix_seconds", "deprecated"]:
                continue
            if not isinstance(values, list) or len(values) != len(timestamps):
                continue
            
            key_lower = key.lower()
            
            for ts, val in zip(timestamps, values):
                dt_str = process_timestamp(ts)
                value = val if val is not None else 0
                
                # Solar
                if "solar" in key_lower and "forecast" not in key_lower:
                    if dt_str not in country_solar:
                        country_solar[dt_str] = 0
                    country_solar[dt_str] += value
                
                # Wind (onshore + offshore)
                elif "wind" in key_lower and "forecast" not in key_lower:
                    if dt_str not in country_wind:
                        country_wind[dt_str] = 0
                    country_wind[dt_str] += value
                
                # Residual Load (直接从API获取)
                elif "residual" in key_lower and "load" in key_lower:
                    country_residual[dt_str] = value
                
                # Generation 分类 (排除 load, residual, forecast)
                if "load" not in key_lower and "residual" not in key_lower and "forecast" not in key_lower:
                    if "nuclear" in key_lower:
                        category = "Nuclear"
                    elif "lignite" in key_lower or "brown coal" in key_lower:
                        category = "Fossil Coal"
                    elif "hard coal" in key_lower:
                        category = "Fossil Coal"
                    elif "coal" in key_lower:
                        category = "Fossil Coal"
                    elif "gas" in key_lower:
                        category = "Fossil Gas"
                    elif "oil" in key_lower:
                        category = "Fossil Oil"
                    elif "solar" in key_lower:
                        category = "Solar"
                    elif "wind" in key_lower:
                        category = "Wind"
                    elif "hydro" in key_lower:
                        category = "Hydro"
                    elif "biomass" in key_lower:
                        category = "Biomass"
                    elif "geothermal" in key_lower or "waste" in key_lower or "other" in key_lower:
                        category = "Other"
                    else:
                        category = "Other"
                    
                    all_generation.append({
                        "datetime": dt_str,
                        "country": country,
                        "category": category,
                        "value_MW": value
                    })
    
    all_solar[country] = country_solar
    all_wind[country] = country_wind
    all_residual[country] = country_residual
    print(f"  ✅ 完成\n")

# ============ 处理并保存数据 ============
print("=" * 60)
print("处理数据...\n")

def create_wide_df(data_dict, countries):
    """创建宽格式 DataFrame"""
    all_times = set()
    for country_data in data_dict.values():
        all_times.update(country_data.keys())
    all_times = sorted(all_times)
    
    df = pd.DataFrame({"Date": all_times})
    for country in countries:
        df[country] = df["Date"].map(lambda x: data_dict.get(country, {}).get(x, 0))
    
    # 重采样到小时
    df["Date"] = pd.to_datetime(df["Date"], format="%Y/%m/%d %H:%M")
    df = df.set_index("Date").resample("H").mean().reset_index()
    df["Date"] = df["Date"].dt.strftime("%Y/%m/%d %H:%M")
    
    return df

# 1. Solar
print("📊 solar.csv...")
solar_df = create_wide_df(all_solar, COUNTRIES)
solar_df.to_csv(os.path.join(DATA_DIR, "solar.csv"), index=False)
print(f"  ✅ {len(solar_df)} 行")

# 2. Wind
print("📊 wind.csv...")
wind_df = create_wide_df(all_wind, COUNTRIES)
wind_df.to_csv(os.path.join(DATA_DIR, "wind.csv"), index=False)
print(f"  ✅ {len(wind_df)} 行")

# 3. Residual Load (直接从API)
print("📊 residual_load.csv...")
residual_df = create_wide_df(all_residual, COUNTRIES)
residual_df.to_csv(os.path.join(DATA_DIR, "residual_load.csv"), index=False)
print(f"  ✅ {len(residual_df)} 行")

# 4. Generation (长格式)
print("📊 generation.csv...")
gen_df = pd.DataFrame(all_generation)
gen_df = gen_df.groupby(["datetime", "country", "category"])["value_MW"].sum().reset_index()
gen_df["datetime"] = pd.to_datetime(gen_df["datetime"], format="%Y/%m/%d %H:%M")
gen_df = gen_df.groupby([pd.Grouper(key="datetime", freq="H"), "country", "category"])["value_MW"].mean().reset_index()
gen_df["datetime"] = gen_df["datetime"].dt.strftime("%Y/%m/%d %H:%M")
gen_df.to_csv(os.path.join(DATA_DIR, "generation.csv"), index=False)
print(f"  ✅ {len(gen_df)} 行")

print("\n" + "=" * 60)
print("🎉 全部完成!")
print(f"文件: {DATA_DIR}/solar.csv, wind.csv, residual_load.csv, generation.csv")
