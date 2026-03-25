import requests
import pandas as pd
import time
import os
from datetime import datetime, timedelta

# 尝试导入时区库
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# ============================================================
# 配置
# ============================================================

DATA_DIR = "data"
LOAD_FILE = os.path.join(DATA_DIR, "load.csv")
PRICE_FILE = os.path.join(DATA_DIR, "price.csv")

# 数据源时区（欧洲能源数据以柏林时间为准）
DATA_TIMEZONE = ZoneInfo("Europe/Berlin")

# 国家列表（统一顺序）
COLUMNS = ['DE', 'FR', 'ES', 'IT', 'GR', 'RO', 'HU', 'AT', 'PL', 'SK', 'RS', 'HR', 'BG']

# 负荷数据：国家代码映射
LOAD_COUNTRY_MAP = {
    'DE': 'de',
    'FR': 'fr',
    'ES': 'es',
    'IT': 'it',
    'GR': 'gr',
    'RO': 'ro',
    'HU': 'hu',
    'AT': 'at',
    'PL': 'pl',
    'SK': 'sk',
    'RS': 'rs',
    'HR': 'hr',
    'BG': 'bg',
}

# 价格数据：竞价区映射
PRICE_BZN_MAP = {
    'DE': 'DE-LU',
    'FR': 'FR',
    'ES': 'ES',
    'IT': 'IT-North',
    'GR': 'GR',
    'RO': 'RO',
    'HU': 'HU',
    'AT': 'AT',
    'PL': 'PL',
    'SK': 'SK',
    'RS': 'RS',
    'HR': 'HR',
    'BG': 'BG',
}

# 请求配置
CHUNK_DAYS = 90
REQUEST_DELAY = 0.5
START_DATE_DEFAULT = "2024-01-01"

# ============================================================
# 工具函数
# ============================================================

def get_current_berlin_time():
    """获取当前柏林时间（无时区信息的 datetime）"""
    now_utc = datetime.now(ZoneInfo("UTC"))
    now_berlin = now_utc.astimezone(DATA_TIMEZONE)
    return now_berlin.replace(tzinfo=None)


def format_timestamp(dt):
    """datetime → 'YYYY/M/D H:MM' 格式（无前导零）"""
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}"


def load_existing_csv(filepath):
    """读取已有 CSV，返回 DataFrame，索引为 datetime"""
    if os.path.exists(filepath):
        df = pd.read_csv(filepath, index_col=0)
        df.index = pd.to_datetime(df.index, format='%Y/%m/%d %H:%M')
        print(f"  已加载 {filepath}，共 {len(df)} 行")
        return df
    print(f"  文件不存在：{filepath}，将创建新文件")
    return pd.DataFrame()


def save_csv(df, filepath):
    """保存 DataFrame 为 CSV，时间戳用指定格式"""
    df = df.sort_index()
    df.index = df.index.map(format_timestamp)
    df.to_csv(filepath, index=True, index_label='Date')
    print(f"  已保存 {filepath}，共 {len(df)} 行")


def get_date_chunks(start_date, end_date, chunk_days=90):
    """将日期范围拆分为若干块"""
    chunks = []
    current = start_date
    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_days), end_date)
        chunks.append((current, chunk_end))
        current = chunk_end
    return chunks


def get_start_date(existing_df):
    """根据已有数据确定起始日期"""
    if existing_df.empty:
        return datetime.strptime(START_DATE_DEFAULT, "%Y-%m-%d")
    last_date = existing_df.index.max()
    # 从最后一天的 00:00 重新拉取，确保数据完整
    return last_date.replace(hour=0, minute=0, second=0, microsecond=0)


def resample_to_hourly(series):
    """将 15 分钟数据重采样为小时平均"""
    if series.empty:
        return series
    series = series.dropna()
    series = series.resample('h').mean()
    return series

# ============================================================
# 负荷数据获取
# ============================================================

def fetch_load_one_country(country_code, start_dt, end_dt):
    """
    获取单个国家的负荷数据
    返回 Series（小时级均值，整数 MW）
    """
    url = "https://api.energy-charts.info/public_power"
    params = {
        "country": country_code,
        "start": start_dt.strftime("%Y-%m-%d"),
        "end": end_dt.strftime("%Y-%m-%d"),
    }
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # 找到 Load 类别
        production_types = data.get("production_types", [])
        load_data = None
        for pt in production_types:
            if pt.get("name") == "Load":
                load_data = pt.get("data", [])
                break

        if load_data is None:
            print(f"未找到 Load")
            return pd.Series(dtype=float)

        unix_seconds = data.get("unix_seconds", [])
        if not unix_seconds or not load_data:
            print(f"空数据")
            return pd.Series(dtype=float)

        # 构建 Series，转换时区
        idx = pd.to_datetime(unix_seconds, unit='s', utc=True)
        idx = idx.tz_convert('Europe/Berlin').tz_localize(None)
        s = pd.Series(load_data, index=idx, dtype=float)

        # 重采样为小时均值
        s = resample_to_hourly(s)
        
        # 取整
        s = s.round(0).astype('Int64')

        return s

    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return pd.Series(dtype=float)
    except Exception as e:
        print(f"处理失败: {e}")
        return pd.Series(dtype=float)


def fetch_load_all(start_dt, end_dt):
    """获取所有国家的负荷数据，返回 DataFrame"""
    print(f"\n获取负荷数据：{start_dt.date()} → {end_dt.date()}")
    chunks = get_date_chunks(start_dt, end_dt, CHUNK_DAYS)
    all_data = []

    for chunk_start, chunk_end in chunks:
        print(f"\n  区块：{chunk_start.date()} → {chunk_end.date()}")
        chunk_dict = {}

        for col, code in LOAD_COUNTRY_MAP.items():
            print(f"    {col} ({code})... ", end="", flush=True)
            s = fetch_load_one_country(code, chunk_start, chunk_end)
            chunk_dict[col] = s
            print(f"✓ {len(s)} 条")
            time.sleep(REQUEST_DELAY)

        chunk_df = pd.DataFrame(chunk_dict)
        all_data.append(chunk_df)

    if not all_data:
        return pd.DataFrame()

    result = pd.concat(all_data)
    return result

