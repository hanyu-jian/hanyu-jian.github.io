import os
import sys
from pathlib import Path

import pandas as pd

os.environ.setdefault("ENTSOE_TOKEN", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fetch" / "entsoe"))
import fetch_imbalance as F  # noqa: E402

TZ = "Europe/Brussels"


def series(start_utc, periods, first_value=0.0):
    idx = pd.date_range(start_utc, periods=periods, freq="15min", tz="UTC").tz_convert(TZ)
    return pd.Series([first_value + i for i in range(periods)], index=idx)


def read(path):
    return pd.read_csv(path, index_col=0)


def test_fall_back_day_keeps_both_passes_in_raw_file(tmp_path):
    # 2025-10-26 00:00 UTC = 02:00 CEST（第一遍），01:00 UTC = 02:00 CET（第二遍）
    s = series("2025-10-25 22:00", 16)                # 当地 00:00 .. 03:45
    F.merge_and_save_raw_wide({"HU": s}, tmp_path / "A85.csv", "t")
    out = read(tmp_path / "2025" / "A85.csv")
    assert out.index.tolist()[8:16] == ["2025/10/26 2:00", "2025/10/26 2:15", "2025/10/26 2:30", "2025/10/26 2:45"] * 2
    assert out["HU"].tolist()[8:16] == [8, 9, 10, 11, 12, 13, 14, 15]          # 第一遍在前，第二遍在后
    assert len(out) == 16


def test_rerun_overwrites_by_pass_and_old_single_row_counts_as_second_pass(tmp_path):
    # 旧文件：回拨日 02:00~02:45 只有一遍（旧代码留的是第二遍）
    legacy = pd.DataFrame({"HU": [1.0, 2.0, 3.0, 4.0, 5.0]},
                          index=["2025/10/26 1:45", "2025/10/26 2:00", "2025/10/26 2:15", "2025/10/26 2:30", "2025/10/26 2:45"])
    legacy.index.name = "Date"
    p = tmp_path / "2025" / "A85.csv"
    p.parent.mkdir()
    legacy.to_csv(p)
    # 只补第二遍（UTC+1）的两个点：旧的单行应当被当作第二遍更新，而不是第一遍
    s = series("2025-10-26 01:00", 2, first_value=100)
    F.merge_and_save_raw_wide({"HU": s}, tmp_path / "A85.csv", "t")
    out = read(p)
    assert out.index.tolist() == ["2025/10/26 1:45", "2025/10/26 2:00", "2025/10/26 2:15", "2025/10/26 2:30", "2025/10/26 2:45"]
    assert out["HU"].tolist() == [1.0, 100.0, 101.0, 4.0, 5.0]


def test_hourly_mean_of_fold_hour_uses_eight_points_and_spring_gap_is_empty():
    s = series("2025-10-25 22:00", 16)
    local = s.copy()
    local.index = local.index.tz_localize(None)
    h = local.groupby(local.index.floor("h")).mean()
    assert h.loc["2025-10-26 02:00"] == sum(range(8, 16)) / 8                  # 两遍共 8 个点
    spring = series("2025-03-29 22:00", 16)               # 2025-03-30 当地 00:00 .. 02:45 只有 23:00~... 少一小时
    assert len(spring) == 16 and (spring.index.tz_localize(None).floor("h") == pd.Timestamp("2025-03-30 02:00")).sum() == 0
