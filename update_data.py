import requests
from datetime import datetime

url = "https://api.energy-charts.info/public_power"
params = {"country": "de", "start": "2024-01-01", "end": "2024-01-02"}

response = requests.get(url, params=params, timeout=30)
data = response.json()

timestamps = data["unix_seconds"]

print("=" * 60)
print("时间戳分析")
print("=" * 60)
print(f"总条数: {len(timestamps)}")
print(f"\n前10个时间戳:")

for i, ts in enumerate(timestamps[:10]):
    # 不做任何时区转换，直接用 UTC
    dt_utc = datetime.utcfromtimestamp(ts)
    print(f"  {i}: unix={ts} -> UTC: {dt_utc.strftime('%Y-%m-%d %H:%M')}")

print(f"\n最后5个时间戳:")
for i, ts in enumerate(timestamps[-5:]):
    dt_utc = datetime.utcfromtimestamp(ts)
    print(f"  {len(timestamps)-5+i}: unix={ts} -> UTC: {dt_utc.strftime('%Y-%m-%d %H:%M')}")

# 检查时间间隔
print(f"\n时间间隔:")
intervals = set()
for i in range(1, min(20, len(timestamps))):
    diff = timestamps[i] - timestamps[i-1]
    intervals.add(diff)
    print(f"  {i-1}->{i}: {diff} 秒 = {diff//60} 分钟")

print(f"\n所有不同间隔: {intervals}")

# 检查 Solar 数据
print("\n" + "=" * 60)
print("Solar 数据")
print("=" * 60)
for pt in data.get("production_types", []):
    if "Solar" in pt.get("name", ""):
        solar_data = pt.get("data", [])
        print(f"名称: {pt['name']}")
        print(f"数据长度: {len(solar_data)}")
        print(f"前10个值: {solar_data[:10]}")
        
        # 找第一个非零值
        for i, v in enumerate(solar_data):
            if v and v > 0:
                dt = datetime.utcfromtimestamp(timestamps[i])
                print(f"第一个非零值: index={i}, 时间={dt.strftime('%Y-%m-%d %H:%M')}, 值={v}")
                break
