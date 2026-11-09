import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fetch" / "enappsys"))
import fetch_enappsys_ida as f
from enappsys.services.chart import ChartCSV

HEAD = "DateTime,IDA1 Price,IDA1 Volume,IDA2 Price\n,EUR/MWh,MW,EUR/MWh\n"


def chart(body):
    return ChartCSV(HEAD + body, "u", {}, "csv", f.CODE, None, None, "qh", "CET", "EUR", False)


def test_to_frame_two_header_rows_and_dst_fold():
    body = ("[25/10/2026 01:45],10,1,\n[25/10/2026 02:00],11,2,5\n[25/10/2026 02:15],12,3,6\n"
            "[25/10/2026 02:00],13,4,7\n[25/10/2026 02:15],14,5,8\n")
    df = f.to_frame(chart(body))
    assert list(df.columns) == [f.TIME_COL, "IDA1 Price (EUR/MWh)", "IDA1 Volume (MW)", "IDA2 Price (EUR/MWh)"]
    assert df[f.TIME_COL].iloc[1] == "2026-10-25T02:00:00+02:00"
    assert df[f.TIME_COL].iloc[3] == "2026-10-25T02:00:00+01:00"
    assert df[f.TIME_COL].is_unique


def test_merge_keeps_newest_and_sorts():
    a = f.to_frame(chart("[01/10/2026 00:00],1,1,\n[01/10/2026 00:15],2,1,\n"))
    b = f.to_frame(chart("[01/10/2026 00:15],9,1,3\n[01/10/2026 00:30],4,1,\n"))
    out = f.merge(a, b)
    assert out["IDA1 Price (EUR/MWh)"].tolist() == [1, 9, 4]
    assert len(f.merge(out, b)) == 3


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_"):
            fn()
    print("ok")
