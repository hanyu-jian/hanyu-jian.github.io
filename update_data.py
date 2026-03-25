import os
import time
import pandas as pd
import requests
from datetime import datetime

# ================================
# 配置
# ================================
SAVE_PATH = "data"
START_DATE = "2024-01-01"
END_DATE = "2024-01-03"  # 测试用短日期

ORDER = ["DE", "FR", "ES", "IT", "GR", "RO", "HU", "AT", "PL", "SK", "RS", "HR", "BG"]

os.makedirs(SAVE_PATH, exist_ok=True)

print("=" * 60)
print(f"下载数据: {START_DATE} to {END_DATE}")
print("=" * 60)

# ================================
# 1. API 下载函数
# ================================
def download_from_api(country, start_date, end_date):
    """从 Energy-Charts API 下载数据"""
    url = "https://api.energy-charts.info/public_power"
    params = {
        "country": country.lower(),
        "start": start_date,
        "end": end_date
    }
    
    response = requests.get(url, params=params, timeout=60)
    if response.status_code != 200:
        raise Exception(f"API error: {response.status_code}")
    
    data = response.json()
    
    if "unix_seconds" not in data:
        raise Exception("No data returned")
    
    # 构建 DataFrame
    timestamps = data["unix_seconds"]
    df = pd.DataFrame({"unix_seconds": timestamps})
    
    for pt in data.get("production_types", []):
        name = pt.get("name", "")
        values = pt.get("data", [])
        if name and values:
            df[name] = values
    
    return df


def download_price_from_api(country, start_date, end_date):
    """从 Energy-Charts API 下载电价数据"""
    url = "https://api.energy-charts.info/price"
    params = {
        "country": country.lower(),
        "start": start_date,
        "end": end_date
    }
    
    response = requests.get(url, params=params, timeout=60)
    if response.status_code != 200:
        return None
    
    data = response.json()
    
    if "unix_seconds" not in data:
        return None
    
    timestamps = data["unix_seconds"]
    price = data.get("price", [])
    
    df = pd.DataFrame({
        "unix_seconds": timestamps,
        "price": price
    })
    
    return df


# ================================
# 2. 15min to hourly (沿用原逻辑)
# ================================
def convert_to_hourly(df):
    """15分钟数据转小时平均"""
    # 转换时间戳
    df["datetime"] = pd.to_datetime(df["unix_seconds"], unit="s", utc=True)
    df = df.set_index("datetime")
    df = df.drop(columns=["unix_seconds"], errors="ignore")
    
    # 转数值
    df = df.apply(pd.to_numeric, errors="coerce")
    
    # 小时平均
    df_hourly = df.resample("h").mean()
    
    return df_hourly


# ================================
# 3. 辅助函数：匹配列名
# ================================
def col_match(cols, keywords):
    """匹配包含关键词的列"""
    return [c for c in cols if any(k in c.lower() for k in keywords)]


# ================================
# 4. 下载所有国家数据
# ================================
print("\n下载所有国家数据...")

dfs = {}        # 各国 hourly DataFrame
price_dfs = {}  # 各国价格 DataFrame

for country in ORDER:
    print(f"\n{country}...", end=" ")
    
    try:
        # 下载发电数据
        df_raw = download_from_api(country, START_DATE, END_DATE)
        df_hourly = convert_to_hourly(df_raw)
        dfs[country] = df_hourly
        print(f"{len(df_hourly)} 小时", end=" ")
        
        # 下载价格数据
        df_price = download_price_from_api(country, START_DATE, END_DATE)
        if df_price is not None:
            df_price_hourly = convert_to_hourly(df_price)
            price_dfs[country] = df_price_hourly
            print(f"+ 价格", end="")
        
        print(" ✓")
        
    except Exception as e:
        print(f"错误: {e}")
    
    time.sleep(1.5)  # API 限速


# ================================
# 5. 构建 Solar / Wind / Load / Residual Load
# ================================
print("\n" + "=" * 60)
print("构建汇总表...")

solar_df = pd.DataFrame()
wind_df = pd.DataFrame()
load_df = pd.DataFrame()
residual_df = pd.DataFrame()
da_df = pd.DataFrame()

for country in ORDER:
    if country not in dfs:
        continue
    
    df = dfs[country]
    
    # -------- Solar -----------
    solar_cols = col_match(df.columns, ["solar"])
    if solar_cols:
        series = df[solar_cols].sum(axis=1).rename(country)
        solar_df = series.to_frame() if solar_df.empty else solar_df.join(series, how="outer")
    
    # -------- Wind -----------
    wind_cols = col_match(df.columns, ["wind"])
    if wind_cols:
        series = df[wind_cols].sum(axis=1).rename(country)
        wind_df = series.to_frame() if wind_df.empty else wind_df.join(series, how="outer")
    
    # -------- Load -----------
    load_cols = col_match(df.columns, ["load"])
    # 排除 residual load
    load_cols = [c for c in load_cols if "residual" not in c.lower()]
    if load_cols:
        series = df[load_cols].sum(axis=1).rename(country)
        load_df = series.to_frame() if load_df.empty else load_df.join(series, how="outer")
    
    # -------- Residual Load -----------
    residual_cols = col_match(df.columns, ["residual"])
    if residual_cols:
        series = df[residual_cols].sum(axis=1).rename(country)
        residual_df = series.to_frame() if residual_df.empty else residual_df.join(series, how="outer")
    
    # -------- DA Price -----------
    if country in price_dfs:
        price_series = price_dfs[country]["price"].rename(country)
        da_df = price_series.to_frame() if da_df.empty else da_df.join(price_series, how="outer")


