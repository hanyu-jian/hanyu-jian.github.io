import requests
import json

# 查看 API 实际返回结构
url = "https://api.energy-charts.info/public_power"
params = {"country": "de", "start": "2024-01-01", "end": "2024-01-02"}

response = requests.get(url, params=params, timeout=30)
data = response.json()

print("=" * 60)
print("API 返回结构:")
print("=" * 60)

print(f"\n顶层 keys: {list(data.keys())}")

if "production_types" in data:
    print(f"\nproduction_types 数量: {len(data['production_types'])}")
    print("\n前3个 production_types 详情:")
    for i, pt in enumerate(data["production_types"][:3]):
        print(f"\n--- [{i}] ---")
        print(f"  name: {pt.get('name')}")
        print(f"  data 长度: {len(pt.get('data', []))}")
        if pt.get('data'):
            print(f"  data 前5个值: {pt['data'][:5]}")

print("\n" + "=" * 60)
print("所有 production_type 名称:")
print("=" * 60)
for pt in data.get("production_types", []):
    name = pt.get("name", "?")
    vals = pt.get("data", [])
    non_null = sum(1 for v in vals if v is not None)
    print(f"  {name}: {len(vals)} 条, {non_null} 非空")

print("\n" + "=" * 60)
print("unix_seconds 示例:")
print("=" * 60)
if "unix_seconds" in data:
    print(f"长度: {len(data['unix_seconds'])}")
    print(f"前5个: {data['unix_seconds'][:5]}")
