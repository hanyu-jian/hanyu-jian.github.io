"""tokaj_imbalance_price_calculation.compute() 的小测试：期望值都是手算的，不依赖 Excel 或任何数据文件。

    python py/tests/test_tokaj_imbalance_price_calculation.py
（也兼容 pytest）
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import tokaj_imbalance_price_calculation as T  # noqa: E402

NAN = np.nan


def one(ts, **kw):
    cols = dict(I=NAN, J=NAN, K=NAN, L=NAN, M=NAN, N=NAN, AH=NAN, P=NAN, V=NAN, W=NAN, X=NAN, AA=NAN,
                Pneg=NAN, Ppos=NAN, AJ=NAN)
    cols.update(kw)
    return pd.DataFrame([cols], index=pd.DatetimeIndex([ts]))


def close(a, b, tol=1e-9):
    assert abs(a - b) < tol, f"{a} != {b}"


def test_id3_morning_uses_ida_position_and_all_revenues():
    # 11:00：K 为空 → 已交易头寸取 J；Z = N − J
    r = T.compute(one("2026-10-01 11:00", I=100, J=110, N=130, AH=120, P=50, V=60, AA=70, Pneg=40, Ppos=40)).iloc[0]
    assert (r["H"], r["S"], r["T"], r["U"], r["Y"], r["Z"]) == (100, 10, 0, 0, 0, 20)
    close(r.position, 130); close(r.AI, 10)
    close(r.Q, 5.0); close(r.AB, 0.6); close(r.AE, 1.4); close(r.AF, 7.0)
    close(r.fee, -0.4)                        # 工作簿：缺电 AI>0 → 费用为负（成本）：−10 kWh × 40 EUR/MWh（AJ 缺失，只算一次）
    close(r.result, 6.6)                      # AS = AF + AQ = 7.0 − 0.4


def test_ida2_ida3_and_noon_id3_basis():
    # 12:00：T = K − J，U = L − K，Z 以 L 为基准（N 空 → 0）
    r = T.compute(one("2026-10-01 12:00", I=100, J=110, K=108, L=105, AH=100)).iloc[0]
    assert (r["S"], r["T"], r["U"], r["Z"]) == (10, -2, -3, 0)
    close(r.position, 105); close(r.AI, 5)
    z = T.compute(one("2026-10-01 12:00", I=100, J=110, K=108, L=105, N=120, AH=0)).iloc[0]
    close(z["Z"], 15)                            # N − L，而不是 N − K


def test_id3_afternoon_only_16_to_19():
    r = T.compute(one("2026-10-01 17:00", I=100, J=95, K=92, L=90, M=95, AH=95)).iloc[0]
    close(r["Y"], 5); close(r["U"], -2)
    close(r.position, 100 + (95 - 100) + (92 - 95) + (90 - 92) + 5)   # H + S + T + U + Y
    out = T.compute(one("2026-10-01 20:00", I=1, J=1, L=1, M=9, AH=1)).iloc[0]
    close(out["Y"], 0)                           # 16:00~19:45 以外不算


def test_k_blank_vs_k_zero():
    blank = T.compute(one("2026-10-01 13:00", I=100, J=100, L=90, AH=0)).iloc[0]
    zero = T.compute(one("2026-10-01 13:00", I=100, J=100, K=0, L=90, AH=0)).iloc[0]
    close(blank["T"], 0); close(zero["T"], -100)    # K 为空：T=0；K 为数值 0：T = 0 − J
    close(blank["U"], -10); close(zero["U"], -10)   # 两种情况 U 都以 J 为基准


def test_fee_sign_matches_the_workbook_and_direction_is_optional():
    # 缺电 20 kWh，价格 50：AQ = −MAX(0,AI)×Ppos/1000 = −1（成本）；不管系统方向是正是负，只有一列成立
    for sd in (5.0, -5.0):
        r = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50, AJ=sd)).iloc[0]
        close(r.fee, -1.0)
    pos = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50, AJ=5.0)).iloc[0]
    close(pos.AO, -1.0); close(pos.AP, 0.0)   # AJ>=0 → AO；AJ<0 → AP
    neg = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50, AJ=-5.0)).iloc[0]
    close(neg.AO, 0.0); close(neg.AP, -1.0)
    # 余电 20 kWh：AQ = −MIN(0,AI)×Pneg/1000 = +1（收入）
    sur = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=120, Ppos=50, Pneg=50, AJ=5.0)).iloc[0]
    close(sur.AI, -20); close(sur.AM, 1.0); close(sur.fee, 1.0)
    # 系统方向缺失：Excel 当 0，只算一次；AM~AP 留空，并标记
    na = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50)).iloc[0]
    close(na.fee, -1.0); assert na["no_direction"] and np.isnan(na.AM) and np.isnan(na.AP)
    # 系统方向恰好为 0：工作簿里 AM/AO 用 AJ>=0、AN/AP 用 AJ<0，互斥，所以只算一次（旧脚本曾在这里重复计两次）
    z = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=120, Ppos=50, Pneg=50, AJ=0)).iloc[0]
    close(z.fee, 1.0); close(z.AM, 1.0); close(z.AN, 0.0)


def test_missing_direction_column_entirely():
    # 数据里根本没有 AJ 这一列（另外四个站的情形）也要当「方向未知」处理，而不是报错
    df = one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50).drop(columns=["AJ"])
    r = T.compute(df).iloc[0]
    close(r.fee, -1.0); assert r["no_direction"]


def test_legacy_mode_is_an_alias_of_the_default():
    # 旧版本把默认模式的符号写反了，legacy 才是对的；现在默认模式已按工作簿修正，两者数值相同
    for kw, fee in ((dict(AH=120, Pneg=50, Ppos=50), 1.0),         # 余电 20 kWh × 50 EUR/MWh = +1（收入）
                    (dict(AH=80, Pneg=50, Ppos=50), -1.0),         # 缺电付钱
                    (dict(AH=120, Pneg=-10, Ppos=-10), -0.2)):     # 负价格：余电反而要付钱
        a = T.compute(one("2026-10-01 12:00", I=100, J=100, **kw), "legacy").iloc[0]
        b = T.compute(one("2026-10-01 12:00", I=100, J=100, **kw)).iloc[0]
        close(a.fee, fee); close(b.fee, fee)


# 下面的数值都是从工作簿（PV_Generation_Forecast_Test_Robestec_Jan-Aug2026，Putnok 表）里原样取出来的：
# 输入（预测 I~N、实测 AH、价格、系统方向）和 Excel 算出来的 AF、AI、AQ、AS。
# 脚本的默认模式必须和它逐行一致——尤其是符号。（2026-01~08 全部 22,924 个格子都核对过，这里只放有代表性的几行。）
WORKBOOK_ROWS = [
    # (工作簿行号, 时间, 输入, 期望 AI, AF, AQ, AS)
    (34, "2026-01-01 07:15", dict(AH=12.673, P=79.41, V=78.41, W=68.76, AA=72.14, Pneg=31.13648157758173, Ppos=31.13648157758173, AJ=2.2),
     -12.673, 0.0, 0.39459263103269326, 0.39459263103269326),                                   # 余电、价格为正 → 收入
    (49, "2026-01-01 11:00", dict(N=382.232, AH=329.933, P=41.26, V=45.25, W=42.83, AA=51.0, Pneg=-46.44519979242346, Ppos=-46.44519979242346, AJ=488.16),
     52.299, 19.493832, 2.429037503943956, 21.922869503943957),                                  # 缺电但价格为负 → 反而赚钱
    (63, "2026-01-01 14:30", dict(L=737.163, AH=1792.223, P=56.87, V=62.99, W=54.69, X=62.78, AA=47.75, Pneg=189.2411001556824, Ppos=189.2411001556824, AJ=-9.1),
     -1055.06, 46.27909314000001, 199.6607151302543, 245.9398082702543),                         # 余电 1055 kWh → +199.66
    (138, "2026-01-02 09:15", dict(I=368.581, J=395.884, K=375.407, AH=181.795, P=96.0, V=106.09, W=95.7, AA=101.53, Pneg=186.4664504411002, Ppos=186.4664504411002, AJ=-8.5),
     193.612, 36.32070236999999, -36.102142402802286, 0.21855996719770587),                      # 缺电 193.6 kWh → −36.10（成本）
]
# 四个格子 AM / AN / AO / AP 各一行：(工作簿行号, AI, AJ, 不平衡价, 期望 (AM, AN, AO, AP))
WORKBOOK_CELLS = [
    (11589, -1172.321, 81.38, -146.5427002513386, (-171.7950849013495, 0.0, 0.0, 0.0)),
    (13423, -163.1500000000001, -1.27, 283.7828485873513, (0.0, 46.29917174702639, 0.0, 0.0)),
    (12631, 980.7749999999997, 149.44, -52.60739927358729, (0.0, 0.0, 51.59602202255256, 0.0)),
    (13181, 164.69000000000003, -9.45, 526.2119089316988, (0.0, 0.0, 0.0, -86.66183928196148)),
]


def test_matches_workbook_rows():
    for row, ts, inp, ai, af, aq, as_ in WORKBOOK_ROWS:
        r = T.compute(one(ts, **inp)).iloc[0]
        close(r.AI, ai, 1e-6); close(r.AF, af, 1e-9); close(r.fee, aq, 1e-9); close(r.result, as_, 1e-9)


def test_matches_workbook_four_cells():
    for row, ai, aj, price, (am, an, ao, ap) in WORKBOOK_CELLS:
        # 构造 H=100、AH=100−AI，使 AI 恰好等于工作簿里的值
        r = T.compute(one("2026-05-01 12:00", I=100, J=100, AH=100 - ai, Pneg=price, Ppos=price, AJ=aj)).iloc[0]
        close(r.AI, ai, 1e-9)
        for got, want in ((r.AM, am), (r.AN, an), (r.AO, ao), (r.AP, ap)):
            close(got, want, 1e-9)
        close(r.fee, am + an + ao + ap, 1e-9)


def test_invalid_and_uncovered():
    r = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=NAN, P=40)).iloc[0]
    assert not r.valid                        # 实测缺失 → 整格剔除
    ok = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=100, P=40)).iloc[0]
    assert ok.valid
    close(ok.uncovered_kWh, 10 + 10 + 5)      # |S|+|T|+|U| 都没有 IDA 价格覆盖
    covered = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=100, V=1, W=1, X=1)).iloc[0]
    close(covered.uncovered_kWh, 0)
    assert not T.compute(one("2026-10-01 12:00", I=NAN, J=1, AH=1)).iloc[0].valid   # 日前预测缺失也剔除


def test_load_montel_parsing_sign_and_incomplete_days():
    import tempfile
    head = "Date (CET),DA Price,IDA1 Price (HUPX),IDA2 Price (HUPX),IDA3 Price (HUPX),IDA1 Volume (HUPX),IDA2 Volume (HUPX),IDA3 Volume (HUPX),SYSTEM DIRECTION\n"
    units = ",EUR/MWh,EUR/MWh,EUR/MWh,EUR/MWh,MW,MW,MW,MW\n"
    rows = []
    for ts in pd.date_range("2026-08-04 00:00", periods=96, freq="15min"):       # 残缺日：IDA1 只有前 4 格
        rows.append((ts, 50.0, 60.0 if ts.hour == 0 else None, 70.0, None, 1, 2, None, 5.0))
    for ts in pd.date_range("2026-08-05 00:00", periods=96, freq="15min"):       # 完整日；系统方向 −3 → AJ = +3
        rows.append((ts, 50.0, 61.0, 71.0, 81.0 if ts.hour >= 12 else None, 1, 2, 3, -3.0 if ts.minute else None))
    for ts in pd.date_range("2026-08-06 00:00", periods=6, freq="15min"):        # 文件最后一天只有 6 行：IDA1 保留
        rows.append((ts, 50.0, 62.0, None, None, 1, None, None, 1.0))
    body = "".join(",".join(["" if v is None else (v.strftime("%d/%m/%Y %H:%M") if hasattr(v, "strftime") else str(v)) for v in r]) + "\n"
                   for r in rows)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(head + units + body)
    m = T.load_montel(f.name, quiet=True)
    assert len(m) == 96 + 96 + 6 and m.index[0] == pd.Timestamp("2026-08-04 00:00")      # dd/mm/yyyy，当地时间起点
    assert m.loc["2026-08-04", "V"].isna().all()                                         # 残缺日整日按缺失
    assert m.loc["2026-08-04", "W"].eq(70.0).all()                                       # 其它列不受影响
    assert m.loc["2026-08-05", "V"].eq(61.0).all()
    assert m.loc["2026-08-05", "X"].notna().sum() == 48                                  # IDA3 只有 12:00 起
    assert m.loc["2026-08-06", "V"].eq(62.0).all()                                       # 最后一天不算残缺日
    assert m.loc["2026-08-04", "AJ"].eq(-5.0).all()                                      # AJ = −SYSTEM DIRECTION
    assert m.loc["2026-08-05 00:15", "AJ"] == 3.0 and np.isnan(m.loc["2026-08-05 00:00", "AJ"])


def test_load_montel_id3_brackets_and_blanks():
    import tempfile
    txt = ("Date (CET),HU HUPX PRICE INDEX ID3,HU HUPX TRADED VOLUME INDEX ID3\n,EUR/MWh,MW\n"
           "[01/01/2026 00:00],108.90,514.7\n[01/01/2026 00:15],,522.4\n[20/10/2026 23:45],328.81,686.1\n")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(txt)
    a = T.load_montel_id3(f.name)
    assert a.name == "AA" and len(a) == 3
    close(a[pd.Timestamp("2026-01-01 00:00")], 108.9)
    assert np.isnan(a[pd.Timestamp("2026-01-01 00:15")])                                  # 空值保持空，不当 0
    close(a[pd.Timestamp("2026-10-20 23:45")], 328.81)                                    # dd/mm/yyyy，当地时间起点


def test_load_forecast_xlsx_reads_cached_values_and_stops_at_totals():
    import datetime as dt
    import tempfile
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Putnok"
    ws["A4"] = "Date"
    base = dt.datetime(2026, 8, 1, 0, 0)
    for i in range(3):
        r = 5 + i
        ws.cell(r, 1, base + dt.timedelta(minutes=15 * i))
        for j, col in enumerate(range(9, 15)):                  # I~N
            if not (j >= 3 and i == 0):                         # 第一行 L~N 留空
                ws.cell(r, col, 100 * (j + 1) + i)
    ws.cell(9, 1, "Total")                                      # 汇总行：读到这里停
    ws.cell(9, 9, 12345)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        wb.save(f.name)
    fc = T.load_forecast_xlsx(f.name, "Putnok")
    assert list(fc.columns) == T.FCST_LETTERS and len(fc) == 3
    assert fc.index[1] == pd.Timestamp("2026-08-01 00:15")
    assert fc.loc["2026-08-01 00:30", "I"] == 102 and fc.loc["2026-08-01 00:30", "N"] == 602
    assert np.isnan(fc.loc["2026-08-01 00:00", "L"]) and fc.loc["2026-08-01 00:15", "L"] == 401


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} 个测试全部通过")
