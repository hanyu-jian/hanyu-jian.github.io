"""convert_open_meteo_cloud：多位置 CSV、UTC 小时 -> Budapest 本地小时、层按名字匹配、夏令时、追加合并。"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("conv", Path(__file__).resolve().parents[1] / "build" / "convert_open_meteo_cloud.py")
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)

LOCS = "location_id,latitude,longitude,elevation,utc_offset_seconds,timezone,timezone_abbreviation\n0,48.26,20.46,164.0,0,GMT,GMT\n1,48.26,20.61,135.0,0,GMT,GMT\n2,48.26,20.61,178.0,0,GMT,GMT\n\n"
HIST = "location_id,time,cloud_cover (%),cloud_cover_low (%),cloud_cover_high (%),cloud_cover_mid (%)\n"
DAY1 = "location_id,time,cloud_cover_previous_day1 (%)\n"


def write(tmp_path, head, rows, name="in.csv"):
    p = tmp_path / name
    p.write_text(LOCS + head + "".join(rows), encoding="utf-8")
    return p


def test_areas_and_winter_summer_offsets(tmp_path):
    p = write(tmp_path, HIST, ["0,2026-01-01T00:00,100,100,9,4\n", "1,2026-01-01T00:00,50,40,5,2\n", "0,2026-07-01T00:00,37,30,5,2\n"])
    got = conv.read_open_meteo(p)
    assert set(got) == {"2026-01-01 01:00", "2026-07-01 02:00"}                  # UTC+1 in winter, UTC+2 in summer
    assert got["2026-01-01 01:00"]["cloud_cover_west"] == 100.0 and got["2026-01-01 01:00"]["cloud_cover_east"] == 50.0
    assert got["2026-07-01 02:00"] == {"cloud_cover_west": 37.0, "cloud_cover_low_west": 30.0, "cloud_cover_high_west": 5.0, "cloud_cover_mid_west": 2.0}


def test_layers_by_header_name_not_position(tmp_path):
    # the Open-Meteo order is total, low, HIGH, MID
    got = conv.read_open_meteo(write(tmp_path, HIST, ["0,2026-01-01T00:00,55,1,2,3\n"]))["2026-01-01 01:00"]
    assert (got["cloud_cover_west"], got["cloud_cover_low_west"], got["cloud_cover_high_west"], got["cloud_cover_mid_west"]) == (55, 1, 2, 3)


def test_previous_day1_suffix_is_stripped(tmp_path):
    got = conv.read_open_meteo(write(tmp_path, DAY1, ["0,2025-12-31T23:00,30\n", "1,2025-12-31T23:00,44\n"]))
    assert got == {"2026-01-01 00:00": {"cloud_cover_west": 30.0, "cloud_cover_east": 44.0}}


def test_extra_locations_are_ignored(tmp_path):
    got = conv.read_open_meteo(write(tmp_path, DAY1, ["0,2026-01-01T00:00,30\n", "2,2026-01-01T00:00,99\n", "3,2026-01-01T00:00,98\n"]))
    assert got == {"2026-01-01 01:00": {"cloud_cover_west": 30.0}}


def test_dst_end_duplicate_hour_is_averaged_and_dst_start_gap_is_not_filled(tmp_path):
    got = conv.read_open_meteo(write(tmp_path, DAY1, ["0,2026-10-25T00:00,80\n", "0,2026-10-25T01:00,100\n", "0,2026-03-29T00:00,10\n", "0,2026-03-29T01:00,20\n"]))
    assert got["2026-10-25 02:00"]["cloud_cover_west"] == 90.0
    assert sorted(k for k in got if k.startswith("2026-03-29")) == ["2026-03-29 01:00", "2026-03-29 03:00"]


def test_single_location_file_without_location_table_is_refused(tmp_path):
    p = tmp_path / "old.csv"
    p.write_text("latitude,longitude\n48,20\n\ntime,cloud_cover (%)\n2026-01-01T00:00,10\n", encoding="utf-8")
    try:
        conv.read_open_meteo(p)
        assert False, "should have been refused"
    except SystemExit as e:
        assert "multi-location" in str(e)


def test_append_keeps_old_hours_and_columns_and_overwrites_same_hours(tmp_path, monkeypatch):
    out = tmp_path / "out.csv"
    for rows in (["0,2026-01-01T00:00,100\n", "0,2026-01-01T01:00,50\n"], ["0,2026-01-01T01:00,60\n", "1,2026-01-01T01:00,7\n", "0,2026-01-01T02:00,70\n"]):
        monkeypatch.setattr(sys, "argv", ["x", "day1", str(write(tmp_path, DAY1, rows)), str(out)])
        conv.main()
    assert out.read_text(encoding="utf-8").splitlines() == [
        "time,cloud_cover_west,cloud_cover_east",
        "2026-01-01 01:00,100,", "2026-01-01 02:00,60,7", "2026-01-01 03:00,70,"]


def test_committed_data_files_are_consistent():
    root = Path(__file__).resolve().parents[2] / "data" / "weather"
    for name, cols in (("cloud_day1.csv", 2), ("cloud_hist.csv", 8)):
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        assert lines[0].split(",")[0] == "time" and len(lines[0].split(",")) == cols + 1, name
        assert all(len(l.split(",")) == cols + 1 for l in lines[1:]), name
        assert all(l[13] == ":" and l[14:16] == "00" for l in (x.split(",")[0] for x in lines[1:])), "hourly local time"
