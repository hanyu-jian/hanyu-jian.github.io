"""
Tokaj 五站「DA + IDC 滚动（超短期）」结算回溯：日前收入 + IDC 调整收入 − 不平衡费用 = 最终收入。

不再用日前 + 五轮日内（IDA1/2/3、ID3）这六次预测，只用两样：
    DA 预测          乙方每天 09:30 出的日前报量（vcom 仓库 data/production forecast 的 I 列，kWh）
    IDC 滚动预测     乙方超短期 POA 预测换算成发电量（R2 里 ust/view/<月>/<场站>.csv，kWh）。
                    每个 15 分钟时段 T 用「提前 L 分钟」那一期修正（默认 L = 60，即 t0 = T−60 分钟的那一期；
                    每期约在 t0 之后 26 分钟生成，赶在日内关闸（交割前约 15 分钟）之前）。
                    某个时段正好缺这一档时，用更早的一期（提前量 >= L 里最小的一档，和网站页面的规则一致）。
                    这个时段完全没有超短期预测（夜间、当期没发）→ 不调整（最终计划 = DA）。

每个 15 分钟（kWh / EUR，布达佩斯当地时间、区间起点），和工作簿的口径一致，只是把六次预测压成两段：
    H   = DA 预测 I                          日前计划
    F   = 最终计划 = 提前 L 分钟的超短期预测（没有则 = H）
    IDC = F − H                              IDC 调整量（正 = 多卖，负 = 买回）
    日前收入   Q  = P · H / 1000             P = 日前价（EUR/MWh）
    IDC 收入  AE  = AA · (F − H) / 1000      AA = ID3 指数价（EUR/MWh）
    交易收入  AF  = Q + AE
    不平衡    AI  = F − AH                   AH = 理论发电量（实测 POA 算出来的，含 AC 限值）；正 = 缺电，负 = 余电
    不平衡费用 AQ = −AI · Pimb / 1000        Pimb = 不平衡价，正负不平衡是同一个价格（单一价格），系统方向不影响总额
                                              AQ 为正是收入，为负是成本：余电（AI<0）按不平衡价卖给系统，缺电（AI>0）要买回；价格为负时反过来
    最终收入  AS  = AF + AQ
另外给一个基准：只用 DA（不做 IDC 修正）的结果 result_da_only_EUR = Q − (H − AH)·Pimb/1000，
idc_gain_EUR = AS − result_da_only_EUR 就是 IDC 滚动修正带来的收益（可正可负）。

这里的 AQ 公式 = 工作簿里 AM~AP 四列合起来的结果：用工作簿（PV_Generation_Forecast_Test_Robestec_Jan-Aug2026）
Putnok 表 22,924 个格子核对过，−AI·价格/1000 与工作簿 AQ 逐行一致、合计相同；Pneg 与 Ppos 在这些格子里没有一行不同。

数据来源（价格的读取直接复用 tokaj_imbalance_price_calculation.load_prices）：
    vcom 仓库（--plant-dir）  data/production forecast/<年>/15min/<场站>.csv 的 I 列；data/theoretical production/.../<场站>.csv 的 kWh
    超短期（二选一）          --ust-dir DIR   R2 里 ust/view/ 的本地副本（<DIR>/<YYYY-MM>/<场站>.csv）
                              --ust-r2        直接读 R2（环境变量 R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET）
    价格（本仓库 data/）       日前价 entsoe/15min/price.csv、ID3 enappsys/ID3/HU_*.csv（或 --id3 Montel 导出）、不平衡价 raw_data/*/A85.csv
超短期只从 2026-10-25（replay）/ 2026-10-29（live）才有，更早的日子没有 IDC 调整，请用 --start 避开。

输出（--out，默认 data/tokaj_settlement/）：daily.csv、monthly.csv（带 lead_min 列，同一场站同一天同一提前量重算会覆盖）；
--detail DIR 另外写出逐 15 分钟明细（不要提交）。--lead 可以给多个，一次比较不同提前量。

用法：
    python py/analysis/tokaj_da_idc_settlement.py --plant-dir ../vcom --ust-dir ./ust_view --start 2026-10-29 --end 2026-11-10
    python py/analysis/tokaj_da_idc_settlement.py --plant-dir ../vcom --ust-r2 --lead 45 60 90 --start 2026-10-29
"""

import argparse
import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tokaj_imbalance_price_calculation as T  # noqa: E402

SITES = T.SITES
DATA = T.DATA
UST_HORIZONS = list(range(15, 241, 15))          # 超短期每期 16 档提前量（分钟）
DEFAULT_LEAD = 60


