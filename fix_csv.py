#!/usr/bin/env python3
"""
一次性修复 data/ 目录下所有 CSV 的两个问题：
  1. 浮点精度：178.32999999999998 → 178.33
  2. 列类型退化：object 类型数字 → float64
运行方法：python fix_csv.py
"""

from pathlib import Path
import pandas as pd

DATA_DIR = Path("data")
GEN_DIR  = DATA_DIR / "generation"

WIDE_FILES = [
    DATA_DIR / "price.csv",
    DATA_DIR / "load.csv",
    DATA_DIR / "solar.csv",
    DATA_DIR / "wind.csv",
    DATA_DIR / "residual_load.csv",
]

FLOAT_FORMAT = "%.6g"   # 最多6位有效数字，自动去掉无意义尾零


def fix_wide(path: Path):
    if not path.exists():
        print(f"  [SKIP] {path.name} 不存在")
        return

    df = pd.read_csv(path, index_col=0)
    before_dtype = df.dtypes.value_counts().to_dict()

    # 强制所有列转为数值型，再 round
    df = df.apply(pd.to_numeric, errors="coerce").round(6)

    df.index.name = "Date"
    df.sort_index(inplace=True)
    df.to_csv(path, float_format=FLOAT_FORMAT)

    after_dtype = df.dtypes.value_counts().to_dict()
    print(f"  ✓ {path.name}: {len(df)} 行 × {len(df.columns)} 列  |  类型 {before_dtype} → {after_dtype}")


def fix_generation(gen_dir: Path):
    if not gen_dir.exists():
        print(f"  [SKIP] {gen_dir} 不存在")
        return

    csv_files = sorted(gen_dir.glob("*.csv"))
    if not csv_files:
        print(f"  [SKIP] {gen_dir} 里没有 CSV")
        return

    for path in csv_files:
        df = pd.read_csv(path)

        # value 列强制数值型 + round
        if "value" in df.columns:
            df["value"] = pd.to_numeric(df["value"], errors="coerce").round(6)

        df.sort_values(["date", "category"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.to_csv(path, index=False, float_format=FLOAT_FORMAT)
        print(f"  ✓ generation/{path.name}: {len(df)} 行")


def main():
    print("=" * 56)
    print("CSV 精度修复工具")
    print("=" * 56)

    print("\n[1/2] 修复宽表文件...")
    for p in WIDE_FILES:
        fix_wide(p)

    print("\n[2/2] 修复 generation/ 文件...")
    fix_generation(GEN_DIR)

    print("\n" + "=" * 56)
    print("完成！所有文件已原地修复。")
    print("=" * 56)


if __name__ == "__main__":
    main()