# ============================================================
# 价格数据获取
# ============================================================

def fetch_price_one_bzn(bzn_code, start_dt, end_dt):
    """
    获取单个竞价区的价格数据
    返回 Series（小时级均值，保留 2 位小数，EUR/MWh）
    """
    url = "https://api.energy-charts.info/price"
    params = {
        "bzn": bzn_code,
        "start": start_dt.strftime("%Y-%m-%d"),
        "end": end_dt.strftime("%Y-%m-%d"),
    }
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        unix_seconds = data.get("unix_seconds", [])
        prices = data.get("price", [])

        if not unix_seconds or not prices:
            print(f"空数据")
            return pd.Series(dtype=float)

        # 构建 Series，转换时区
        idx = pd.to_datetime(unix_seconds, unit='s', utc=True)
        idx = idx.tz_convert('Europe/Berlin').tz_localize(None)
        s = pd.Series(prices, index=idx, dtype=float)

        # 重采样为小时均值
        s = resample_to_hourly(s)
        
        # 保留 2 位小数
        s = s.round(2)

        return s

    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return pd.Series(dtype=float)
    except Exception as e:
        print(f"处理失败: {e}")
        return pd.Series(dtype=float)


def fetch_price_all(start_dt, end_dt):
    """获取所有国家的价格数据，返回 DataFrame"""
    print(f"\n获取价格数据：{start_dt.date()} → {end_dt.date()}")
    chunks = get_date_chunks(start_dt, end_dt, CHUNK_DAYS)
    all_data = []

    for chunk_start, chunk_end in chunks:
        print(f"\n  区块：{chunk_start.date()} → {chunk_end.date()}")
        chunk_dict = {}

        for col, bzn in PRICE_BZN_MAP.items():
            print(f"    {col} ({bzn})... ", end="", flush=True)
            s = fetch_price_one_bzn(bzn, chunk_start, chunk_end)
            chunk_dict[col] = s
            print(f"✓ {len(s)} 条")
            time.sleep(REQUEST_DELAY)

        chunk_df = pd.DataFrame(chunk_dict)
        all_data.append(chunk_df)

    if not all_data:
        return pd.DataFrame()

    result = pd.concat(all_data)
    return result

# ============================================================
# 合并与保存
# ============================================================

def merge_and_save(existing_df, new_df, filepath):
    """合并新旧数据，去重，保存"""
    if new_df.empty:
        print(f"  无新数据，跳过保存")
        return existing_df

    # 确保列顺序一致
    new_df = new_df.reindex(columns=COLUMNS)

    if not existing_df.empty:
        existing_df = existing_df.reindex(columns=COLUMNS)
        combined = pd.concat([existing_df, new_df])
    else:
        combined = new_df

    # 去重：保留最新的数据（后出现的覆盖先出现的）
    combined = combined[~combined.index.duplicated(keep='last')]
    
    # 排序
    combined = combined.sort_index()

    save_csv(combined, filepath)
    return combined

# ============================================================
# 主函数
# ============================================================

def main():
    # 创建数据目录
    os.makedirs(DATA_DIR, exist_ok=True)

    # 获取当前柏林时间作为结束日期
    end_dt = get_current_berlin_time()

    print("=" * 60)
    print("欧洲能源数据更新工具")
    print("=" * 60)
    print(f"柏林时间：{end_dt.strftime('%Y-%m-%d %H:%M')}")
    print(f"本地时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"数据目录：{os.path.abspath(DATA_DIR)}")

    # ==================== 负荷数据 ====================
    print("\n" + "=" * 60)
    print("【负荷数据更新】")
    print("=" * 60)
    
    existing_load = load_existing_csv(LOAD_FILE)
    start_dt_load = get_start_date(existing_load)

    print(f"  起始日期：{start_dt_load.date()}")
    print(f"  结束日期：{end_dt.date()}")

    if start_dt_load.date() >= end_dt.date():
        print("  ⏭ 数据已是最新，无需更新")
    else:
        new_load = fetch_load_all(start_dt_load, end_dt)
        merge_and_save(existing_load, new_load, LOAD_FILE)

    # ==================== 价格数据 ====================
    print("\n" + "=" * 60)
    print("【价格数据更新】")
    print("=" * 60)
    
    existing_price = load_existing_csv(PRICE_FILE)
    start_dt_price = get_start_date(existing_price)

    print(f"  起始日期：{start_dt_price.date()}")
    print(f"  结束日期：{end_dt.date()}")

    if start_dt_price.date() >= end_dt.date():
        print("  ⏭ 数据已是最新，无需更新")
    else:
        new_price = fetch_price_all(start_dt_price, end_dt)
        merge_and_save(existing_price, new_price, PRICE_FILE)

    # ==================== 完成 ====================
    print("\n" + "=" * 60)
    print("✅ 数据更新完成！")
    print("=" * 60)
    
    # 显示数据统计
    if os.path.exists(LOAD_FILE):
        df_load = pd.read_csv(LOAD_FILE)
        print(f"  负荷数据：{len(df_load)} 行 × {len(df_load.columns)-1} 国家")
    if os.path.exists(PRICE_FILE):
        df_price = pd.read_csv(PRICE_FILE)
        print(f"  价格数据：{len(df_price)} 行 × {len(df_price.columns)-1} 国家")


if __name__ == "__main__":
    main()
