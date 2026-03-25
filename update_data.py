import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import os

# ============ 配置 ============
TEST_DATE = "2024-01-01"  # 测试一天
TEST_END = "2024-01-02"

COUNTRIES = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]
DATA_DIR = "data"

os.makedirs(DATA_DIR, exist_ok=True)

print("=" * 60)
print(f"🧪 测试下载: {TEST_DATE} 一天数据")
print("=" * 60)

# ============ 辅助函数 ============
def process_timestamp(unix_seconds):
    dt = datetime.utcfromtimestamp(unix_seconds) + timedelta(hours=1)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"

def format_date(dt):
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"

# ============ 测试单个请求 ============
print("\n📡 测试 API 响应结构 (DE)...")
url = "https://api.energy-charts.info/public_power"
params = {"country": "de", "start": TEST_DATE, "end": TEST_END}

response = requests.get(url, params=params, timeout=30)
print(f"状态码: {response.status_code}")

data = response.json()
print(f"\n所有字段:")
for key in sorted(data.keys()):
    if isinstance(data[key], list):
        non_null = sum(1 for v in data[key] if v is not None)
        print(f"  {key}: {len(data[key])} 条, {non_null} 非空")
    else:
        print(f"  {key}: {data[key]}")

# ============ 下载所有国家 ============
print("\n" + "=" * 60)
print("下载所有国家...")
print("=" * 60)

all_solar = {}
all_wind = {}
all_residual = {}
all_generation = []

for country in COUNTRIES:
    print(f"\n🌍 {country}...", end=" ")
    
    params = {"country": country.lower(), "start": TEST_DATE, "end": TEST_END}
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            print(f"❌ 状态码 {response.status_code}")
            continue
            
        data = response.json()
        
        if "unix_seconds" not in data:
            print(f"❌ 无数据")
            continue
        
        timestamps = data["unix_seconds"]
        print(f"✅ {len(timestamps)} 条时间戳")
        
        country_solar = {}
        country_wind = {}
        country_residual = {}
        
        for key, values in data.items():
            if key == "unix_seconds" or not isinstance(values, list):
                continue
            if len(values) != len(timestamps):
                continue
            
            key_lower = key.lower()
            
            for ts, val in zip(timestamps, values):
                if val is None:
                    continue
                dt_str = process_timestamp(ts)
                
                # Solar
                if "solar" in key_lower and "forecast" not in key_lower:
                    country_solar[dt_str] = country_solar.get(dt_str, 0) + val
                
                # Wind
                if "wind" in key_lower and "forecast" not in key_lower:
                    country_wind[dt_str] = country_wind.get(dt_str, 0) + val
                
                # Residual Load
                if "residual" in key_lower:
                    country_residual[dt_str] = val
                
                # Generation
                if "nuclear" in key_lower:
                    cat = "Nuclear"
                elif "lignite" in key_lower or "hard_coal" in key_lower:
                    cat = "Fossil Coal"
                elif "gas" in key_lower:
                    cat = "Fossil Gas"
                elif "oil" in key_lower:
                    cat = "Fossil Oil"
                elif "solar" in key_lower and "forecast" not in key_lower:
                    cat = "Solar"
                elif "wind" in key_lower and "forecast" not in key_lower:
                    cat = "Wind"
                elif "hydro" in key_lower:
                    cat = "Hydro"
                elif "biomass" in key_lower:
                    cat = "Biomass"
                else:
                    continue  # 跳过其他
                
                all_generation.append({
                    "Date": dt_str, 
                    "country": country, 
                    "category": cat, 
                    "value_MW": val
                })
        
        all_solar[country] = country_solar
        all_wind[country] = country_wind
        all_residual[country] = country_residual
        
        print(f"    Solar: {len(country_solar)}, Wind: {len(country_wind)}, Residual: {len(country_residual)}")
        
    except Exception as e:
        print(f"❌ 错误: {e}")
    
    time.sleep(1)

# ============ 创建 DataFrame ============
print("\n" + "=" * 60)
print("创建 DataFrame...")
print("=" * 60)

def create_wide_df(data_dict, countries):
    all_times = set()
    for d in data_dict.values():
        all_times.update(d.keys())
    
    if not all_times:
        return pd.DataFrame(columns=["Date"] + countries)
    
    all_times = sorted(all_times)
    df = pd.DataFrame({"Date": all_times})
    
    for c in countries:
        df[c] = df["Date"].map(lambda x, c=c: data_dict.get(c, {}).get(x, 0))
    
    return df

# Solar
solar_df = create_wide_df(all_solar, COUNTRIES)
print(f"\nSolar DataFrame: {len(solar_df)} 行")
print(solar_df.head(10))
solar_df.to_csv(os.path.join(DATA_DIR, "solar.csv"), index=False)

# Wind
wind_df = create_wide_df(all_wind, COUNTRIES)
print(f"\nWind DataFrame: {len(wind_df)} 行")
print(wind_df.head(10))
wind_df.to_csv(os.path.join(DATA_DIR, "wind.csv"), index=False)

# Residual Load
residual_df = create_wide_df(all_residual, COUNTRIES)
print(f"\nResidual Load DataFrame: {len(residual_df)} 行")
print(residual_df.head(10))
residual_df.to_csv(os.path.join(DATA_DIR, "residual_load.csv"), index=False)

# Generation
if all_generation:
    gen_df = pd.DataFrame(all_generation)
    gen_df = gen_df.groupby(["Date", "country", "category"])["value_MW"].sum().reset_index()
    print(f"\nGeneration DataFrame: {len(gen_df)} 行")
    print(gen_df.head(20))
    gen_df.to_csv(os.path.join(DATA_DIR, "generation.csv"), index=False)
else:
    print("\n⚠️ Generation 无数据")

print("\n" + "=" * 60)
print("✅ 测试完成! 检查 data/ 文件夹")
print("=" * 60)
