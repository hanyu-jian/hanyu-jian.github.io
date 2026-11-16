"""convert_open_meteo_cloud：UTC 小时 -> Budapest 本地小时，夏令时，追加合并。"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("conv", Path(__file__).resolve().parents[1] / "build" / "convert_open_meteo_cloud.py")
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)

HEAD = "latitude,longitude,elevation,utc_offset_seconds,timezone,timezone_abbreviation\n48.2,20.4,164.0,0,GMT,GMT\n\n"
COLS = "time,cloud_cover (%),cloud_cover_low (%),cloud_cover_high (%),cloud_cover_mid (%)\n"


def write(tmp_path, rows, name="in.csv"):
    p = tmp_path / name
    p.write_text(HEAD + COLS + "".join(rows), encoding="utf-8")
    return p


def test_winter_and_summer_offsets(tmp_path):
    p = write(tmp_path, ["2026-01-01T00:00,100,100,9,4\n", "2026-07-01T00:00,37,30,5,2\n"])
    got = conv.read_open_meteo(p)
    assert set(got) == {"2026-01-01 01:00", "2026-07-01 02:00"}              # UTC+1 in winter, UTC+2 in summer
    assert got["2026-01-01 01:00"]["cloud_cover"] == 100.0 and got["2026-07-01 02:00"]["cloud_cover"] == 37.0


def test_all_four_layers_by_header_name(tmp_path):
    # the Open-Meteo order is total, low, HIGH, MID: the layers are matched by name, not by position
    got = conv.read_open_meteo(write(tmp_path, ["2026-01-01T00:00,55,1,2,3\n"]))
    assert got == {"2026-01-01 01:00": {"cloud_cover": 55.0, "cloud_cover_low": 1.0, "cloud_cover_high": 2.0, "cloud_cover_mid": 3.0}}


def test_dst_end_duplicate_hour_is_averaged(tmp_path):
    # 2026-10-25: local 02:00 happens twice (00:00 UTC and 01:00 UTC)
    got = conv.read_open_meteo(write(tmp_path, ["2026-10-25T00:00,80,0,0,0\n", "2026-10-25T01:00,100,0,0,0\n"]))
    assert got["2026-10-25 02:00"]["cloud_cover"] == 90.0 and list(got) == ["2026-10-25 02:00"]


def test_dst_start_has_no_missing_hour_filled(tmp_path):
    # 2026-03-29: local 02:00 does not exist (01:00 UTC -> 03:00 local)
    got = conv.read_open_meteo(write(tmp_path, ["2026-03-29T00:00,10,0,0,0\n", "2026-03-29T01:00,20,0,0,0\n"]))
    assert list(got) == ["2026-03-29 01:00", "2026-03-29 03:00"]


def test_append_keeps_old_hours_and_overwrites_same_hours(tmp_path, monkeypatch):
    out = tmp_path / "out.csv"
    for rows in (["2026-01-01T00:00,100,0,0,0\n", "2026-01-01T01:00,50,0,0,0\n"], ["2026-01-01T01:00,60,0,0,0\n", "2026-01-01T02:00,70,0,0,0\n"]):
        monkeypatch.setattr(sys, "argv", ["x", str(write(tmp_path, rows)), str(out)])
        conv.main()
    assert out.read_text(encoding="utf-8").splitlines() == [
        "time,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
        "2026-01-01 01:00,100,0,0,0", "2026-01-01 02:00,60,0,0,0", "2026-01-01 03:00,70,0,0,0"]


def test_old_two_column_file_is_still_read(tmp_path, monkeypatch):
    out = tmp_path / "out.csv"
    out.write_text("time,cloud_cover\n2026-01-01 01:00,100\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", str(write(tmp_path, ["2026-01-01T01:00,60,1,2,3\n"])), str(out)])
    conv.main()
    assert out.read_text(encoding="utf-8").splitlines()[1:] == ["2026-01-01 01:00,100,,,", "2026-01-01 02:00,60,1,3,2"]