# 补全缺失国家列
for df_x in [solar_df, wind_df, load_df, residual_df, da_df]:
    for c in ORDER:
        if c not in df_x.columns:
            df_x[c] = pd.NA

# 按 ORDER 排列列
if not solar_df.empty:
    solar_df = solar_df[ORDER]
if not wind_df.empty:
    wind_df = wind_df[ORDER]
if not load_df.empty:
    load_df = load_df[ORDER]
if not residual_df.empty:
    residual_df = residual_df[ORDER]
if not da_df.empty:
    da_df = da_df[ORDER]


# ================================
# 6. 格式化日期列 (YYYY/M/D H:00)
# ================================
def format_index_to_date_col(df):
    """将 datetime index 转为 Date 列"""
    if df.empty:
        return df
    
    df = df.copy()
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    
    # 格式化为 YYYY/M/D H:00
    dates = [f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:00" for dt in df.index]
    df.insert(0, "Date", dates)
    df = df.reset_index(drop=True)
    
    return df


solar_out = format_index_to_date_col(solar_df)
wind_out = format_index_to_date_col(wind_df)
load_out = format_index_to_date_col(load_df)
residual_out = format_index_to_date_col(residual_df)
da_out = format_index_to_date_col(da_df)


# ================================
# 7. Generation (长格式)
# ================================
print("\n构建 Generation (长格式)...")

GENERATION_CATEGORIES = {
    "Nuclear": ["nuclear"],
    "Fossil Coal": ["lignite", "hard coal", "coal"],
    "Fossil Gas": ["fossil gas", "natural gas"],
    "Fossil Oil": ["fossil oil", "oil"],
    "Hydro": ["hydro"],
    "Biomass": ["biomass"],
    "Other": ["geothermal", "waste", "other"],
}

all_generation = []

for country in ORDER:
    if country not in dfs:
        continue
    
    df = dfs[country]
    
    for cat, keywords in GENERATION_CATEGORIES.items():
        matched_cols = col_match(df.columns, keywords)
        if matched_cols:
            series = df[matched_cols].sum(axis=1)
            
            for dt, val in series.items():
                if pd.notna(val) and val != 0:
                    # 去除时区
                    dt_naive = dt.tz_localize(None) if dt.tzinfo else dt
                    date_str = f"{dt_naive.year}/{dt_naive.month}/{dt_naive.day} {dt_naive.hour}:00"
                    
                    all_generation.append({
                        "Date": date_str,
                        "country": country,
                        "category": cat,
                        "value_MW": val
                    })

if all_generation:
    gen_df = pd.DataFrame(all_generation)
    # 合并同一时间、国家、类别
    gen_df = gen_df.groupby(["Date", "country", "category"])["value_MW"].sum().reset_index()
else:
    gen_df = pd.DataFrame(columns=["Date", "country", "category", "value_MW"])


# ================================
# 8. 输出预览
# ================================
print("\n" + "=" * 60)
print("数据预览:")

print(f"\n=== Solar ({len(solar_out)} 行) ===")
print(solar_out.head())

print(f"\n=== Wind ({len(wind_out)} 行) ===")
print(wind_out.head())

print(f"\n=== Load ({len(load_out)} 行) ===")
print(load_out.head())

print(f"\n=== Residual Load ({len(residual_out)} 行) ===")
print(residual_out.head())

print(f"\n=== DA Price ({len(da_out)} 行) ===")
print(da_out.head())

print(f"\n=== Generation ({len(gen_df)} 行) ===")
print(gen_df.head(15))


# ================================
# 9. 保存 CSV
# ================================
print("\n" + "=" * 60)
print("保存 CSV 文件...")

solar_out.to_csv(os.path.join(SAVE_PATH, "solar.csv"), index=False)
print("  solar.csv")

wind_out.to_csv(os.path.join(SAVE_PATH, "wind.csv"), index=False)
print("  wind.csv")

load_out.to_csv(os.path.join(SAVE_PATH, "load.csv"), index=False)
print("  load.csv")

residual_out.to_csv(os.path.join(SAVE_PATH, "residual_load.csv"), index=False)
print("  residual_load.csv")

da_out.to_csv(os.path.join(SAVE_PATH, "price.csv"), index=False)
print("  price.csv")

gen_df.to_csv(os.path.join(SAVE_PATH, "generation.csv"), index=False)
print("  generation.csv")

print("\n" + "=" * 60)
print("✔ 完成!")
print("=" * 60)
