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


def test_download_tries_the_newest_folder_first_and_falls_back_to_the_old_one(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        return b"PK-ok" if "/358398864/" in url and "20261008_20261012" in url else None
    monkeypatch.setattr(M, "get", fake_get)
    monkeypatch.delenv("MAVIR_BASES", raising=False)
    from datetime import date
    assert M.download(date(2026, 10, 8)) == b"PK-ok"
    # 发布日 +1 … +4 都试了新目录再试旧目录；第 4 次（20261012）在旧目录命中
    assert calls[0] == "https://www.mavir.hu/documents/187408084/360748237/Pos_ImbalancePrice_ENG_preliminary_20261008_20261009.xlsx"
    assert calls[1].startswith("https://www.mavir.hu/documents/187408084/358398864/") and "20261009" in calls[1]
    assert calls[-1].endswith("preliminary_20261008_20261012.xlsx") and "/358398864/" in calls[-1]
    assert len(calls) == 8


def test_download_new_folder_hit_stops_at_once(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "get", lambda url: (calls.append(url), b"PK-ok" if "/360748237/" in url and "20261108_20261112" in url else None)[1])
    monkeypatch.delenv("MAVIR_BASES", raising=False)
    from datetime import date
    assert M.download(date(2026, 11, 8)) == b"PK-ok"
    assert calls[-1] == "https://www.mavir.hu/documents/187408084/360748237/Pos_ImbalancePrice_ENG_preliminary_20261108_20261112.xlsx"
    assert len(calls) == 2 * 3 + 1          # 发布日 +1、+2、+3 两个目录都没有，+4 在新目录命中


def test_bases_from_the_environment_come_first_and_accept_folder_numbers(monkeypatch):
    monkeypatch.setenv("MAVIR_BASES", "999111, https://example.test/docs/55/ ,360748237")
    assert M.bases() == ["https://www.mavir.hu/documents/187408084/999111/", "https://example.test/docs/55/",
                         "https://www.mavir.hu/documents/187408084/360748237/", "https://www.mavir.hu/documents/187408084/358398864/"]
