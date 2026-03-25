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
REQUEST_DELAY = 1.0
MAX_RETRIES = 3

os.makedirs(DATA_DIR, exist_ok=True)

print(f"下载范围: {START_DATE} → {END_DATE}")
print(f"国家: {COUNTRIES}")
print("=" * 60)

# ============ API 请求函数 ============
def fetch_power_data(country, start, end):
    """获取 public_power 数据，带重试机制"""
    url = "https://api.energy-charts.info/public_power"
    params = {
        "country": country.lower(),
        "start": start,
        "end": end
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=30)
            
            if response.status_code == 429:
                wait_time = (attempt + 1) * 5
                print(f"  ⏳ 429 限流，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if "429" in str(e) and attempt < MAX_RETRIES - 1:
                wait_time = (attempt + 1) * 5
                print(f"  ⏳ 429 限流，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            print(f"  ❌ 请求失败: {e}")
            return None
        except Exception as e:
            print(f"  ❌ 请求失败: {e}")
            return None
    
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
    """Unix 秒转换为 Europe/Berlin 时区，格式: YYYY/M/D H:MM"""
    dt = datetime.utcfromtimestamp(unix_seconds) + timedelta(hours=1)
    # ✅ 格式改为 2024/1/1 0:00 (不补零)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"

# ============ 下载数据 ============
all_solar = {}
all_wind = {}
all_residual = {}
all_generation = []

date_ranges = generate_date_ranges(START_DATE, END_DATE)
print(f"分 {len(date_ranges)} 批下载")
print(f"实际范围: {date_ranges[0][0]} → {date_ranges[-1][1]}\n")

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
                
                if "solar" in key_lower and "forecast" not in key_lower:
                    if dt_str not in country_solar:
                        country_solar[dt_str] = 0
                    country_solar[dt_str] += value
                
                elif "wind" in key_lower and "forecast" not in key_lower:
                    if dt_str not in country_wind:
                        country_wind[dt_str] = 0
                    country_wind[dt_str] += value
                
                elif "residual" in key_lower and "load" in key_lower:
                    country_residual[dt_str] = value
                
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
                        "Date": dt_str,  # ✅ 改为 Date
                        "country": country,
                        "category": category,
                        "value_MW": value
                    })
    
    all_solar[country] = country_solar
    all_wind[country] = country_wind
    all_residual[country] = country_residual
    print(f"  ✅ 完成\n")
    
    time.sleep(2)

# ============ 处理并保存数据 ============
print("=" * 60)
print("处理数据...\n")

def format_date(dt):
    """格式化为 YYYY/M/D H:MM"""
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"

def create_wide_df(data_dict, countries):
    """创建宽格式 DataFrame"""
    all_times = set()
    for country_data in data_dict.values():
        all_times.update(country_data.keys())
    all_times = sorted(all_times)
    
    if not all_times:
        return pd.DataFrame(columns=["Date"] + countries)
    
    df = pd.DataFrame({"Date": all_times})
    for country in countries:
        df[country] = df["Date"].map(lambda x, c=country: data_dict.get(c, {}).get(x, 0))
    
    # 重采样到小时
    df["Date"] = pd.to_datetime(df["Date"], format="%Y/%m/%d %H:%M")
    df = df.set_index("Date").resample("h").mean().reset_index()
    
    # ✅ 格式化为 2024/1/1 0:00
    df["Date"] = df["Date"].apply(format_date)
    
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

# 3. Residual Load
print("📊 residual_load.csv...")
residual_df = create_wide_df(all_residual, COUNTRIES)
residual_df.to_csv(os.path.join(DATA_DIR, "residual_load.csv"), index=False)
print(f"  ✅ {len(residual_df)} 行")

# 4. Generation
print("📊 generation.csv...")
if all_generation:
    gen_df = pd.DataFrame(all_generation)
    gen_df = gen_df.groupby(["Date", "country", "category"])["value_MW"].sum().reset_index()
    gen_df["Date"] = pd.to_datetime(gen_df["Date"], format="%Y/%m/%d %H:%M")
    gen_df = gen_df.groupby([pd.Grouper(key="Date", freq="h"), "country", "category"])["value_MW"].mean().reset_index()
    
    # ✅ 格式化为 2024/1/1 0:00
    gen_df["Date"] = gen_df["Date"].apply(format_date)
    
    gen_df.to_csv(os.path.join(DATA_DIR, "generation.csv"), index=False)
    print(f"  ✅ {len(gen_df)} 行")
else:
    print("  ⚠️ 无数据")

print("\n" + "=" * 60)
print("🎉 全部完成!")
print(f"文件: {DATA_DIR}/solar.csv, wind.csv, residual_load.csv, generation.csv")
