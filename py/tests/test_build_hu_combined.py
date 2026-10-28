import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
import build_hu_combined as B  # noqa: E402


def frame(labels):
    return pd.DataFrame({"v": range(len(labels))}, index=pd.DatetimeIndex(labels))


def utc(df):
    return df.index.strftime("%Y-%m-%d %H:%M").tolist()


def test_single_fold_hour_goes_to_the_requested_pass():
    df = frame(["2025-10-26 01:45", "2025-10-26 02:00", "2025-10-26 03:00"])
    assert utc(B.to_utc(df, single_is_first=True)) == ["2025-10-25 23:45", "2025-10-26 00:00", "2025-10-26 02:00"]
    assert utc(B.to_utc(df, single_is_first=False)) == ["2025-10-25 23:45", "2025-10-26 01:00", "2025-10-26 02:00"]


def test_both_passes_in_source_are_ordered_first_then_second():
    df = frame(["2025-10-26 02:00", "2025-10-26 02:00", "2025-10-26 02:15", "2025-10-26 02:15"])
    for single_is_first in (True, False):
        assert utc(B.to_utc(df, single_is_first)) == [
            "2025-10-26 00:00", "2025-10-26 01:00", "2025-10-26 00:15", "2025-10-26 01:15"]


def test_spring_gap_has_no_rows_and_normal_days_unchanged():
    df = frame(["2025-03-30 01:45", "2025-03-30 03:00", "2025-07-01 12:00"])
    assert utc(B.to_utc(df, True)) == ["2025-03-30 00:45", "2025-03-30 01:00", "2025-07-01 10:00"]
