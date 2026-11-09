import io
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fetch" / "mavir"))
import fetch_mavir_imbalance as M  # noqa: E402


def xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for _ in range(M.HEADER_ROW):
        ws.append(["junk"])
    ws.append(["Date", "Period", "System direction [MWh]"])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


FOLD = [("2025-10-26", "01:45 - 02:00", 1), ("2025-10-26", "02:00 - 02:15", 2), ("2025-10-26", "02:15 - 02:30", 3),
        ("2025-10-26", "02:30 - 02:45", 4), ("2025-10-26", "02:45 - 02+00", 5), ("2025-10-26", "02+00 - 02+15", 6),
        ("2025-10-26", "02+15 - 02+30", 7), ("2025-10-26", "02+30 - 02+45", 8), ("2025-10-26", "02+45 - 03:00", 9),
        ("2025-10-26", "03:00 - 03:15", 10)]


def test_parse_keeps_both_passes_in_order():
    df = M.parse(xlsx(FOLD))
    assert df["Date"].dt.strftime("%H:%M").tolist() == [
        "01:45", "02:00", "02:15", "02:30", "02:45", "02:00", "02:15", "02:30", "02:45", "03:00"]
    assert df["System direction [MWh]"].tolist() == list(range(1, 11))


def test_merge_replaces_old_first_pass_only_file_and_sorts_chronologically(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OUT_DIR", tmp_path)
    (tmp_path / "HU_2025.csv").write_text(
        "Date,System direction [MWh]\r\n26/10/2025 02:00,-0.235\r\n26/10/2025 02:15,-2.717\r\n"
        "26/10/2025 02:30,11.6\r\n26/10/2025 02:45,1.5\r\n26/10/2025 03:00,17.6\r\n", newline="")
    M.merge_into_files(M.parse(xlsx(FOLD)))
    out = pd.read_csv(tmp_path / "HU_2025.csv")
    assert len(out) == 10 and out["Date"].tolist()[0] == "26/10/2025 01:45"
    assert out["Date"].str[-5:].tolist() == ["01:45", "02:00", "02:15", "02:30", "02:45", "02:00", "02:15", "02:30", "02:45", "03:00"]
    assert out["System direction [MWh]"].tolist() == list(range(1, 11))
