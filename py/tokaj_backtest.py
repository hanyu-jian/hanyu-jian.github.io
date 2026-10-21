"""
Tokaj 五站「预测 → 报量 → 不平衡」回溯计算（复现 PV_Generation_Forecast_Test 工作簿里的公式）。

数据来源（都是布达佩斯当地时间、15 分钟、区间起点标签）：
  电站数据（在 vcom 仓库，用 --plant-dir 指向它的 checkout；本脚本只读，不复制进本仓库）
      <plant-dir>/data/production forecast/<年>/15min/<场站>.csv    乙方的发电量预测 I~N 六列，kWh
      <plant-dir>/data/theoretical production/<年>/15min/<场站>.csv  实测理论发电量 kWh（工作簿的 AH 列）
  价格（本仓库 data/）
      data/15min/price.csv            HU 日前价 EUR/MWh（工作簿 P 列）
      data/intraday/HU.csv            ID3 指数 EUR/MWh（工作簿 AA 列）
      raw_data/<年>/A85.csv           HU 不平衡价，15 分钟（2021~），福林；用 data/MNB.csv 的 EUR 汇率折成 EUR/MWh（默认）
      data/imbalance_price.csv        同一份数据的小时均值（--imbalance-source hourly 才用）
  可选（本仓库没有，有就给）
      --ida-prices FILE               列 Date,IDA1,IDA2,IDA3（EUR/MWh，15 分钟，区间起点，当地时间）→ V/W/X 列
      --imbalance-price-csv FILE      列 Date,price（EUR/MWh，15 分钟）→ 覆盖 A85（例如 MAVIR 官方的月度结算价）
      --montel FILE                   Montel 导出的 CSV（Date (CET)、DA/IDA1~3 价格、IDA1~3 成交量、SYSTEM DIRECTION，15 分钟，
                                      第二行是单位）→ IDA1/2/3 价格（V/W/X）和系统方向（AJ，符号取反，见下）
      --system-direction FILE         列 Date 加 AJ（或 system_direction）：系统方向，15 分钟，区间起点，当地时间 → AJ 列

计算（每个 15 分钟，kWh / EUR），和工作簿一致：
    H = I                                   日前仓位
    S = J − H                               IDA1 成交量
    T = 0 若 K 空，否则 K − J               IDA2 成交量
    U = 0 若 L 空，否则 L − (J 若 K 空或为 0，否则 K)       IDA3 成交量
    Y = 0 若 M 空，否则 M − L               ID3 下午段（16:00~19:45）
    Z = 0 若 N 空，否则 N − (J 若 K 空或为 0，否则 K)（11:00~11:45）；12:00~15:45 为 N − L
    不平衡 AI = H+S+T+U+Y+Z − AH            （正 = 报多了/缺电，负 = 报少了/余电）
    收入：Q = P·H/1000，AB = V·S/1000，AC = W·T/1000，AD = X·U/1000，AE = AA·(Y+Z)/1000，AF = Q+AB+AC+AD+AE
    不平衡费用（默认，和工作簿 Putnok 那张表一致，五个站统一用它；--fee-mode legacy 是另外四张旧表的写法）：
        AM = AI<=0 且 AJ>=0 时 −AI·Pneg/1000·(−1)，AN = AI<=0 且 AJ<=0 时同式，
        AO = AI>=0 且 AJ>=0 时 −AI·Ppos/1000·(−1)，AP = AI>=0 且 AJ<=0 时同式，费用 AQ = AM+AN+AO+AP
        （AJ 是系统方向。两个负号抵消，实际是 +AI·价格：缺电 AI>0 时费用为正，余电 AI<0 时为负。）
        AJ 非零时四列里恰好一列成立；AJ 为 0 时两列同时成立，按模板原样重复计一次。
        AJ 缺失（本仓库没有这份数据）：Excel 会把空当成 0 而重复计算，这里按「方向未知、只算一次」处理，
        AM~AP 留空，并在汇总里统计没有系统方向的格子数（cells_no_system_direction）。
        可用 --montel 或 --system-direction 提供系统方向。
        Montel 的 SYSTEM DIRECTION（MW）和工作簿 Putnok 的 AJ 符号相反（8 月 97.5% 的格子相反，取反后四列费用与 Excel
        只有 0.8% 的格子落在不同的列，总费用相同），所以读 Montel 时 AJ = −SYSTEM DIRECTION。
        Montel 里个别日子的 IDA1/IDA2 只有 00:00~00:45 四个格子（和工作簿一样是「残缺日」），整日按缺失处理。
    价格缺失按 0 计（和 Excel 的空单元格一样），但会在汇总里统计「没有价格覆盖的成交量」。
    实测 AH 缺失（或日前预测 I 缺失）的格子整格剔除，不参与任何汇总（工作簿里这类格子结果为 0）。

说明：工作簿里只有 Putnok 一张表带系统方向，用的是上面这套公式；另外四张表还是旧公式（符号相反：缺电付费、
余电得钱，且没有系统方向）。按约定一切以 Putnok 为准，所以默认五站都用 Putnok 的写法；旧写法保留为 --fee-mode legacy。

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
# A85（以及由它得到的 imbalance_price.csv）的 HU 列：2026-09-30 及以前是 HUF/MWh（几万量级），2026-10-01 起就是
# EUR/MWh（几百量级，不用再除汇率）。这个日期是从数据量级判断出来的，不是数据源的说明；可用 --imbalance-eur-from 修改。
IMBALANCE_EUR_FROM = "2026-10-01"
# Montel 里某个日子某个 IDA 价格只有这么多个格子以内，就当作残缺日整日留空（工作簿的做法；文件最后一天除外）
INCOMPLETE_DAY_MAX_CELLS = 8
FCST_COL = {  # vcom 里 production forecast 的列名 -> 模板列字母
    "Production fcst I (DA) [kWh]": "I", "Production fcst J (IDA1) [kWh]": "J",
    "Production fcst K (IDA2) [kWh]": "K", "Production fcst L (IDA3) [kWh]": "L",
    "Production fcst M (ID3 16-20h) [kWh]": "M", "Production fcst N (ID3 11-15h) [kWh]": "N",
}


# ───────────────────────── 核心计算（只依赖 DataFrame，和读文件无关）─────────────────────────

def compute(df, fee_mode="putnok"):
    """df：以 15 分钟起点（当地时间）为索引，列 I J K L M N（预测 kWh）、AH（实测 kWh）、
    P（日前价）、V W X（IDA1/2/3 价）、AA（ID3 价）、Pneg、Ppos（不平衡价，EUR/MWh）；
    AJ（系统方向）可选，缺失时费用只算一次（见模块说明）。fee_mode：'putnok'（默认，Putnok 模板）或 'legacy'
    （另外四张旧表）。返回加上计算列的拷贝，列名沿用工作簿的字母。"""
    d = df.copy()
    for c in FCST_LETTERS + ["AH", "P", "V", "W", "X", "AA", "Pneg", "Ppos", "AJ"]:
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

    if fee_mode == "legacy":
        d["AL"] = (-d["AI"] / 1000 * f("Pneg")).where(d["AI"] < 0, 0.0)   # 余电得钱
        d["AM"] = (-d["AI"] / 1000 * f("Ppos")).where(d["AI"] > 0, 0.0)   # 缺电付钱
        d["fee"] = d["AL"] + d["AM"]
    elif fee_mode == "putnok":
        ai = d["AI"].to_numpy()
        known = d["AJ"].notna().to_numpy()
        sd = d["AJ"].fillna(0).to_numpy()
        v_neg = -ai / 1000 * f("Pneg").to_numpy() * (-1)
        v_pos = -ai / 1000 * f("Ppos").to_numpy() * (-1)
        parts = {
            "AM": np.where((ai <= 0) & (sd >= 0), v_neg, 0.0),
            "AN": np.where((ai <= 0) & (sd <= 0), v_neg, 0.0),
            "AO": np.where((ai >= 0) & (sd >= 0), v_pos, 0.0),
            "AP": np.where((ai >= 0) & (sd <= 0), v_pos, 0.0),
        }
        single = np.where(ai < 0, v_neg, np.where(ai > 0, v_pos, 0.0))      # 方向未知：只算一次
        for k, v in parts.items():
            d[k] = np.where(known, v, np.nan)
        d["fee"] = np.where(known, sum(parts.values()), single)
        d["no_direction"] = ~known
    else:
        raise ValueError(f"fee_mode 只能是 putnok 或 legacy，收到 {fee_mode!r}")
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
    v["no_direction"] = v["no_direction"] if "no_direction" in v else False
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
        "cells_no_system_direction": g["no_direction"].sum(),
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


def _find_col(cols, *words):
    for c in cols:
        if all(w.lower() in str(c).lower() for w in words):
            return c
    raise KeyError(f"Montel 文件里找不到包含 {words} 的列，实际列名: {list(cols)}")


def load_montel(path, quiet=False):
    """读 Montel 导出的 CSV，返回以当地时间（区间起点）为索引的表：
    V/W/X = IDA1/2/3 价格（EUR/MWh），AJ = −SYSTEM DIRECTION（MW，符号取反，见模块说明），
    DA = Montel 的日前价（只用来核对），VOL1/2/3 = IDA 成交量（MW，目前不参与计算）。"""
    m = pd.read_csv(path, skiprows=[1], encoding="utf-8-sig")        # 第 2 行是单位
    date = m.columns[0]
    out = pd.DataFrame({
        "Date": pd.to_datetime(m[date], format="%d/%m/%Y %H:%M"),
        "DA": m[_find_col(m.columns, "DA", "price")],
        "V": m[_find_col(m.columns, "IDA1", "price")], "W": m[_find_col(m.columns, "IDA2", "price")],
        "X": m[_find_col(m.columns, "IDA3", "price")],
        "VOL1": m[_find_col(m.columns, "IDA1", "volume")], "VOL2": m[_find_col(m.columns, "IDA2", "volume")],
        "VOL3": m[_find_col(m.columns, "IDA3", "volume")],
        "AJ": -pd.to_numeric(m[_find_col(m.columns, "system", "direction")], errors="coerce"),
    }).drop_duplicates("Date", keep="first").set_index("Date").sort_index()
    out = out.apply(pd.to_numeric, errors="coerce")
    last_day = out.index.max().normalize()
    day = out.index.normalize()
    for c in ("V", "W", "X"):
        n = out[c].notna().groupby(day).transform("sum")
        bad = (n > 0) & (n <= INCOMPLETE_DAY_MAX_CELLS) & (day != last_day)
        if bad.any() and not quiet:
            days = sorted({d.strftime("%Y-%m-%d") for d in day[bad]})
            print(f"提示：Montel {c} 有 {len(days)} 个残缺日（每天只有 {INCOMPLETE_DAY_MAX_CELLS} 个格子以内）按缺失处理: "
                  f"{', '.join(days[:6])}{' ...' if len(days) > 6 else ''}", file=sys.stderr)
        out.loc[bad, c] = np.nan
    return out


def load_prices(data_dir=DATA, ida_csv=None, imb_csv=None, fx_lag_days=1, eur_from=IMBALANCE_EUR_FROM,
                imb_source="raw", sd_csv=None, sd_sign=1, montel_csv=None):
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
    montel = load_montel(montel_csv) if montel_csv else None
    if montel is not None:
        for c in ("V", "W", "X"):
            p[c] = montel[c]
    if ida_csv:
        x = pd.read_csv(ida_csv, parse_dates=["Date"]).drop_duplicates("Date", keep="first").set_index("Date")
        for src, dst in (("IDA1", "V"), ("IDA2", "W"), ("IDA3", "X")):
            if src in x:
                p[dst] = x[src]

    if imb_csv:
        x = pd.read_csv(imb_csv, parse_dates=["Date"]).drop_duplicates("Date", keep="first").set_index("Date")
        p["Pneg"] = x["price"]
    else:
        raw_files = sorted((data_dir.parent / "raw_data").glob("*/A85.csv")) if imb_source == "raw" else []
        if raw_files:                                                                      # 15 分钟，福林
            parts = []
            for f in raw_files:
                x = pd.read_csv(f, usecols=["Date", "HU"])
                x["Date"] = pd.to_datetime(x["Date"], format="%Y/%m/%d %H:%M")
                parts.append(x)
            huf = pd.concat(parts).drop_duplicates("Date", keep="first").set_index("Date")["HU"].sort_index()
            step = None
        else:                                                                              # 小时，福林
            if imb_source == "raw":
                print("提示：没找到 raw_data/*/A85.csv，改用小时价 data/imbalance_price.csv", file=sys.stderr)
            ib = pd.read_csv(data_dir / "imbalance_price.csv", usecols=["Date", "HU"])
            ib["Date"] = pd.to_datetime(ib["Date"], format="%Y/%m/%d %H:%M")
            huf = ib.drop_duplicates("Date", keep="first").set_index("Date")["HU"]
            step = "1h"
        fx = pd.read_csv(data_dir / "MNB.csv", usecols=["Date", "EUR"])
        fx["Date"] = pd.to_datetime(fx["Date"], format="%Y/%m/%d")
        fx = fx.drop_duplicates("Date").set_index("Date")["EUR"]                         # HUF per EUR，工作日
        day = huf.index.normalize() - pd.Timedelta(days=fx_lag_days)
        rate = fx.sort_index().reindex(day, method="ffill").to_numpy()                   # T-1 的最近一个工作日汇率
        eur = (huf / rate).rename("Pneg")
        if eur_from:
            native = huf.index >= pd.Timestamp(eur_from)                               # 这些时段本来就是 EUR/MWh
            eur = eur.where(~native, huf)
        p["Pneg"] = eur.reindex(p.index.floor(step) if step else p.index).to_numpy()   # 小时价展开到 15 分钟
    p["Ppos"] = p["Pneg"]
    p["AJ"] = np.nan                                                                   # 系统方向，没给就留空
    if sd_csv:
        x = pd.read_csv(sd_csv, parse_dates=["Date"]).drop_duplicates("Date", keep="first").set_index("Date")
        col = "AJ" if "AJ" in x else "system_direction"
        p["AJ"] = x[col] * sd_sign
    elif montel is not None:
        p["AJ"] = montel["AJ"]
    return p


def run_site(plant_dir, site, prices, start=None, end=None, fee_mode="putnok"):
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
    ap.add_argument("--imbalance-source", choices=["raw", "hourly"], default="raw",
                    help="不平衡价来源：raw = raw_data/*/A85.csv（15 分钟，默认）；hourly = data/imbalance_price.csv（小时）")
    ap.add_argument("--montel", help="Montel 导出的 CSV（IDA1/2/3 价格、成交量、SYSTEM DIRECTION；SYSTEM DIRECTION 会取反当 AJ）")
    ap.add_argument("--system-direction-sign", type=int, choices=[1, -1], default=1,
                    help="--system-direction 文件的符号：1 = 原样当 AJ（默认），-1 = 取反")
    ap.add_argument("--system-direction", help="CSV：Date 加 AJ（或 system_direction），15 分钟，区间起点，当地时间")
    ap.add_argument("--fee-mode", choices=["putnok", "legacy"], default="putnok",
                    help="putnok = Putnok 表的不平衡费用公式（默认，五站统一）；legacy = 另外四张旧表的写法（符号相反）")
    ap.add_argument("--out", default=str(DATA / "tokaj"), help="输出目录（默认 data/tokaj）")
    ap.add_argument("--detail", help="另外把逐 15 分钟明细写到这个目录（不要提交）")
    args = ap.parse_args()

    prices = load_prices(ida_csv=args.ida_prices, imb_csv=args.imbalance_price_csv, eur_from=args.imbalance_eur_from or None,
                          imb_source=args.imbalance_source, sd_csv=args.system_direction,
                          sd_sign=args.system_direction_sign, montel_csv=args.montel)
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
