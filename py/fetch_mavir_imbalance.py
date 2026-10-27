"""Download MAVIR preliminary imbalance price files (Pos_ImbalancePrice_ENG_preliminary_<data>_<publish>.xlsx).

Raw xlsx files go to data/mavir/imbalance/. A combined CSV with every column
(data/mavir/imbalance/all.csv) is rebuilt from them.

The publish date in the file name is unknown in advance (e.g. 20261018 -> 20261022),
so each missing data date is probed with publish dates data+1 .. data+MAX_LAG.
Known full URLs (with UUID) can be added to data/mavir/imbalance/known_urls.txt.
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://mavir.hu/documents/187408084/358398864/"
NAME = "Pos_ImbalancePrice_ENG_preliminary_{d}_{p}.xlsx"
OUT = Path(__file__).resolve().parent.parent / "data" / "mavir" / "imbalance"
MAX_LAG = 14
SETTLE_DAYS = 3  # newest data dates are probed only once they are at least this old
XLSX_MAGIC = b"PK"


def get(url):
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        print(f"  request error: {e}")
        return None
    if r.status_code == 200 and r.content[:2] == XLSX_MAGIC:
        return r.content
    return None


def known_urls():
    f = OUT / "known_urls.txt"
    urls = {}
    if f.exists():
        for line in f.read_text().split():
            m = re.search(r"preliminary_(\d{8})_(\d{8})\.xlsx", line)
            if m:
                urls[m.group(1)] = (m.group(2), line)
    return urls


def existing():
    return {re.search(r"_(\d{8})_\d{8}", p.name).group(1) for p in OUT.glob("Pos_ImbalancePrice_*.xlsx")}


def fetch_day(d, known):
    ds = d.strftime("%Y%m%d")
    if ds in known:
        pub, url = known[ds]
        content = get(url)
        if content:
            return NAME.format(d=ds, p=pub), content
    for lag in range(1, MAX_LAG + 1):
        pub = (d + timedelta(days=lag)).strftime("%Y%m%d")
        name = NAME.format(d=ds, p=pub)
        content = get(BASE + name)
        if content:
            return name, content
    return None


def rebuild_csv():
    frames = []
    for p in sorted(OUT.glob("Pos_ImbalancePrice_*.xlsx")):
        df = pd.read_excel(p, header=6)
        df = df.dropna(subset=["Period"]).copy()
        df.insert(0, "source_file", p.name)
        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
        df["Date"] = df["Date"].astype(str).str.strip()
        frames.append(df)
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(OUT / "all.csv", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="first data date YYYY-MM-DD (default: 30 days ago)")
    ap.add_argument("--end", help="last data date inclusive (default: today - SETTLE_DAYS)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    start = date.fromisoformat(a.start) if a.start else today - timedelta(days=30)
    end = date.fromisoformat(a.end) if a.end else today - timedelta(days=SETTLE_DAYS)
    have, known = existing(), known_urls()
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        if ds not in have:
            res = fetch_day(d, known)
            if res:
                (OUT / res[0]).write_bytes(res[1])
                print(f"saved {res[0]}")
            else:
                print(f"not available: {ds}")
        d += timedelta(days=1)
    rebuild_csv()


if __name__ == "__main__":
    sys.exit(main())
