import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fetch" / "enappsys"))
import fetch_enappsys_id3 as f
from enappsys.services.chart import ChartCSV

HEAD = "DateTime,ID3 Index,ID3 Volume\n,EUR/MWh,MW\n"


def frame(body, head=HEAD):
    return f.to_frame(ChartCSV(head + body, "u", {}, "csv", f.CODE, None, None, "qh", "CET", "EUR", False))


def test_to_legacy_maps_columns_and_keeps_both_fold_passes():
    fr = frame("[25/10/2026 01:45],10,1\n[25/10/2026 02:00],11,2\n[25/10/2026 02:15],12,3\n"
               "[25/10/2026 02:00],13,4\n[25/10/2026 02:15],14,5\n")
    out = f.to_legacy(fr)
    assert list(out.columns) == f.COLS
    assert out["Date"].dt.strftime(f.DATE_FMT).tolist() == [
        "25/10/2026 01:45", "25/10/2026 02:00", "25/10/2026 02:15", "25/10/2026 02:00", "25/10/2026 02:15"]
    assert out["ID3"].tolist() == [10, 11, 12, 13, 14] and out["Vol"].tolist() == [1, 2, 3, 4, 5]


def test_lone_second_pass_gets_blank_first_pass_placeholder():
    fr = frame("[25/10/2026 01:45],10,1\n[25/10/2026 02:00],,\n[25/10/2026 02:15],12,3\n"
               "[25/10/2026 02:00],13,4\n[25/10/2026 02:15],14,5\n")
    out = f.to_legacy(fr)
    assert out["Date"].dt.strftime(f.DATE_FMT).tolist() == [
        "25/10/2026 01:45", "25/10/2026 02:00", "25/10/2026 02:00", "25/10/2026 02:15", "25/10/2026 02:15"]
    assert out["ID3"].isna().tolist() == [False, True, False, False, False]
    assert out["ID3"].tolist()[2] == 13.0                       # 空行在前，第二遍的数据在后


def test_merge_replaces_both_passes_of_a_label():
    old = f.pd.DataFrame({"Date": ["25/10/2026 02:00", "25/10/2026 02:00"], "ID3": ["", ""], "Vol": ["", ""]})
    new = f.to_legacy(frame("[25/10/2026 02:00],1,1\n[25/10/2026 02:00],2,2\n"))
    out = f.merge(old, new)
    assert out["ID3"].tolist() == ["1.0", "2.0"] or out["ID3"].tolist() == [1.0, 2.0] or list(map(float, out["ID3"])) == [1.0, 2.0]


def test_merge_keeps_legacy_text_overwrites_same_date_and_sorts(tmp_path):
    p = tmp_path / "HU_2026.csv"
    p.write_bytes(b"Date\tID3\tVol\r\n01/10/2026 00:00\t1.5\t10\r\n01/10/2026 00:15\t2.5\t11\r\n")
    new = f.to_legacy(frame("[01/10/2026 00:15],9,1\n[01/10/2026 00:30],4,1\n"))
    out = f.merge(f.read_old(p), new)
    f.write(out, p)
    assert p.read_bytes() == (b"Date\tID3\tVol\r\n01/10/2026 00:00\t1.5\t10\r\n"
                              b"01/10/2026 00:15\t9\t1\r\n01/10/2026 00:30\t4\t1\r\n")


def test_unmappable_columns_exit():
    fr = frame("[01/10/2026 00:00],1,1\n", "DateTime,Foo,Bar\n,EUR/MWh,MW\n")
    try:
        f.to_legacy(fr)
    except SystemExit as e:
        assert "ID3" in str(e)
    else:
        raise AssertionError


def test_real_manual_download_headers():
    head = "Date (CET),HU HUPX PRICE INDEX ID3,HU HUPX TRADED VOLUME INDEX ID3\n,EUR/MWh,MW\n"
    out = f.to_legacy(frame("[26/12/2025 16:15],117.92,579.1\n[26/12/2025 16:30],117.67,600\n", head))
    assert out["ID3"].tolist() == [117.92, 117.67] and out["Vol"].tolist() == [579.1, 600]
    assert out["Date"].dt.strftime(f.DATE_FMT).tolist() == ["26/12/2025 16:15", "26/12/2025 16:30"]


def test_roundtrip_of_real_file_is_byte_identical(tmp_path):
    src = Path(__file__).resolve().parents[2] / "data/enappsys/ID3/HU_2025.csv"
    out = tmp_path / "x.csv"
    f.write(f.read_old(src), out)
    assert out.read_bytes() == src.read_bytes()
