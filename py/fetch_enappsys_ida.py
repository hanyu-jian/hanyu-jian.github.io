#!/usr/bin/env python3
"""
EnAppSys 下载脚本 — HUPX IDA1/IDA2/IDA3（价格 + 成交量，15min）
- 接口：https://app.enappsys.com/datadownload  code=hu/elec/hupxauctionid/idsummary/version2
- 凭证只从环境变量读取：ENAPPSYS_USER / ENAPPSYS_PASS（GitHub Secrets），不写进代码，
  日志与报错里一律屏蔽 user / pass
- 返回的列原样保留（不改名、不换算），按第一列时间合并进
  private_data/enappsys/hupx_ida_qh.csv（该目录在 .assetsignore 中，不对外发布）
- 时间：timezone=CET 请求，结果即接口返回的本地时间文本，重复时间戳（秋季回拨）以后到的为准并告警
- 模式：
    --months-back 1   滚动取最近 1 个月（默认，每日更新用）
    --months-back N   补数：滚动取最近 N 个月
    --from-file F     不联网，直接解析本地下载好的 CSV（测试/排错用）
"""

import argparse
import csv
import io
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://app.enappsys.com/datadownload"
CODE = "hu/elec/hupxauctionid/idsummary/version2"
OUT_DEFAULT = Path("private_data/enappsys/hupx_ida_qh.csv")
TIME_COL = "datetime"
MAX_RETRIES = 3
TIMEOUT = 60


def build_params(user, password, months_back):
    return {
        "code": CODE, "amountback": months_back, "amountfor": 0,
        "currency": "EUR", "delimiter": "comma", "minavmax": "false",
        "pass": password, "periodback": "monthly", "periodfor": "monthly",
        "res": "qh", "tag": "csv", "timedisplay": "rolling",
        "timezone": "CET", "user": user,
    }


def redact(text, *secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def download(user, password, months_back):
    params = build_params(user, password, months_back)
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
            if r.status_code == 200 and r.text.strip():
                return r.text
            last = f"HTTP {r.status_code}, {len(r.text)} chars"
        except requests.RequestException as exc:
            last = redact(str(exc), user, password)
        print(f"  attempt {attempt + 1} failed: {last}", file=sys.stderr)
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"EnAppSys download failed: {last}")


def _to_time(series):
    """ISO（yyyy-mm-dd）按年月日解析；其余（dd/mm/yyyy 等）按日在前解析，避免 10-01 被当成 1 月 10 日。"""
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s, format="ISO8601", errors="coerce")
    return iso.fillna(pd.to_datetime(s.where(iso.isna()), dayfirst=True, errors="coerce"))


def _looks_like_time(s):
    s = s.strip()
    if len(s) < 8 or not s[0].isdigit():
        return False
    return not pd.isna(_to_time(pd.Series([s])).iloc[0])


def parse(text):
    """返回以 TIME_COL 为首列的 DataFrame；表头 = 第一个下一行以时间开头的行（兜底第 2 行）。"""
    rows = [r for r in csv.reader(io.StringIO(text.strip())) if r]
    head = next((i for i in range(len(rows) - 1) if _looks_like_time(rows[i + 1][0])), None)
    if head is None:
        raise ValueError("no data rows found; first lines: " + redact(repr(rows[:3]), ""))
    header = [h.strip() for h in rows[head]]
    header[0] = TIME_COL
    width = len(header)
    body = [(r + [""] * width)[:width] for r in rows[head + 1:]]
    df = pd.DataFrame(body, columns=header).replace("", pd.NA)
    t = _to_time(df[TIME_COL])
    df = df[t.notna()].copy()
    df[TIME_COL] = t[t.notna()].dt.strftime("%Y-%m-%d %H:%M")
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)


def merge(old, new):
    if old is None or old.empty:
        out = new
    else:
        missing = [c for c in new.columns if c not in old.columns]
        for c in missing:
            old[c] = pd.NA
        out = pd.concat([old[new.columns.tolist() + [c for c in old.columns if c not in new.columns]], new])
    dup = out[TIME_COL].duplicated(keep="last")
    out = out[~dup].sort_values(TIME_COL).reset_index(drop=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months-back", type=int, default=1)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--from-file", type=Path)
    a = ap.parse_args(argv)

    if a.from_file:
        text = a.from_file.read_text(encoding="utf-8-sig")
    else:
        user, password = os.environ.get("ENAPPSYS_USER"), os.environ.get("ENAPPSYS_PASS")
        if not user or not password:
            sys.exit("ENAPPSYS_USER / ENAPPSYS_PASS not set")
        text = download(user, password, a.months_back)

    new = parse(text)
    if new.empty:
        sys.exit("downloaded file has no data rows")
    if new[TIME_COL].duplicated().any():
        print("WARNING: duplicate timestamps in download (DST fall-back?); keeping the last",
              file=sys.stderr)
    old = pd.read_csv(a.out, dtype={TIME_COL: str}) if a.out.exists() else None
    out = merge(old, new)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    print(f"downloaded {len(new)} rows [{new[TIME_COL].iloc[0]} .. {new[TIME_COL].iloc[-1]}]; "
          f"file now {len(out)} rows, columns: {list(out.columns)}")


if __name__ == "__main__":
    main()
