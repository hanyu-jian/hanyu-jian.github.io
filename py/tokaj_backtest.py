"""
Tokaj 五站「预测 → 报量 → 不平衡」回溯计算（复现 PV_Generation_Forecast_Test 工作簿里的公式）。

数据来源（都是布达佩斯当地时间、15 分钟、区间起点标签）：
  电站数据（在 vcom 仓库，用 --plant-dir 指向它的 checkout；本脚本只读，不复制进本仓库）
      <plant-dir>/data/production forecast/<年>/15min/<场站>.csv    乙方的发电量预测 I~N 六列，kWh
      <plant-dir>/data/theoretical production/<年>/15min/<场站>.csv  实测理论发电量 kWh（工作簿的 AH 列）
  价格（本仓库 data/）
      data/15min/price.csv            HU 日前价 EUR/MWh（工作簿 P 列）
      data/intraday/HU.csv            ID3 指数 EUR/MWh（工作簿 AA 列）
      data/imbalance_price.csv        HU 不平衡价，小时，HUF/MWh；用 data/MNB.csv 的 EUR 汇率折成 EUR/MWh
  可选（本仓库没有，有就给）
      --ida-prices FILE               列 Date,IDA1,IDA2,IDA3（EUR/MWh，15 分钟，区间起点，当地时间）→ V/W/X 列
      --imbalance-price-csv FILE      列 Date,price（EUR/MWh，15 分钟）→ 覆盖小时价（MAVIR 的 15 分钟价）

计算（每个 15 分钟，kWh / EUR），和工作簿一致：
    H = I                                   日前仓位
    S = J − H                               IDA1 成交量
    T = 0 若 K 空，否则 K − J               IDA2 成交量
    U = 0 若 L 空，否则 L − (J 若 K 空或为 0，否则 K)       IDA3 成交量
    Y = 0 若 M 空，否则 M − L               ID3 下午段（16:00~19:45）
    Z = 0 若 N 空，否则 N − (J 若 K 空或为 0，否则 K)（11:00~11:45）；12:00~15:45 为 N − L
    不平衡 AI = H+S+T+U+Y+Z − AH            （正 = 报多了/缺电，负 = 报少了/余电）
    收入：Q = P·H/1000，AB = V·S/1000，AC = W·T/1000，AD = X·U/1000，AE = AA·(Y+Z)/1000，AF = Q+AB+AC+AD+AE
    不平衡费用（默认，和工作簿里 Serényfalva/Sajókaza/Szuhakálló 的公式一致）：
        余电（AI<0）按 Pneg 得到收入 −AI·Pneg/1000；缺电（AI>0）按 Ppos 付费 −AI·Ppos/1000
    结果 = AF + 不平衡费用
    价格缺失按 0 计（和 Excel 的空单元格一样），但会在汇总里统计「没有价格覆盖的成交量」。
    实测 AH 缺失（或日前预测 I 缺失）的格子整格剔除，不参与任何汇总（工作簿里这类格子结果为 0）。

注意：工作簿里 Putnok 一张表用的是另一套不平衡费用公式（符号相反：缺电反而增加收入）。
默认按另外四张表的逻辑；--fee-mode putnok 才按 Putnok 那张表原样复现（用来验证，需要 AJ 系统方向列）。

输出（--out，默认 data/tokaj/）：
    daily.csv     每个场站 × 每天的汇总
    monthly.csv   由 daily.csv 汇总
  同一场站同一天重算会覆盖，其它天保留。--detail DIR 另外写出逐 15 分钟的明细（不要提交）。

用法：
    python py/tokaj_backtest.py --plant-dir ../vcom --start 2026-09-24 --end 2026-10-17
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
SITES = ["Putnok", "Serenyfalva", "Sajokaza", "Szuhakálló 1", "Szuhakálló 2"]
FCST_LETTERS = ["I", "J", "K", "L", "M", "N"]
# data/imbalance_price.csv 的 HU 列：2026-09-30 及以前是 HUF/MWh（6~8 万量级），2026-10-01 起就是 EUR/MWh（几百量级，
# 不用再除汇率）。这个日期是从数据量级判断出来的，不是数据源的说明；可用 --imbalance-eur-from 修改。
IMBALANCE_EUR_FROM = "2026-10-01"
FCST_COL = {  # vcom 里 production forecast 的列名 -> 模板列字母
    "Production fcst I (DA) [kWh]": "I", "Production fcst J (IDA1) [kWh]": "J",
    "Production fcst K (IDA2) [kWh]": "K", "Production fcst L (IDA3) [kWh]": "L",
    "Production fcst M (ID3 16-20h) [kWh]": "M", "Production fcst N (ID3 11-15h) [kWh]": "N",
}


# ───────────────────────── 核心计算（只依赖 DataFrame，和读文件无关）─────────────────────────

def compute(df, fee_mode="consistent"):
    """df：以 15 分钟起点（当地时间）为索引，列 I J K L M N（预测 kWh）、AH（实测 kWh）、
    P（日前价）、V W X（IDA1/2/3 价）、AA（ID3 价）、Pneg、Ppos（不平衡价，EUR/MWh）；
    fee_mode='putnok' 时还要有 AJ（系统方向）。返回加上计算列的拷贝，列名沿用工作簿的字母。"""
    d = df.copy()
    for c in FCST_LETTERS + ["AH", "P", "V", "W", "X", "AA", "Pneg", "Ppos"]:
        if c not in d:
            d[c] = np.nan
    hour = d.index.hour
    I, J, K, L, M, N = (d[c] for c in FCST_LETTERS)
    k_empty0 = K.isna() | (K == 0)                      # Excel 里 K=0 对空单元格也成立
    base_k = J.fillna(0).where(k_empty0, K)              # 「已交易头寸」：K 为 0/空时取 J

    d["H"] = I.fillna(0)
    d["S"] = J.fillna(0) - d["H"]
    d["T"] = (K - J.fillna(0)).where(K.notna(), 0.0)
    d["U"] = (L - base_k).where(L.notna(), 0.0)
    d["Y"] = (M - L.fillna(0)).where(M.notna() & (hour >= 16) & (hour <= 19), 0.0)
    z_early = N - base_k                                  # 11:00~11:45
    z_late = N - L.fillna(0)                              # 12:00~15:45
    d["Z"] = pd.Series(np.where(hour < 12, z_early, z_late), index=d.index).where(
        N.notna() & (hour >= 11) & (hour <= 15), 0.0)
    d["position"] = d[["H", "S", "T", "U", "Y", "Z"]].sum(axis=1)
    d["AI"] = d["position"] - d["AH"]

    f = lambda c: d[c].fillna(0)
    d["Q"] = f("P") * d["H"] / 1000
    d["AB"] = f("V") * d["S"] / 1000
    d["AC"] = f("W") * d["T"] / 1000
    d["AD"] = f("X") * d["U"] / 1000
    d["AE"] = f("AA") * (d["Y"] + d["Z"]) / 1000
    d["AF"] = d[["Q", "AB", "AC", "AD", "AE"]].sum(axis=1)

    if fee_mode == "consistent":
        d["AL"] = (-d["AI"] / 1000 * f("Pneg")).where(d["AI"] < 0, 0.0)   # 余电
        d["AM"] = (-d["AI"] / 1000 * f("Ppos")).where(d["AI"] > 0, 0.0)   # 缺电
        d["fee"] = d["AL"] + d["AM"]
    elif fee_mode == "putnok":
        sd = f("AJ")
        ai = d["AI"]
        d["AM"] = np.where((ai <= 0) & (sd >= 0), -ai / 1000 * f("Pneg") * (-1), 0.0)
        d["AN"] = np.where((ai <= 0) & (sd <= 0), -ai / 1000 * f("Pneg") * (-1), 0.0)
        d["AO"] = np.where((ai >= 0) & (sd >= 0), -ai / 1000 * f("Ppos") * (-1), 0.0)
        d["AP"] = np.where((ai >= 0) & (sd <= 0), -ai / 1000 * f("Ppos") * (-1), 0.0)
        d["fee"] = d[["AM", "AN", "AO", "AP"]].sum(axis=1)
    else:
        raise ValueError(f"fee_mode 只能是 consistent 或 putnok，收到 {fee_mode!r}")
    d["result"] = d["AF"] + d["fee"]
    d["valid"] = d["AH"].notna() & d["I"].notna()
    # 没有价格覆盖的成交量（价格缺失按 0 计，这里把它暴露出来）
    d["uncovered_kWh"] = (d["S"].abs() * d["V"].isna() + d["T"].abs() * d["W"].isna() + d["U"].abs() * d["X"].isna()
                          + (d["Y"] + d["Z"]).abs() * d["AA"].isna())
    return d


ADD = ["AH", "H", "position", "AI", "Q", "AB", "AC", "AD", "AE", "AF", "fee", "result", "uncovered_kWh"]


def summarize(d, freq="D"):
    """逐 15 分钟结果 -> 汇总。freq 'D' 按天，'M' 按月；只统计 valid 的格子。"""
    v = d[d["valid"]].copy()
    v["abs_imbalance"] = v["AI"].abs()
    v["Pneg_missing"] = v["Pneg"].isna() | v["Ppos"].isna()
    v["DA_missing"] = v["P"].isna()
    v["Pneg_zero"] = v["Pneg"] == 0          # 恰好为 0 的不平衡价，很可能是缺失被记成了 0
    g = v.groupby(v.index.to_period(freq))
    out = pd.DataFrame({
        "cells": g.size(),
        "actual_kWh": g["AH"].sum(),
        "da_position_kWh": g["H"].sum(),
        "final_position_kWh": g["position"].sum(),
        "imbalance_net_kWh": g["AI"].sum(),
        "imbalance_abs_kWh": g["abs_imbalance"].sum(),
        "rev_da_EUR": g["Q"].sum(),
        "rev_ida1_EUR": g["AB"].sum(),
        "rev_ida2_EUR": g["AC"].sum(),
        "rev_ida3_EUR": g["AD"].sum(),
        "rev_id3_EUR": g["AE"].sum(),
        "rev_before_imbalance_EUR": g["AF"].sum(),
        "imbalance_fee_EUR": g["fee"].sum(),
        "result_EUR": g["result"].sum(),
        "uncovered_trade_kWh": g["uncovered_kWh"].sum(),
        "cells_no_da_price": g["DA_missing"].sum(),
        "cells_no_imbalance_price": g["Pneg_missing"].sum(),
        "cells_zero_imbalance_price": g["Pneg_zero"].sum(),
    })
    return add_ratios(out)


def add_ratios(out):
    o = out.copy()
    a = o["actual_kWh"].replace(0, np.nan)
    o["imbalance_abs_pct_of_actual"] = o["imbalance_abs_kWh"] / a * 100
    o["imbalance_fee_EUR_per_MWh"] = o["imbalance_fee_EUR"] / a * 1000
    o["result_EUR_per_MWh"] = o["result_EUR"] / a * 1000
    return o


# ───────────────────────── 读数据 ─────────────────────────

def load_site(plant_dir, site):
    """预测 I~N 和实测 AH（kWh）。"""
    def cat(sub, glob_name):
        files = sorted((Path(plant_dir) / "data" / sub).glob(f"*/15min/{glob_name}"))
        if not files:
            raise FileNotFoundError(f"{plant_dir}/data/{sub}/*/15min/{glob_name} 不存在")
        x = pd.concat(pd.read_csv(f, index_col=0, parse_dates=True, encoding="utf-8-sig") for f in files).sort_index()
        return x[~x.index.duplicated(keep="last")]
    fc = cat("production forecast", f"{site}.csv").rename(columns=FCST_COL)[FCST_LETTERS]
    ac = cat("theoretical production", f"{site}.csv")["Theoretical production [kWh]"].rename("AH")
    return fc.join(ac, how="outer")


def load_prices(data_dir=DATA, ida_csv=None, imb_csv=None, fx_lag_days=1, eur_from=IMBALANCE_EUR_FROM):
    """返回 15 分钟价格表：P（日前）、AA（ID3）、V/W/X（IDA，没给则空）、Pneg/Ppos（不平衡价 EUR/MWh）。"""
    data_dir = Path(data_dir)
    da = pd.read_csv(data_dir / "15min" / "price.csv", usecols=["Date", "HU"])
    da["Date"] = pd.to_datetime(da["Date"], format="%Y/%m/%d %H:%M")
    da = da.drop_duplicates("Date", keep="first").set_index("Date")["HU"].rename("P")

    p = da.to_frame()
    it_path = data_dir / "intraday" / "HU.csv"
    if it_path.exists():
        it = pd.read_csv(it_path, sep="\t", usecols=["Date", "ID3"])
        it["Date"] = pd.to_datetime(it["Date"], format="%d/%m/%Y %H:%M")
        p["AA"] = it.drop_duplicates("Date", keep="first").set_index("Date")["ID3"]
    else:
        p["AA"] = np.nan
    for c in ("V", "W", "X"):
        p[c] = np.nan
    if ida_csv:
        x = pd.read_csv(ida_csv, parse_dates=["Date"]).drop_duplicates("Date", keep="first").set_index("Date")
        for src, dst in (("IDA1", "V"), ("IDA2", "W"), ("IDA3", "X")):
            if src in x:
                p[dst] = x[src]

    if imb_csv:
        x = pd.read_csv(imb_csv, parse_dates=["Date"]).drop_duplicates("Date", keep="first").set_index("Date")
        p["Pneg"] = x["price"]
    else:
        ib = pd.read_csv(data_dir / "imbalance_price.csv", usecols=["Date", "HU"])
        ib["Date"] = pd.to_datetime(ib["Date"], format="%Y/%m/%d %H:%M")
        huf = ib.drop_duplicates("Date", keep="first").set_index("Date")["HU"]          # HUF/MWh，小时起点
        fx = pd.read_csv(data_dir / "MNB.csv", usecols=["Date", "EUR"])
        fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
        fx = fx.drop_duplicates("Date").set_index("Date")["EUR"]                         # HUF per EUR，工作日
        day = huf.index.normalize() - pd.Timedelta(days=fx_lag_days)
        rate = fx.sort_index().reindex(day, method="ffill").to_numpy()                   # T-1 的最近一个工作日汇率
        eur = (huf / rate).rename("Pneg")
        if eur_from:
            native = huf.index >= pd.Timestamp(eur_from)                               # 这些小时本来就是 EUR/MWh
            eur = eur.where(~native, huf)
        p["Pneg"] = eur.reindex(p.index.floor("1h")).to_numpy()                          # 小时价展开到 15 分钟
    p["Ppos"] = p["Pneg"]
    return p


def run_site(plant_dir, site, prices, start=None, end=None, fee_mode="consistent"):
    x = load_site(plant_dir, site)
    x = x.join(prices, how="left")
    if start:
        x = x[x.index >= pd.Timestamp(start)]
    if end:
        x = x[x.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
    return compute(x, fee_mode)


# ───────────────────────── 命令行 ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Tokaj 五站预测→报量→不平衡回溯")
    ap.add_argument("--plant-dir", default=os.environ.get("TOKAJ_PLANT_DIR", str(BASE.parent / "vcom")),
                    help="vcom 仓库的 checkout 路径（环境变量 TOKAJ_PLANT_DIR，默认 ../vcom）")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--end", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--sites", nargs="+", choices=SITES, default=SITES)
    ap.add_argument("--ida-prices", help="CSV：Date,IDA1,IDA2,IDA3（EUR/MWh，15 分钟，区间起点，当地时间）")
    ap.add_argument("--imbalance-price-csv", help="CSV：Date,price（EUR/MWh，15 分钟），覆盖仓库里的小时价")
    ap.add_argument("--imbalance-eur-from", default=IMBALANCE_EUR_FROM,
                    help="仓库里的 HU 不平衡价从这一天起已经是 EUR/MWh（不再除汇率），默认 %(default)s；留空表示全部按 HUF 折算")
    ap.add_argument("--fee-mode", choices=["consistent", "putnok"], default="consistent")
    ap.add_argument("--out", default=str(DATA / "tokaj"), help="输出目录（默认 data/tokaj）")
    ap.add_argument("--detail", help="另外把逐 15 分钟明细写到这个目录（不要提交）")
    args = ap.parse_args()

    prices = load_prices(ida_csv=args.ida_prices, imb_csv=args.imbalance_price_csv, eur_from=args.imbalance_eur_from or None)
    for name, col in (("IDA1/2/3 价格", "V"), ("ID3 指数", "AA")):
        if args.start and prices[col].loc[args.start:args.end].isna().all():
            print(f"提示：这段时间没有 {name}，相关成交量的收入按 0 计（汇总里的 uncovered_trade_kWh 是没有价格覆盖的成交量）")

    daily = []
    for site in args.sites:
        d = run_site(args.plant_dir, site, prices, args.start, args.end, args.fee_mode)
        s = summarize(d, "D")
        s.insert(0, "site", site)
        s.index = s.index.to_timestamp()
        s.index.name = "date"
        daily.append(s.reset_index())
        if args.detail:
            Path(args.detail).mkdir(parents=True, exist_ok=True)
            d.to_csv(Path(args.detail) / f"{site}.csv", encoding="utf-8-sig")
        print(f"[{site}] {len(s)} 天，实测 {s.actual_kWh.sum():,.0f} kWh，"
              f"不平衡费用 {s.imbalance_fee_EUR.sum():,.0f} EUR，结果 {s.result_EUR.sum():,.0f} EUR")
    new = pd.concat(daily, ignore_index=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    f_daily = out / "daily.csv"
    if f_daily.exists():
        old = pd.read_csv(f_daily, parse_dates=["date"])
        key = set(zip(new["site"], new["date"]))
        old = old[[k not in key for k in zip(old["site"], old["date"])]]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["site", "date"]).reset_index(drop=True)
    cols = [c for c in new.columns if c not in ("imbalance_abs_pct_of_actual", "imbalance_fee_EUR_per_MWh", "result_EUR_per_MWh")]
    new = add_ratios(new[cols])
    num = new.select_dtypes("number").columns
    new[num] = new[num].round(4)
    new.to_csv(f_daily, index=False, encoding="utf-8-sig")

    add_cols = [c for c in new.columns if c not in ("site", "date", "imbalance_abs_pct_of_actual",
                                                    "imbalance_fee_EUR_per_MWh", "result_EUR_per_MWh")]
    m = new.assign(month=new["date"].dt.to_period("M")).groupby(["site", "month"])[add_cols].sum().reset_index()
    m["month"] = m["month"].astype(str)
    add_ratios(m).round(4).to_csv(out / "monthly.csv", index=False, encoding="utf-8-sig")
    print(f"已写入 {f_daily} 和 {out / 'monthly.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
