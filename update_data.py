import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import os

# ============ 配置 ============
TEST_DATE = "2024-01-01"
TEST_END = "2024-01-02"

COUNTRIES = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]
DATA_DIR = "data"

os.makedirs(DATA_DIR, exist_ok=True)

print("=" * 60)
print(f"测试下载: {TEST_DATE} 一天数据")
print("=" * 60)

# ============ 辅助函数 ============
def process_timestamp(unix_seconds):
    """转换时间戳到欧洲时间格式"""
    dt = datetime.utcfromtimestamp(unix_seconds) + timedelta(hours=1)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"

def get_production_data(data, name_contains):
    """从 production_types 中提取指定类型的数据"""
    for pt in data.get("production_types", []):
        if name_contains.lower() in pt.get("name", "").lower():
            return pt.get("data", [])
    return []

def sum_production_data(data, name_list):
    """合并多个类型的数据"""
    result = None
    for pt in data.get("production_types", []):
        name = pt.get("name", "").lower()
        for target in name_list:
            if target.lower() in name:
                vals = pt.get("data", [])
                if result is None:
                    result = [0] * len(vals)
                for i, v in enumerate(vals):
                    if v is not None:
                        result[i] += v
                break
    return result

# ============ 下载所有国家 ============
print("\n下载所有国家...")

all_solar = {}
all_wind = {}
all_residual = {}
all_generation = []

url = "https://api.energy-charts.info/public_power"

for country in COUNTRIES:
    print(f"\n{country}...", end=" ")
    
    params = {"country": country.lower(), "start": TEST_DATE, "end": TEST_END}
    
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
        print(f"{len(timestamps)} 条", end=" ")
        
        # 提取各类数据
        solar_data = get_production_data(data, "Solar")
        wind_onshore = get_production_data(data, "Wind onshore")
        wind_offshore = get_production_data(data, "Wind offshore")
        residual_data = get_production_data(data, "Residual load")
        
        # 合并 Wind
        wind_data = []
        for i in range(len(timestamps)):
            w = 0
            if wind_onshore and i < len(wind_onshore) and wind_onshore[i]:
                w += wind_onshore[i]
            if wind_offshore and i < len(wind_offshore) and wind_offshore[i]:
                w += wind_offshore[i]
            wind_data.append(w)
        
        # 存储数据
        country_solar = {}
        country_wind = {}
        country_residual = {}
        
        for i, ts in enumerate(timestamps):
            dt_str = process_timestamp(ts)
            
            if solar_data and i < len(solar_data) and solar_data[i] is not None:
                country_solar[dt_str] = solar_data[i]
            
            if wind_data and i < len(wind_data):
                country_wind[dt_str] = wind_data[i]
            
            if residual_data and i < len(residual_data) and residual_data[i] is not None:
                country_residual[dt_str] = residual_data[i]
        
        all_solar[country] = country_solar
        all_wind[country] = country_wind
        all_residual[country] = country_residual
        
        print(f"Solar:{len(country_solar)} Wind:{len(country_wind)} Residual:{len(country_residual)}")
        
        # Generation 数据
        category_map = {
            "Nuclear": ["nuclear"],
            "Fossil Coal": ["lignite", "hard coal"],
            "Fossil Gas": ["Fossil gas"],
            "Fossil Oil": ["Fossil oil"],
            "Solar": ["Solar"],
            "Wind": ["Wind onshore", "Wind offshore"],
            "Hydro": ["Hydro"],
            "Biomass": ["Biomass"],
        }
        
        for pt in data.get("production_types", []):
            pt_name = pt.get("name", "")
            pt_data = pt.get("data", [])
            
            for cat, keywords in category_map.items():
                if any(kw.lower() in pt_name.lower() for kw in keywords):
                    for i, val in enumerate(pt_data):
                        if val is not None and i < len(timestamps):
                            all_generation.append({
                                "Date": process_timestamp(timestamps[i]),
                                "country": country,
                                "category": cat,
                                "value_MW": val
                            })
                    break
        
    except Exception as e:
        print(f"错误: {e}")
    
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
print(f"\nSolar: {len(solar_df)} 行")
print(solar_df.head())
solar_df.to_csv(os.path.join(DATA_DIR, "solar.csv"), index=False)

# Wind
wind_df = create_wide_df(all_wind, COUNTRIES)
print(f"\nWind: {len(wind_df)} 行")
print(wind_df.head())
wind_df.to_csv(os.path.join(DATA_DIR, "wind.csv"), index=False)

# Residual Load
residual_df = create_wide_df(all_residual, COUNTRIES)
print(f"\nResidual Load: {len(residual_df)} 行")
print(residual_df.head())
residual_df.to_csv(os.path.join(DATA_DIR, "residual_load.csv"), index=False)

# Generation
if all_generation:
    gen_df = pd.DataFrame(all_generation)
    gen_df = gen_df.groupby(["Date", "country", "category"])["value_MW"].sum().reset_index()
    print(f"\nGeneration: {len(gen_df)} 行")
    print(gen_df.head(10))
    gen_df.to_csv(os.path.join(DATA_DIR, "generation.csv"), index=False)

print("\n" + "=" * 60)
print("完成! 检查 data/ 文件夹")
print("=" * 60)
