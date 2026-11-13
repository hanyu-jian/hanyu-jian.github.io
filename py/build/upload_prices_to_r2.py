#!/usr/bin/env python3
"""
把 build_hu_combined.py 生成的整合表（HU_<年>.csv）里 /ops/imbalance 页面需要的三列传到 Cloudflare R2，Worker 直接读，不用 GitHub token：
    <prefix>/prices_HU_<年>.csv     默认 prefix = market（桶 = R2_BUCKET，和超短期同一个桶）
列和列名原样取自整合表（Worker 按列名取，读 R2 和读 hu-market-data 用的是同一套解析）：
    Date                                 布达佩斯当地时间 YYYY-MM-DD HH:MM，每行 15 分钟（夏令时回拨日 02:00~02:45 出现两遍）
    DA Price (EUR/MWh)                   日前价
    ID3 Price (EUR/MWh)                  ID3 指数价
    Imbalance Price (EUR/MWh)            不平衡价（单一价格）
比整合表（16 列、几 MB）小得多，Worker 解析更省 CPU。上传是覆盖写，幂等。

环境变量：R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET；R2_PRICE_PREFIX（可选，默认 market）。
用法：
    python py/build/upload_prices_to_r2.py --src build/combined
    python py/build/upload_prices_to_r2.py --src build/combined --dry-run     # 只列出会传什么
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path

import pandas as pd

COLUMNS = ["Date", "DA Price (EUR/MWh)", "ID3 Price (EUR/MWh)", "Imbalance Price (EUR/MWh)"]


def slim(csv_path):
    """整合表 -> 只留 COLUMNS，返回 CSV 文本；缺列直接报错（宁可让 workflow 红掉，也不要悄悄传一张缺列的表）。"""
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"{csv_path}: 缺少列 {missing}（有：{list(df.columns)}）")
    buf = io.StringIO()
    df[COLUMNS].to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue()


def make_s3():
    import boto3
    from botocore.config import Config
    need = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [k for k in need if not os.environ.get(k, "").strip()]
    if missing:
        raise SystemExit("请设置环境变量 " + ", ".join(missing) + "（在网站仓库 Settings → Secrets and variables → Actions 里添加）")
    return boto3.client(
        "s3", region_name="auto",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, signature_version="s3v4"),
    )


def main():
    ap = argparse.ArgumentParser(description="把整合表里 /ops/imbalance 需要的价格列传到 R2")
    ap.add_argument("--src", default="build/combined", help="build_hu_combined.py 的输出目录（默认 %(default)s）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会传什么，不写 R2")
    a = ap.parse_args()
    prefix = os.environ.get("R2_PRICE_PREFIX", "market").strip().strip("/") or "market"
    files = sorted(Path(a.src).glob("HU_*.csv"))
    if not files:
        raise SystemExit(f"{a.src} 里没有 HU_*.csv，先运行 py/build/build_hu_combined.py --out {a.src}")
    s3 = None if a.dry_run else make_s3()
    for f in files:
        year = re.fullmatch(r"HU_(\d{4})\.csv", f.name)
        if not year:
            continue
        body = slim(f)
        key = f"{prefix}/prices_HU_{year.group(1)}.csv"
        rows = body.count("\n") - 1
        if a.dry_run:
            print(f"[dry-run] {f} -> {key}: {rows} 行，{len(body.encode()):,} 字节")
            continue
        s3.put_object(Bucket=os.environ["R2_BUCKET"].strip(), Key=key, Body=body.encode("utf-8"), ContentType="text/csv; charset=utf-8")
        print(f"{f} -> {key}: {rows} 行，{len(body.encode()):,} 字节")


if __name__ == "__main__":
    sys.exit(main())
