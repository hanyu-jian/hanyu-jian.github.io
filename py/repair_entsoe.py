import argparse
import sys
from datetime import datetime, timedelta

import pandas as pd

from fetch_entsoe import (
    COUNTRY_CONFIG,
    DATA_DIR,
    ENTSOE_DISPLAY_TZ,
    FULL_START_DATE,
    RAW_DIR,
    _fmt_dt_hourly,
    build_generation_result,
    fetch_generation,
    fetch_load,
    merge_and_save_generation,
    merge_and_save_raw_generation,
    merge_and_save_raw_wide,
    merge_and_save_wide,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair one country's ENTSO-E load and generation data."
    )
    parser.add_argument(
        "--country",
        required=True,
        help="Country code to repair, for example DE.",
    )
    parser.add_argument(
        "--start",
        default=FULL_START_DATE,
        help="Repair start date, for example 2024-01-01. Defaults to FULL_START_DATE.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Repair end date. Defaults to yesterday.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    cc = args.country.upper()
    start_date = args.start or FULL_START_DATE
    end_date = args.end or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    cfg = COUNTRY_CONFIG.get(cc.lower())
    if cfg is None:
        print(f"[ERROR] Unknown country: {cc}")
        return 1

    bzn_eic = cfg["bzn_eic"]
    load_domain = cfg.get("load_eic", bzn_eic)
    gen_domain = cfg.get("gen_eic", bzn_eic)

    cutoff_naive = (
        pd.Timestamp(end_date).tz_localize(ENTSOE_DISPLAY_TZ) + timedelta(days=1)
    ).tz_localize(None)

    print(f"Repair {cc} load+generation: {start_date} -> {end_date}")
    print(f"  load_domain = {load_domain}")
    print(f"  gen_domain  = {gen_domain}")

    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    print(f"\n-> A65 load  eic={load_domain}")
    s_hourly, s_raw = fetch_load(load_domain, start_date, end_date)
    if s_hourly is not None:
        s_hourly = s_hourly[s_hourly.index < cutoff_naive]
        if s_raw is not None:
            s_raw = s_raw[s_raw.index < cutoff_naive]

        merge_and_save_wide({cc: s_hourly}, DATA_DIR / "load.csv", "load")
        if s_raw is not None:
            merge_and_save_raw_wide({cc: s_raw}, RAW_DIR / "A65.csv", "A65 load")

        raw_count = s_raw.notna().sum() if s_raw is not None else 0
        print(f"  OK {s_hourly.notna().sum()} hourly values | {raw_count} raw points")
    else:
        print("  [WARN] No load data")

    print(f"\n-> A75 gen  in_Domain={gen_domain}")
    hourly_dict, raw_dict = fetch_generation(gen_domain, start_date, end_date)
    result = build_generation_result(hourly_dict)

    gen_df = result.get("generation", pd.DataFrame())
    gen_rows = []
    if not gen_df.empty:
        gen_df = gen_df[gen_df.index < cutoff_naive]
        for cat in gen_df.columns:
            for dt, val in gen_df[cat].items():
                gen_rows.append(
                    {
                        "date": _fmt_dt_hourly(dt),
                        "country": cc,
                        "category": cat,
                        "value": val,
                    }
                )

    merge_and_save_generation(gen_rows, DATA_DIR / "generation")

    if raw_dict:
        raw_gen = {
            cc: {
                key: value[value.index < cutoff_naive]
                for key, value in raw_dict.items()
            }
        }
        merge_and_save_raw_generation(raw_gen, RAW_DIR / "generation")

    for field, fname in [("solar", "solar.csv"), ("wind", "wind.csv")]:
        series = result.get(field)
        if series is not None:
            series = series[series.index < cutoff_naive]
            merge_and_save_wide({cc: series}, DATA_DIR / fname, field)
            print(f"  OK {field} updated")

    solar_s = result.get("solar")
    wind_s = result.get("wind")
    if s_hourly is not None and (solar_s is not None or wind_s is not None):
        renewables = pd.Series(0.0, index=s_hourly.index)
        for renewable in [solar_s, wind_s]:
            if renewable is not None:
                renewables = renewables.add(
                    renewable.reindex(s_hourly.index, fill_value=0), fill_value=0
                )

        residual = s_hourly - renewables
        merge_and_save_wide(
            {cc: residual}, DATA_DIR / "residual_load.csv", "residual_load"
        )
        print("  OK residual_load updated")

    print("\nDone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
