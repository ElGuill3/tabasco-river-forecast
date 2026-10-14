"""Command line for GloFAS discharge at a station's river cell.

    uv run python -m riverforecast.glofas_cli fetch BDCTB --lat 17.43 --lon -91.492

Reads the station's observed discharge from ``<root>/conagua/hidros/<CODE>.csv``,
chooses the river cell among the 5 x 5 cells around the station, and stores
``<root>/glofas/<CODE>/{cell.json,consolidated_v4.csv,forecast_v4.csv}``. The
candidate cells are kept in ``candidates_consolidated_v4.csv`` and reused on the
next run; delete that file to download them again.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from riverforecast import conagua, glofas

DEFAULT_ROOT = Path("data/raw")
FIRST_DAY = "1997-01-01"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.glofas_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="raw data folder")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="choose the river cell and download discharge")
    fetch.add_argument("code")
    fetch.add_argument("--lat", type=float, required=True)
    fetch.add_argument("--lon", type=float, required=True)
    fetch.add_argument("--start", default=FIRST_DAY)
    fetch.add_argument("--end", default=f"{date.today():%Y-%m-%d}")
    args = parser.parse_args(argv)

    station_file = args.root / "conagua" / "hidros" / f"{args.code}.csv"
    observed = conagua.parse_sih_csv(station_file.read_text(encoding="utf-8")).data
    folder = args.root / "glofas" / args.code
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / "candidates_consolidated_v4.csv"
    if stored.exists():
        candidates = pd.read_csv(stored, index_col="date", parse_dates=True)
    else:
        candidates = glofas.fetch_discharge(
            glofas.neighbourhood(args.lat, args.lon),
            args.start,
            args.end,
            model="consolidated_v4",
            get_json=glofas._get_json,
        )
        candidates.to_csv(stored, float_format="%.3f")
    choice = glofas.select_river_cell(
        candidates, observed["discharge_m3s"], station=(args.lat, args.lon)
    )
    print(
        f"{args.code}: river cell {choice.cell} (correlation {choice.correlation:.3f} "
        f"at lag {choice.lag_days} days, mean ratio {choice.mean_ratio:.2f}, {choice.days} days)"
    )

    lat, lon = (float(part) for part in choice.cell.split(","))
    (folder / "cell.json").write_text(choice.to_json(), encoding="utf-8")

    series = {}
    for model in ("consolidated_v4", "forecast_v4"):
        table = glofas.fetch_discharge(
            [(lat, lon)], args.start, args.end, model=model, get_json=glofas._get_json
        )
        values = table[choice.cell].rename("discharge_m3s")
        values.to_frame().to_csv(folder / f"{model}.csv", float_format="%.3f")
        series[model] = values.dropna()

    reanalysis, forecast = series["consolidated_v4"], series["forecast_v4"]
    print(f"{args.code}: consolidated_v4 {len(reanalysis)} days, forecast_v4 {len(forecast)} days")
    shared = reanalysis.index.intersection(forecast.index)
    if len(shared):
        ratio = forecast[shared].mean() / reanalysis[shared].mean()
        print(
            f"{args.code}: forecast_v4 / consolidated_v4 mean ratio {ratio:.2f} "
            f"on {len(shared)} shared days"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
