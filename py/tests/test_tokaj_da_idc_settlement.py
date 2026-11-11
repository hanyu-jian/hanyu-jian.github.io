"""tokaj_da_idc_settlement 的小测试：期望值都是手算的，或是从工作簿（Putnok 表）里原样取出来的。

    python py/tests/test_tokaj_da_idc_settlement.py
（也兼容 pytest）
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import tokaj_da_idc_settlement as S  # noqa: E402

NAN = np.nan


def one(ts, **kw):
    cols = dict(I=NAN, F=NAN, AH=NAN, P=NAN, AA=NAN, Pneg=NAN)
    cols.update(kw)
    return pd.DataFrame([cols], index=pd.DatetimeIndex([ts]))


def close(a, b, tol=1e-9):
    assert abs(a - b) < tol, f"{a} != {b}"


def test_by_hand_with_idc_adjustment():
    # DA 100，超短期（最终计划）120，实测 110；日前价 50，ID3 价 60，不平衡价 70
    r = S.compute(one("2026-11-01 12:00", I=100, F=120, AH=110, P=50, AA=60, Pneg=70)).iloc[0]
    close(r.H, 100); close(r.position, 120); close(r.IDC, 20)
    close(r.Q, 5.0)                    # 50 × 100 / 1000
    close(r.AE, 1.2)                   # 60 × 20 / 1000（多卖 20 kWh 按 ID3 价）
    close(r.AF, 6.2)
    close(r.AI, 10)                    # 120 − 110：计划比实际多 10 kWh，缺电
    close(r.fee, -0.7)                 # −10 × 70 / 1000，缺电 = 成本
    close(r.result, 5.5)
    # 基准：只按 DA 报，AI = 100 − 110 = −10（余电）→ 费用 +0.7，结果 5.7；IDC 修正反而少赚 0.2
    close(r.result_da_only, 5.7)
    assert r.has_ust and r.valid


def test_no_ust_means_no_adjustment():
    r = S.compute(one("2026-11-01 03:00", I=100, AH=90, P=50, AA=60, Pneg=70)).iloc[0]
    close(r.position, 100); close(r.IDC, 0); close(r.AE, 0)
    close(r.AI, 10); close(r.fee, -0.7); close(r.result, r.result_da_only)
    assert not r.has_ust
    assert r.daytime_no_ust                      # 有发电却没有超短期 → 覆盖缺口
    night = S.compute(one("2026-11-01 03:00", I=0, AH=0, P=50, AA=60, Pneg=70)).iloc[0]
    assert not night.daytime_no_ust              # 夜间没有超短期是正常的


def test_fee_matches_workbook_rows_with_single_price():
    # 工作簿 Putnok 表 AQ = SUM(AM:AP) 的原样数值；这里用单一价格的简化公式 −AI × 价格 / 1000 必须得到同样的数
    rows = [  # (AI, 价格, 工作簿 AQ)
        (-12.673, 31.13648157758173, 0.39459263103269326),      # 第 34 行：余电，价格为正 → 收入
        (52.299, -46.44519979242346, 2.429037503943956),        # 第 49 行：缺电，价格为负 → 反而赚钱
        (-1055.06, 189.2411001556824, 199.6607151302543),       # 第 63 行：余电 1055 kWh
        (193.612, 186.4664504411002, -36.102142402802286),      # 第 138 行：缺电 193.6 kWh → 成本
    ]
    for ai, price, aq in rows:
        r = S.compute(one("2026-01-01 12:00", I=0.0, AH=-ai, P=0, AA=0, Pneg=price)).iloc[0]
        close(r.AI, ai); close(r.fee, aq, 1e-9)


def test_missing_prices_count_as_zero_and_are_reported():
    r = S.compute(one("2026-11-01 12:00", I=100, F=120, AH=110)).iloc[0]       # 一个价格都没有
    close(r.Q, 0); close(r.AE, 0); close(r.fee, 0); close(r.result, 0)
    close(r.uncovered_kWh, 20)                    # 20 kWh 的 IDC 调整没有 ID3 价格
    s = S.summarize(S.compute(one("2026-11-01 12:00", I=100, F=120, AH=110)), 60)
    assert s.cells_no_da_price.iloc[0] == 1 and s.cells_no_imbalance_price.iloc[0] == 1


def test_invalid_rows_are_dropped_from_summary():
    d = pd.concat([one("2026-11-01 12:00", I=100, F=120, AH=NAN, P=50, AA=60, Pneg=70),     # 实测缺失
                   one("2026-11-01 12:15", I=NAN, F=120, AH=100, P=50, AA=60, Pneg=70),     # DA 缺失
                   one("2026-11-01 12:30", I=100, F=120, AH=110, P=50, AA=60, Pneg=70)])
    s = S.summarize(S.compute(d), 60)
    assert s.cells.iloc[0] == 1
    close(s.result_EUR.iloc[0], 5.5); close(s.idc_gain_EUR.iloc[0], -0.2)


def test_da_error_columns_and_lead_table():
    # 两个格子：DA 100 / 实际 110；超短期 120 / 实际 110。DA 误差 −10（偏低）、超短期 +10（偏高）
    d = pd.concat([one("2026-11-01 12:00", I=100, F=120, AH=110, P=50, AA=60, Pneg=70),
                   one("2026-11-01 12:15", I=100, F=105, AH=110, P=50, AA=60, Pneg=70)])
    s = S.summarize(S.compute(d), 60)
    close(s.da_imbalance_net_kWh.iloc[0], -20); close(s.da_imbalance_abs_kWh.iloc[0], 20)
    close(s.imbalance_net_kWh.iloc[0], 5); close(s.imbalance_abs_kWh.iloc[0], 15)          # (+10) + (−5)
    close(s.imbalance_abs_pct_of_actual.iloc[0], 15 / 220 * 100); close(s.da_abs_pct_of_actual.iloc[0], 20 / 220 * 100)
    daily = pd.concat([s.reset_index(), S.summarize(S.compute(d.assign(F=d["I"])), 15).reset_index()])
    t = S.lead_table(daily)
    close(t.loc[60, "abs_err_pct"], 15 / 220 * 100); close(t.loc[15, "abs_err_pct"], 20 / 220 * 100)   # F = DA → 误差同 DA
    assert t.loc[60, "vs_da"] < 0 and abs(t.loc[15, "vs_da"]) < 1e-9


def _view(rows, cols):
    head = "Date," + ",".join(cols) + "\n"
    return head + "".join(f"{t},{','.join('' if v is None else str(v) for v in vals)}\n" for t, vals in rows)


def test_final_plan_uses_smallest_lead_at_or_above_the_chosen_one():
    cols = ["kWh h45", "kWh h60", "kWh h75", "kWh h90"]
    text = _view([("2026-11-01 12:00:00", [1, 2, 3, 4]),           # 都有 → 取 h60
                  ("2026-11-01 12:15:00", [1, None, 30, 40]),      # h60 缺 → 退到更大的 h75
                  ("2026-11-01 12:30:00", [10, None, None, None]), # 只有 h45：lead=60 时没有
                  ("2026-11-01 12:45:00", [None, None, None, 99])], cols)
    read = lambda month, site: text if (month, site) == ("2026-11", "Putnok") else None
    f = S.final_plan(read, "Putnok", 60, ["2026-10", "2026-11"])
    assert list(f["F"]) == [2, 30, 99] and list(f["F_h"]) == [60, 75, 90]
    assert pd.Timestamp("2026-11-01 12:30") not in f.index
    f45 = S.final_plan(read, "Putnok", 45, ["2026-11"])
    assert list(f45["F"]) == [1, 1, 10, 99] and list(f45["F_h"]) == [45, 45, 45, 90]
    f15 = S.final_plan(read, "Putnok", 15, ["2026-11"])               # 没有 h15/h30 列也不报错
    assert list(f15["F"]) == [1, 1, 10, 99]
    assert S.final_plan(read, "Sajokaza", 60, ["2026-11"]).empty        # 这个场站没有视图


def test_dir_reader_accepts_both_layouts():
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "2026-11").mkdir()
        (Path(t) / "2026-11" / "Putnok.csv").write_text("Date,kWh h60\n2026-11-01 12:00:00,5\n", encoding="utf-8")
        (Path(t) / "view" / "2026-10").mkdir(parents=True)
        (Path(t) / "view" / "2026-10" / "Putnok.csv").write_text("Date,kWh h60\n2026-10-01 12:00:00,7\n", encoding="utf-8")
        r = S.dir_reader(t)
        assert "5" in r("2026-11", "Putnok") and "7" in r("2026-10", "Putnok") and r("2026-09", "Putnok") is None


def test_run_site_end_to_end_with_injected_prices():
    with tempfile.TemporaryDirectory() as t:
        plant = Path(t) / "vcom"
        idx = pd.date_range("2026-11-01 12:00", periods=4, freq="15min")
        fc = pd.DataFrame({"Production fcst I (DA) [kWh]": [100, 100, 100, np.nan]}, index=idx)
        (plant / "data" / "production forecast" / "2026" / "15min").mkdir(parents=True)
        fc.index.name = "Date"; fc.to_csv(plant / "data" / "production forecast" / "2026" / "15min" / "Putnok.csv")
        th = pd.DataFrame({"Theoretical production [kWh]": [110, 90, 100, 100]}, index=idx)
        (plant / "data" / "theoretical production" / "2026" / "15min").mkdir(parents=True)
        th.index.name = "Date"; th.to_csv(plant / "data" / "theoretical production" / "2026" / "15min" / "Putnok.csv")
        prices = pd.DataFrame({"P": 50.0, "AA": 60.0, "Pneg": 70.0}, index=idx)
        view = _view([("2026-11-01 12:00:00", [120]), ("2026-11-01 12:15:00", [80])], ["kWh h60"])
        d = S.run_site(plant, "Putnok", prices, lambda m, s: view, 60, "2026-11-01", "2026-11-01", da_source="power")
        assert list(d["valid"]) == [True, True, True, False]                 # 最后一格 DA 缺失
        assert list(d["has_ust"]) == [True, True, False, False]
        assert list(d["position"]) == [120, 80, 100, 0]                     # 最后一格 DA 缺失 → H 按 0，且这一格无效，不进汇总
        s = S.summarize(d, 60, "D")
        assert s.cells.iloc[0] == 3 and s.cells_with_ust.iloc[0] == 2 and s.cells_daytime_no_ust.iloc[0] == 1
        # 12:00 = 5.5（手算同上）；12:15：F=80, AH=90 → IDC −20 → AE −1.2；AI −10 → 费用 +0.7；Q 5 → 4.5；12:30 无超短期：AI 0 → 5
        close(s.result_EUR.iloc[0], 5.5 + (5 - 1.2 + 0.7) + 5.0)
        close(s.lead_min.iloc[0], 60)


def test_da_from_poa_uses_the_vcom_conversion_formula():
    with tempfile.TemporaryDirectory() as t:
        plant = Path(t) / "vcom"
        (plant / "py").mkdir(parents=True)
        (plant / "py" / "calc_theoretical_production.py").write_text(
            "import pandas as pd\n"
            "DC_MWP = {'Putnok': 10.0}\nPR = {'Putnok': [0.9] * 12}\nAC_LIMIT_MWH_15MIN = {'Putnok': 2.0}\n"
            "def production_mwh_15min(poa, dc, pr, ac):\n"
            "    return (poa / 1000 * dc * pr).clip(lower=0).div(4).clip(upper=ac)\n", encoding="utf-8")
        idx = pd.date_range("2026-11-01 12:00", periods=3, freq="15min")
        (plant / "data" / "irradiance forecast" / "2026" / "15min").mkdir(parents=True)
        poa = pd.DataFrame({"POA fcst I (DA) [W/m²]": [400.0, 1000.0, -5.0]}, index=idx); poa.index.name = "Date"
        poa.to_csv(plant / "data" / "irradiance forecast" / "2026" / "15min" / "Putnok.csv")
        (plant / "data" / "production forecast" / "2026" / "15min").mkdir(parents=True)
        pw = pd.DataFrame({"Production fcst I (DA) [kWh]": [111.0, 222.0, 333.0]}, index=idx); pw.index.name = "Date"
        pw.to_csv(plant / "data" / "production forecast" / "2026" / "15min" / "Putnok.csv")
        (plant / "data" / "theoretical production" / "2026" / "15min").mkdir(parents=True)
        th = pd.DataFrame({"Theoretical production [kWh]": [1, 2, 3]}, index=idx); th.index.name = "Date"
        th.to_csv(plant / "data" / "theoretical production" / "2026" / "15min" / "Putnok.csv")
        x = S.load_da_and_actual(plant, "Putnok")                      # 默认：POA + 公式
        # 400 W/m² × 10 MWp × 0.9 / 4 = 0.9 MWh = 900 kWh；1000 → 2.25 MWh 被 AC 限值 2.0 截断 = 2000 kWh；负值记 0
        assert list(x["I"].round(6)) == [900.0, 2000.0, 0.0] and list(x["AH"]) == [1, 2, 3]
        y = S.load_da_and_actual(plant, "Putnok", "power")             # 乙方 power
        assert list(y["I"]) == [111.0, 222.0, 333.0]
        try:
            S.load_da_and_actual(Path(t) / "nowhere", "Putnok")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("没有换算公式时应该报错")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} 个测试全部通过")