# ───────────────────────── 超短期 ─────────────────────────

def dir_reader(root):
    """本地目录读取器：<root>/<YYYY-MM>/<场站>.csv（也接受 <root>/view/... 或 <root>/ust/view/...，即 R2 目录结构的副本）。"""
    root = Path(root)

    def read(month, site):
        for p in (root / month / f"{site}.csv", root / "view" / month / f"{site}.csv", root / "ust" / "view" / month / f"{site}.csv"):
            if p.exists():
                return p.read_text(encoding="utf-8-sig")
        return None
    return read


def r2_reader(prefix="ust"):
    """直接读 R2：<prefix>/view/<YYYY-MM>/<场站>.csv。凭据从环境变量读，不进代码 / 日志。"""
    import boto3
    from botocore.config import Config
    need = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [k for k in need if not os.environ.get(k, "").strip()]
    if missing:
        raise SystemExit("读 R2 需要环境变量: " + ", ".join(missing))
    s3 = boto3.client("s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com",
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
                      region_name="auto", config=Config(retries={"max_attempts": 3, "mode": "standard"}, signature_version="s3v4"))
    bucket = os.environ["R2_BUCKET"].strip()

    def read(month, site):
        try:
            return s3.get_object(Bucket=bucket, Key=f"{prefix}/view/{month}/{site}.csv")["Body"].read().decode("utf-8-sig")
        except Exception as e:                       # 不存在就当没有
            if "NoSuchKey" in repr(e) or "404" in repr(e) or "Not Found" in repr(e):
                return None
            raise
    return read


def final_plan(read, site, lead, months):
    """每个时段取「提前量 >= lead 里最小的一档」的超短期 kWh（那一档缺就退到更大的）。
    返回 DataFrame：F（kWh）、F_h（实际用到的提前量，分钟）；没有超短期的时段不出现。"""
    parts = []
    for m in months:
        text = read(m, site)
        if text:
            parts.append(pd.read_csv(io.StringIO(text), index_col=0, parse_dates=True))
    if not parts:
        return pd.DataFrame(columns=["F", "F_h"], index=pd.DatetimeIndex([], name="Date"))
    view = pd.concat(parts)
    view = view[~view.index.duplicated(keep="last")].sort_index()
    out = pd.Series(np.nan, index=view.index)
    used = pd.Series(np.nan, index=view.index)
    for h in (x for x in UST_HORIZONS if x >= lead):
        col = f"kWh h{h}"
        if col not in view:
            continue
        pick = out.isna() & pd.to_numeric(view[col], errors="coerce").notna()
        out[pick] = pd.to_numeric(view.loc[pick, col])
        used[pick] = h
    return pd.DataFrame({"F": out, "F_h": used}).dropna(subset=["F"])


# ───────────────────────── 计算 ─────────────────────────

def compute(df):
    """df：15 分钟起点（当地时间）为索引，列 I（DA 预测 kWh）、F（超短期最终计划 kWh，没有就空）、AH（理论发电量 kWh）、
    P（日前价）、AA（ID3 指数价）、Pneg（不平衡价，EUR/MWh；单一价格）。返回加上计算列的拷贝。"""
    d = df.copy()
    for c in ("I", "F", "AH", "P", "AA", "Pneg"):
        if c not in d:
            d[c] = np.nan
    f = lambda c: d[c].fillna(0)                         # 价格缺失按 0 计（和工作簿的空单元格一样），在汇总里统计覆盖
    d["has_ust"] = d["F"].notna()
    d["H"] = d["I"].fillna(0)
    d["position"] = d["F"].where(d["has_ust"], d["H"])     # 没有超短期预测 → 不调整
    d["IDC"] = d["position"] - d["H"]
    d["Q"] = f("P") * d["H"] / 1000
    d["AE"] = f("AA") * d["IDC"] / 1000
    d["AF"] = d["Q"] + d["AE"]
    d["AI"] = d["position"] - d["AH"]
    d["fee"] = -d["AI"] * f("Pneg") / 1000
    d["result"] = d["AF"] + d["fee"]
    # 基准：不做 IDC 修正，只按 DA 报
    d["result_da_only"] = d["Q"] - (d["H"] - d["AH"]) * f("Pneg") / 1000
    d["valid"] = d["AH"].notna() & d["I"].notna()
    d["uncovered_kWh"] = d["IDC"].abs() * d["AA"].isna()            # 没有 ID3 价格覆盖的 IDC 调整量
    d["daytime_no_ust"] = d["valid"] & ~d["has_ust"] & ((d["H"] > 0) | (d["AH"] > 0))   # 白天却没有超短期（覆盖缺口）
    return d


