"""给 gate/tests/worker_imbalance.test.mjs 用：造一批随机 15 分钟数据（含缺失），用 py/analysis/tokaj_da_idc_settlement.py 的
final_plan / compute / summarize 算出每天 × 16 档提前量的期望值，连同输入一起以 JSON 打印出来。Worker 的 JS 要和它逐项一致。"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "py" / "analysis"))
import tokaj_da_idc_settlement as S  # noqa: E402

rng = np.random.default_rng(20261112)
days = ["2026-11-02", "2026-11-03", "2026-11-04"]
idx = pd.DatetimeIndex([pd.Timestamp(d) + pd.Timedelta(minutes=15 * i) for d in days for i in range(96)], name="Date")
n = len(idx)
day_shape = np.clip(np.sin(np.pi * (np.arange(n) % 96 - 28) / 44), 0, None)
AH = day_shape * rng.uniform(400, 1500, n)
I = AH * rng.uniform(0.7, 1.5, n)
P, AA, PI = rng.uniform(-20, 400, n), rng.uniform(-20, 400, n), rng.uniform(-300, 600, n)
nan = lambda a, p: np.where(rng.random(n) < p, np.nan, a)
AH, I, AA, PI, P = nan(AH, 0.03), nan(I, 0.03), nan(AA, 0.04), nan(PI, 0.04), nan(P, 0.02)
leads = {}
for h in S.UST_HORIZONS:
    leads[h] = nan(AH_ := np.nan_to_num(day_shape * rng.uniform(400, 1500, n)) * (1 + (h / 240) * rng.normal(0, 0.4, n)), 0.35 + 0.4 * (h < 45))
view = pd.DataFrame({f"kWh h{h}": leads[h] for h in S.UST_HORIZONS}, index=idx)
view_text = view.to_csv(date_format="%Y-%m-%d %H:%M:%S")
read = lambda month, site: view_text
expected = {}
for L in S.UST_HORIZONS:
    fp = S.final_plan(read, "X", L, ["2026-11"])
    df = pd.DataFrame({"I": I, "AH": AH, "P": P, "AA": AA, "Pneg": PI}, index=idx)
    df["F"] = fp["F"].reindex(idx)
    d = S.compute(df)
    s = S.summarize(d, L, "D")
    expected[str(L)] = {str(k): {c: (None if pd.isna(v) else float(v)) for c, v in row.items()} for k, row in s.iterrows()}
f = lambda a: [None if pd.isna(x) else float(x) for x in a]
print(json.dumps({"index": [t.strftime("%Y-%m-%d %H:%M:%S") for t in idx], "AH": f(AH), "I": f(I), "P": f(P), "AA": f(AA), "PI": f(PI),
                  "leads": {str(h): f(leads[h]) for h in S.UST_HORIZONS}, "expected": expected}))
