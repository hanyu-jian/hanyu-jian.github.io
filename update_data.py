import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
import time
import os

# 配置
DATA_DIR = "data"
START_DATE = "2024-01-01"
COUNTRIES = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]
DELAY = 0.5  # API 请求之间的延迟（秒）

# 确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)

def get_date_range(start_date_str):
    """生成从开始日期到今天的日期范围（90天为一块）"""
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.now()
    
    date_ranges = []
    current_start = start_date
    
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=90), end_date)
        date_ranges.append((
            current_start.strftime("%Y-%m-%d"),
            current_end.strftime("%Y-%m-%d")
        ))
        current_start = current_end + timedelta(days=1)
    
    return date_ranges

def fetch_data(country, data_type, start, end):
    """从 Energy Charts API 获取数据"""
    url = "https://api.energy-charts.info/public_power"
    params = {
        "country": country.lower(),
        "start": start,
        "end": end
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data_type == "load":
            # 提取 load 数据
            load_data = next((item for item in data.get("production_types", []) 
                            if item.get("name") == "Load"), None)
            if load_data and "data" in load_data:
                return load_data["data"]
        
        elif data_type == "solar":
            # 提取 Solar 数据
            solar_data = next((item for item in data.get("production_types", []) 
                             if item.get("name") == "Solar"), None)
            if solar_data and "data" in solar_data:
                return solar_data["data"]
        
        elif data_type == "wind":
            # 提取 Wind 数据（包括 onshore 和 offshore）
            wind_onshore = next((item for item in data.get("production_types", []) 
                               if item.get("name") == "Wind onshore"), None)
            wind_offshore = next((item for item in data.get("production_types", []) 
                                if item.get("name") == "Wind offshore"), None)
            
            # 合并 onshore 和 offshore 数据
            if wind_onshore and "data" in wind_onshore:
                onshore_data = wind_onshore["data"]
                offshore_data = wind_offshore.get("data", []) if wind_offshore else []
                
                # 如果有 offshore 数据，需要合并
                if offshore_data:
                    # 假设时间戳对齐，直接相加
                    combined = [onshore_data[i] + (offshore_data[i] if i < len(offshore_data) else 0) 
                              for i in range(len(onshore_data))]
                    return combined
                else:
                    return onshore_data
        
        return None
        
    except Exception as e:
        print(f"Error fetching {data_type} data for {country} ({start} to {end}): {e}")
        return None

def process_generation_data(data_type):
    """处理发电数据（Solar 或 Wind）"""
    print(f"\n{'='*50}")
    print(f"Processing {data_type.upper()} data...")
    print(f"{'='*50}")
    
    date_ranges = get_date_range(START_DATE)
    all_data = {country: [] for country in COUNTRIES}
    
    for start, end in date_ranges:
        print(f"\nFetching data from {start} to {end}...")
        
        for country in COUNTRIES:
            print(f"  - {country}...", end=" ", flush=True)
            data = fetch_data(country, data_type, start, end)
            
            if data:
                all_data[country].extend(data)
                print(f"✓ ({len(data)} records)")
            else:
                print("✗ (no data)")
            
            time.sleep(DELAY)
    
    # 获取时间戳（使用第一个有数据的国家）
    timestamps = None
    for country in COUNTRIES:
        if all_data[country]:
            timestamps_unix = list(range(len(all_data[country])))
            # 创建从2024-01-01开始的小时时间序列
            base_time = datetime(2024, 1, 1, 0, 0, 0)
            timestamps = [base_time + timedelta(hours=i) for i in timestamps_unix]
            break
    
    if timestamps is None:
        print(f"No {data_type} data available!")
        return
    
    # 创建 DataFrame
    df_dict = {"Date": timestamps}
    for country in COUNTRIES:
        if all_data[country]:
            # 确保数据长度一致
            if len(all_data[country]) == len(timestamps):
                df_dict[country] = all_data[country]
            else:
                # 填充缺失值
                df_dict[country] = all_data[country] + [0] * (len(timestamps) - len(all_data[country]))
        else:
            df_dict[country] = [0] * len(timestamps)
    
    df = pd.DataFrame(df_dict)
    
    # 转换为欧洲时区
    berlin_tz = pytz.timezone('Europe/Berlin')
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize('UTC').dt.tz_convert(berlin_tz)
    df['Date'] = df['Date'].dt.strftime('%Y/%m/%d %H:%M')
    
    # 保存为 CSV
    output_file = os.path.join(DATA_DIR, f"{data_type}.csv")
    df.to_csv(output_file, index=False)
    print(f"\n✓ {data_type.upper()} data saved to {output_file}")
    print(f"  Total records: {len(df)}")
    print(f"  Date range: {df['Date'].iloc[0]} to {df['Date'].iloc[-1]}")

def process_load_data():
    """处理负荷数据（保持原有逻辑）"""
    print(f"\n{'='*50}")
    print(f"Processing LOAD data...")
    print(f"{'='*50}")
    
    date_ranges = get_date_range(START_DATE)
    all_data = {country: [] for country in COUNTRIES}
    
    for start, end in date_ranges:
        print(f"\nFetching data from {start} to {end}...")
        
        for country in COUNTRIES:
            print(f"  - {country}...", end=" ", flush=True)
            data = fetch_data(country, "load", start, end)
            
            if data:
                all_data[country].extend(data)
                print(f"✓ ({len(data)} records)")
            else:
                print("✗ (no data)")
            
            time.sleep(DELAY)
    
    # 获取时间戳
    timestamps = None
    for country in COUNTRIES:
        if all_data[country]:
            base_time = datetime(2024, 1, 1, 0, 0, 0)
            timestamps = [base_time + timedelta(hours=i) for i in range(len(all_data[country]))]
            break
    
    if timestamps is None:
        print("No load data available!")
        return
    
    # 创建 DataFrame
    df_dict = {"Date": timestamps}
    for country in COUNTRIES:
        if all_data[country]:
            if len(all_data[country]) == len(timestamps):
                df_dict[country] = all_data[country]
            else:
                df_dict[country] = all_data[country] + [0] * (len(timestamps) - len(all_data[country]))
        else:
            df_dict[country] = [0] * len(timestamps)
    
    df = pd.DataFrame(df_dict)
    
    # 转换为欧洲时区并重采样为小时平均值
    berlin_tz = pytz.timezone('Europe/Berlin')
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize('UTC').dt.tz_convert(berlin_tz)
    df.set_index('Date', inplace=True)
    df_hourly = df.resample('H').mean().round(0)
    df_hourly.reset_index(inplace=True)
    df_hourly['Date'] = df_hourly['Date'].dt.strftime('%Y/%m/%d %H:%M')
    
    # 保存为 CSV
    output_file = os.path.join(DATA_DIR, "load.csv")
    df_hourly.to_csv(output_file, index=False)
    print(f"\n✓ LOAD data saved to {output_file}")
    print(f"  Total records: {len(df_hourly)}")
    print(f"  Date range: {df_hourly['Date'].iloc[0]} to {df_hourly['Date'].iloc[-1]}")

def process_price_data():
    """处理价格数据（保持原有逻辑）"""
    print(f"\n{'='*50}")
    print(f"Processing PRICE data...")
    print(f"{'='*50}")
    
    url = "https://api.energy-charts.info/price"
    all_prices = {country: [] for country in COUNTRIES}
    
    date_ranges = get_date_range(START_DATE)
    
    for start, end in date_ranges:
        print(f"\nFetching prices from {start} to {end}...")
        
        for country in COUNTRIES:
            print(f"  - {country}...", end=" ", flush=True)
            
            params = {
                "bzn": country,
                "start": start,
                "end": end
            }
            
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                if "price" in data and data["price"]:
                    all_prices[country].extend(data["price"])
                    print(f"✓ ({len(data['price'])} records)")
                else:
                    print("✗ (no data)")
                    
            except Exception as e:
                print(f"✗ ({e})")
            
            time.sleep(DELAY)
    
    # 获取时间戳
    timestamps_unix = None
    for country in COUNTRIES:
        if all_prices[country]:
            response = requests.get(url, params={
                "bzn": country,
                "start": START_DATE,
                "end": datetime.now().strftime("%Y-%m-%d")
            })
            data = response.json()
            if "unix_seconds" in data:
                timestamps_unix = data["unix_seconds"]
                break
    
    if timestamps_unix is None:
        print("No price data available!")
        return
    
    timestamps = [datetime.fromtimestamp(ts, tz=pytz.UTC) for ts in timestamps_unix]
    
    # 创建 DataFrame
    df_dict = {"Date": timestamps}
    for country in COUNTRIES:
        if all_prices[country]:
            if len(all_prices[country]) == len(timestamps):
                df_dict[country] = all_prices[country]
            else:
                df_dict[country] = all_prices[country] + [None] * (len(timestamps) - len(all_prices[country]))
        else:
            df_dict[country] = [None] * len(timestamps)
    
    df = pd.DataFrame(df_dict)
    
    # 转换为欧洲时区
    berlin_tz = pytz.timezone('Europe/Berlin')
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_convert(berlin_tz)
    df['Date'] = df['Date'].dt.strftime('%Y/%m/%d %H:%M')
    
    # 保存为 CSV
    output_file = os.path.join(DATA_DIR, "price.csv")
    df.to_csv(output_file, index=False)
    print(f"\n✓ PRICE data saved to {output_file}")
    print(f"  Total records: {len(df)}")
    print(f"  Date range: {df['Date'].iloc[0]} to {df['Date'].iloc[-1]}")

if __name__ == "__main__":
    print(f"Starting data update at {datetime.now()}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Start date: {START_DATE}")
    print(f"Countries: {', '.join(COUNTRIES)}")
    
    # 处理所有数据类型
    process_load_data()
    process_price_data()
    process_generation_data("solar")
    process_generation_data("wind")
    
    print(f"\n{'='*50}")
    print(f"All data updates completed at {datetime.now()}")
    print(f"{'='*50}")
