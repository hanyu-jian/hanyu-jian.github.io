import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fetch" / "enappsys"))
import fetch_enappsys_afrr_capacity as f
from enappsys.services.chart import ChartCSV


def test_codes():
    assert f.CODES == {"up": "hu/elec/ancillary/afrr/summary/up", "down": "hu/elec/ancillary/afrr/summary/down"}


def test_output_layout_and_merge(tmp_path, monkeypatch):
    head = ("DateTime,ACCEPTED PRICE MIN (DAILY),WEIGHTED AVERAGE PRICE UP (HOURLY),WEIGHTED AVERAGE PRICE UP (DAILY),"
            "WEIGHTED AVERAGE PRICE UP (MONTHLY),"
            "VOLUME UP (HOURLY),VOLUME UP (DAILY),VOLUME UP (MONTHLY),TOTAL VOLUME (UP)\n"
            ",EUR/MW/h,EUR/MW/h,EUR/MW/h,EUR/MW/h,MW,MW,MW,MW\n")

    class Chart:
        def get(self, fmt, code, **kw):
            return ChartCSV(head + "[28/10/2026 10:45],0.1,,0.2,11.4,,50,200,250\n[28/10/2026 11:00],0.2,,0.2,11.4,,60,200,260\n",
                            "u", {}, "csv", code, None, None, "qh", "CET", "EUR", False)

    class Client:
        chart = Chart()

    monkeypatch.setattr(f, "EnAppSys", lambda **kw: Client())
    monkeypatch.setenv("ENAPPSYS_USER", "u")
    monkeypatch.setenv("ENAPPSYS_PASS", "p")
    for _ in range(2):  # 重复运行不应产生重复行
        f.main(["--start", "2026-10-28", "--end", "2026-10-29", "--out-root", str(tmp_path)])
    for d in ("up", "down"):
        text = (tmp_path / d / "15min" / "HU_2026.csv").read_text().splitlines()
        assert text[0] == ("datetime,WEIGHTED AVERAGE PRICE UP (HOURLY) (EUR/MW/h),WEIGHTED AVERAGE PRICE UP (DAILY) (EUR/MW/h),"
                           "WEIGHTED AVERAGE PRICE UP (MONTHLY) (EUR/MW/h),VOLUME UP (HOURLY) (MW),"
                           "VOLUME UP (DAILY) (MW),VOLUME UP (MONTHLY) (MW),TOTAL VOLUME (UP) (MW)")
        assert len(text) == 3


def test_fill_missing():
    import pandas as pd
    nan = float("nan")
    df = pd.DataFrame({"datetime": list("abcd"), "p1": [nan, 1.5, nan, nan], "p2": nan, "p3": nan, "vh": nan,
                       "vd": [nan, 5.0, nan, nan], "vm": [nan, nan, 7.0, nan], "t": 0})
    out = f.fill_missing(df)
    assert out.iloc[0, 1:7].tolist() == ["-"] * 6
    assert out["p1"].tolist()[:3] == ["-", 1.5, "-"] and out["vm"].tolist()[:3] == ["-", "-", 7.0]
    assert out.iloc[3, 1:7].isna().all()  # 最后一个有值之后（未来）不填


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
