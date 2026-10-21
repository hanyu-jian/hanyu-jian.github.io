import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fetch_enappsys_ida as f

SAMPLE = """Title line,,,
Date/Time,IDA1 Price,IDA1 Volume,IDA2 Price
2026-10-01 00:00,100.5,10,
2026-10-01 00:15,101,,95.5
"""

def test_parse_and_merge():
    df = f.parse(SAMPLE)
    assert list(df.columns) == ["datetime", "IDA1 Price", "IDA1 Volume", "IDA2 Price"]
    assert len(df) == 2 and df["IDA1 Price"].iloc[0] == 100.5 and df["IDA2 Price"].isna().iloc[0]
    newer = f.parse(SAMPLE.replace("101,,95.5", "102,3,96").replace("2026-10-01 00:15", "2026-10-01 00:30"))
    out = f.merge(df, newer)
    assert out["datetime"].tolist() == ["2026-10-01 00:00", "2026-10-01 00:15", "2026-10-01 00:30"]

def test_dayfirst_and_redact():
    df = f.parse("t\nDate,A\n01/10/2026 00:15,1\n")
    assert df["datetime"].iloc[0] == "2026-10-01 00:15"
    assert "secret" not in f.redact("url?pass=secret", "secret")

def test_cli_from_file():
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d, "s.csv"), Path(d, "o", "x.csv")
        src.write_text(SAMPLE)
        f.main(["--from-file", str(src), "--out", str(out)])
        f.main(["--from-file", str(src), "--out", str(out)])
        assert len(f.pd.read_csv(out)) == 2

if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_"): fn()
    print("ok")
