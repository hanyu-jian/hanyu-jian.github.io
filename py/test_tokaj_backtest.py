"""tokaj_backtest.compute() 的小测试：期望值都是手算的，不依赖 Excel 或任何数据文件。

    python py/test_tokaj_backtest.py
（也兼容 pytest）
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tokaj_backtest as T  # noqa: E402

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
    close(r.fee, -0.4)                        # 缺电（AI>0）付费
    close(r.result, 6.6)


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


def test_surplus_earns_shortage_pays():
    s = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=120, Pneg=50, Ppos=50)).iloc[0]
    close(s.AI, -20); close(s.fee, 1.0)       # 余电 20 kWh × 50 EUR/MWh = +1 EUR
    n = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Pneg=50, Ppos=50)).iloc[0]
    close(n.AI, 20); close(n.fee, -1.0)
    # 负价格：余电反而要付钱
    neg = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=120, Pneg=-10, Ppos=-10)).iloc[0]
    close(neg.fee, -0.2)


def test_workbook_putnok_template_has_opposite_sign():
    r = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=80, Ppos=50, Pneg=50, AJ=5), "putnok").iloc[0]
    close(r.AO, 1.0); close(r.fee, 1.0)       # 缺电 20 kWh：Putnok 模板里是 +1（和默认相反）
    # 系统方向为 0 时 AM/AN（或 AO/AP）都会成立，按模板原样重复计一次
    z = T.compute(one("2026-10-01 12:00", I=100, J=100, AH=120, Ppos=50, Pneg=50, AJ=0), "putnok").iloc[0]
    close(z.fee, -2.0)


def test_invalid_and_uncovered():
    r = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=NAN, P=40)).iloc[0]
    assert not r.valid                        # 实测缺失 → 整格剔除
    ok = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=100, P=40)).iloc[0]
    assert ok.valid
    close(ok.uncovered_kWh, 10 + 10 + 5)      # |S|+|T|+|U| 都没有 IDA 价格覆盖
    covered = T.compute(one("2026-10-01 12:00", I=100, J=110, K=100, L=95, AH=100, V=1, W=1, X=1)).iloc[0]
    close(covered.uncovered_kWh, 0)
    assert not T.compute(one("2026-10-01 12:00", I=NAN, J=1, AH=1)).iloc[0].valid   # 日前预测缺失也剔除


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} 个测试全部通过")
