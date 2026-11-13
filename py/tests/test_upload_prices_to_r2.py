"""py/build/upload_prices_to_r2.py 的 slim()：只留三列价格、原样保留行（含夏令时回拨日重复的小时）、缺列直接报错。
    python py/tests/test_upload_prices_to_r2.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
import upload_prices_to_r2 as U  # noqa: E402

HEADER = "Date,DA Price (EUR/MWh),IDA1 Price (HUPX) (EUR/MWh),ID3 Price (EUR/MWh),System direction (MWh),Imbalance Price (EUR/MWh)\n"


def write(text):
    f = Path(tempfile.mkdtemp()) / "HU_2026.csv"
    f.write_text(text, encoding="utf-8")
    return f


def test_keeps_only_the_three_price_columns_in_order():
    out = U.slim(write(HEADER + "2026-01-01 00:00,104.33,96.05,108.9,-4.69,180.733497665\n2026-01-01 00:15,98.99,,108.23,-2.37,\n")).splitlines()
    assert out[0] == "Date,DA Price (EUR/MWh),ID3 Price (EUR/MWh),Imbalance Price (EUR/MWh)"
    assert out[1] == "2026-01-01 00:00,104.33,108.9,180.733497665"
    assert out[2] == "2026-01-01 00:15,98.99,108.23,"          # 缺失保持为空


def test_keeps_the_repeated_hour_of_the_fall_back_day():
    rows = "".join(f"2026-10-25 02:{m:02d},1,2,3,4,5\n" for m in (0, 15)) * 2
    out = U.slim(write(HEADER + rows)).splitlines()
    assert len(out) == 1 + 4 and out[1].startswith("2026-10-25 02:00") and out[3].startswith("2026-10-25 02:00")


def test_missing_column_is_an_error():
    try:
        U.slim(write("Date,DA Price (EUR/MWh)\n2026-01-01 00:00,1\n"))
    except SystemExit as e:
        assert "缺少列" in str(e)
    else:
        raise AssertionError("expected SystemExit")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} 个测试全部通过")