def summarize(d, lead, freq="D"):
    """逐 15 分钟 -> 汇总（freq 'D' 按天，'M' 按月）；只统计 valid 的格子。"""
    v = d[d["valid"]].copy()
    v["abs_imbalance"] = v["AI"].abs()
    v["abs_idc"] = v["IDC"].abs()
    v["Pimb_missing"] = v["Pneg"].isna()
    v["DA_missing"] = v["P"].isna()
    g = v.groupby(v.index.to_period(freq))
    out = pd.DataFrame({
        "cells": g.size(),
        "cells_with_ust": g["has_ust"].sum(),
        "cells_daytime_no_ust": g["daytime_no_ust"].sum(),
        "actual_kWh": g["AH"].sum(),
        "da_kWh": g["H"].sum(),
        "final_plan_kWh": g["position"].sum(),
        "idc_net_kWh": g["IDC"].sum(),
        "idc_abs_kWh": g["abs_idc"].sum(),
        "imbalance_net_kWh": g["AI"].sum(),
        "imbalance_abs_kWh": g["abs_imbalance"].sum(),
        "rev_da_EUR": g["Q"].sum(),
        "rev_idc_EUR": g["AE"].sum(),
        "rev_before_imbalance_EUR": g["AF"].sum(),
        "imbalance_fee_EUR": g["fee"].sum(),
        "result_EUR": g["result"].sum(),
        "result_da_only_EUR": g["result_da_only"].sum(),
        "uncovered_idc_kWh": g["uncovered_kWh"].sum(),
        "cells_no_da_price": g["DA_missing"].sum(),
        "cells_no_imbalance_price": g["Pimb_missing"].sum(),
    })
    out.insert(0, "lead_min", lead)
    return add_ratios(out)


def add_ratios(out):
    o = out.copy()
    a = o["actual_kWh"].replace(0, np.nan)
    o["idc_gain_EUR"] = o["result_EUR"] - o["result_da_only_EUR"]
    o["imbalance_abs_pct_of_actual"] = o["imbalance_abs_kWh"] / a * 100
    o["result_EUR_per_MWh"] = o["result_EUR"] / a * 1000
    o["result_da_only_EUR_per_MWh"] = o["result_da_only_EUR"] / a * 1000
    return o


RATIO_COLS = ("idc_gain_EUR", "imbalance_abs_pct_of_actual", "result_EUR_per_MWh", "result_da_only_EUR_per_MWh")


# ───────────────────────── 读数据、跑一个场站 ─────────────────────────

DA_COLUMN = "Production fcst I (DA) [kWh]"
ACTUAL_COLUMN = "Theoretical production [kWh]"


def load_da_and_actual(plant_dir, site):
    """DA 预测 I 和理论发电量 AH（kWh，15 分钟，当地时间区间起点）。只读 I 列：以后预测文件里只剩 DA 一列也能用。"""
    def cat(sub, column):
        files = sorted((Path(plant_dir) / "data" / sub).glob(f"*/15min/{site}.csv"))
        if not files:
            raise FileNotFoundError(f"{plant_dir}/data/{sub}/*/15min/{site}.csv 不存在")
        x = pd.concat(pd.read_csv(f, index_col=0, parse_dates=True, encoding="utf-8-sig", usecols=lambda c: c in ("Date", column))
                      for f in files).sort_index()
        return x[~x.index.duplicated(keep="last")][column]
    return pd.concat([cat("production forecast", DA_COLUMN).rename("I"),
                      cat("theoretical production", ACTUAL_COLUMN).rename("AH")], axis=1, join="outer", sort=True)


