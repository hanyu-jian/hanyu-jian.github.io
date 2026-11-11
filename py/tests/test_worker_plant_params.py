"""gate/worker.js 里的 PLANT_PARAMS（DA 功率换算的参数）必须和 vcom 仓库 py/calc_theoretical_production.py 的
DC_MWP / PR / AC_LIMIT_MWH_15MIN 完全一致：理论发电量、超短期的 kWh 视图用的是那份 Python 常量，
网站上 DA 那条线是 Worker 里的 JS 换算，两边对不上图上就会出现口径差。

    python py/tests/test_worker_plant_params.py
（需要 vcom 仓库的 checkout：环境变量 TOKAJ_PLANT_DIR，默认 ../vcom；找不到就跳过。也兼容 pytest）
"""
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
WORKER = BASE / "gate" / "worker.js"


def js_params():
    m = re.search(r"// PLANT_PARAMS:BEGIN\s*const PLANT_PARAMS = (\{.*?\});\s*// PLANT_PARAMS:END", WORKER.read_text(encoding="utf-8"), re.S)
    assert m, "worker.js 里找不到 PLANT_PARAMS:BEGIN / END 标记"
    return json.loads(m.group(1))


def vcom_module():
    plant = Path(os.environ.get("TOKAJ_PLANT_DIR", str(BASE.parent / "vcom")))
    p = plant / "py" / "calc_theoretical_production.py"
    if not p.exists():
        raise unittest.SkipTest(f"找不到 {p}（用 TOKAJ_PLANT_DIR 指向 vcom 的 checkout）")
    spec = importlib.util.spec_from_file_location("calc_theoretical_production", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_params_match_vcom_python_constants():
    c = vcom_module()
    js = js_params()
    assert set(js) == set(c.DC_MWP) == set(c.PR) == set(c.AC_LIMIT_MWH_15MIN), f"场站不一致：{sorted(js)} vs {sorted(c.DC_MWP)}"
    for site, p in js.items():
        assert p["dc"] == c.DC_MWP[site], f"{site} DC 容量：worker {p['dc']} vs vcom {c.DC_MWP[site]}"
        assert p["ac"] == c.AC_LIMIT_MWH_15MIN[site], f"{site} AC 限值：worker {p['ac']} vs vcom {c.AC_LIMIT_MWH_15MIN[site]}"
        assert p["pr"] == list(c.PR[site]), f"{site} 月 PR 不一致"
        assert len(p["pr"]) == 12


def test_worker_params_block_is_well_formed():
    js = js_params()
    assert js and all(set(p) == {"dc", "pr", "ac"} and len(p["pr"]) == 12 for p in js.values())


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ran = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
            ran += 1
        except unittest.SkipTest as e:
            print("SKIP", f.__name__, "-", e)
    print(f"\n{ran}/{len(fns)} 个测试通过")