def run_site(plant_dir, site, prices, read_ust, lead, start=None, end=None):
    x = load_da_and_actual(plant_dir, site)
    x = x.join(prices[["P", "AA", "Pneg"]], how="left")
    if start:
        x = x[x.index >= pd.Timestamp(start)]
    if end:
        x = x[x.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
    months = sorted({f"{t:%Y-%m}" for t in x.index})
    x = x.join(final_plan(read_ust, site, lead, months), how="left")
    return compute(x)


def main():
    ap = argparse.ArgumentParser(description="Tokaj 五站 DA + IDC 滚动（超短期）结算回溯")
    ap.add_argument("--plant-dir", default=os.environ.get("TOKAJ_PLANT_DIR", str(T.BASE.parent / "vcom")),
                    help="vcom 仓库的 checkout 路径（环境变量 TOKAJ_PLANT_DIR，默认 ../vcom）")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--end", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--sites", nargs="+", choices=SITES, default=SITES)
    ap.add_argument("--lead", nargs="+", type=int, choices=UST_HORIZONS, default=[DEFAULT_LEAD],
                    help="最终计划用提前多少分钟的超短期（可给多个一次比较），默认 %(default)s")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ust-dir", help="超短期视图的本地目录（R2 里 ust/view/ 的副本：<DIR>/<YYYY-MM>/<场站>.csv）")
    src.add_argument("--ust-r2", action="store_true", help="直接读 R2（需要环境变量 R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET）")
    ap.add_argument("--ust-prefix", default="ust", help="R2 里超短期的前缀，默认 %(default)s")
    ap.add_argument("--id3", help="Montel 导出的 ID3 指数 CSV（给了就用它，不再用 data/enappsys/ID3/HU_*.csv）")
    ap.add_argument("--imbalance-price-csv", help="CSV：Date,price（EUR/MWh，15 分钟），覆盖仓库里的不平衡价")
    ap.add_argument("--imbalance-eur-from", default=T.IMBALANCE_EUR_FROM,
                    help="仓库里的 HU 不平衡价从这一天起已经是 EUR/MWh（不再除汇率），默认 %(default)s")
    ap.add_argument("--imbalance-source", choices=["raw", "hourly"], default="raw")
    ap.add_argument("--out", default=str(DATA / "tokaj_settlement"), help="输出目录（默认 data/tokaj_settlement）")
    ap.add_argument("--detail", help="另外把逐 15 分钟明细写到这个目录（不要提交）")
    args = ap.parse_args()

    prices = T.load_prices(imb_csv=args.imbalance_price_csv, eur_from=args.imbalance_eur_from or None,
                           imb_source=args.imbalance_source, id3_csv=args.id3)
    if args.start and prices["AA"].loc[args.start:args.end].isna().all():
        print("提示：这段时间没有 ID3 价格，IDC 调整收入按 0 计（汇总里的 uncovered_idc_kWh 是没有价格覆盖的调整量）")
    read_ust = r2_reader(args.ust_prefix) if args.ust_r2 else dir_reader(args.ust_dir)

    daily = []
    for site in args.sites:
        for lead in args.lead:
            d = run_site(args.plant_dir, site, prices, read_ust, lead, args.start, args.end)
            s = summarize(d, lead, "D")
            s.insert(0, "site", site)
            s.index = s.index.to_timestamp()
            s.index.name = "date"
            daily.append(s.reset_index())
            if args.detail:
                Path(args.detail).mkdir(parents=True, exist_ok=True)
                d.to_csv(Path(args.detail) / f"{site}_lead{lead}.csv", encoding="utf-8-sig")
            cov = f"{int(s.cells_with_ust.sum())}/{int(s.cells.sum())}"
            gap = int(s.cells_daytime_no_ust.sum())
            print(f"[{site}] 提前 {lead} 分钟：{len(s)} 天，实测 {s.actual_kWh.sum():,.0f} kWh，不平衡费用 {s.imbalance_fee_EUR.sum():,.0f} EUR，"
                  f"最终收入 {s.result_EUR.sum():,.0f} EUR（只用 DA {s.result_da_only_EUR.sum():,.0f}，IDC 收益 {s.idc_gain_EUR.sum():,.0f}）"
                  f"；有超短期的格子 {cov}，白天缺超短期 {gap} 格")
    new = pd.concat(daily, ignore_index=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    f_daily = out / "daily.csv"
    if f_daily.exists():
        old = pd.read_csv(f_daily, parse_dates=["date"])
        key = set(zip(new["site"], new["date"], new["lead_min"]))
        old = old[[k not in key for k in zip(old["site"], old["date"], old["lead_min"])]]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["site", "lead_min", "date"]).reset_index(drop=True)
    new = add_ratios(new[[c for c in new.columns if c not in RATIO_COLS]])
    num = new.select_dtypes("number").columns
    new[num] = new[num].round(4)
    new.to_csv(f_daily, index=False, encoding="utf-8-sig")

    add_cols = [c for c in new.columns if c not in ("site", "date", "lead_min") + RATIO_COLS]
    m = new.assign(month=new["date"].dt.to_period("M")).groupby(["site", "lead_min", "month"])[add_cols].sum().reset_index()
    m["month"] = m["month"].astype(str)
    add_ratios(m).round(4).to_csv(out / "monthly.csv", index=False, encoding="utf-8-sig")
    print(f"已写入 {f_daily} 和 {out / 'monthly.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
