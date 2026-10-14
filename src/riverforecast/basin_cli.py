"""Trace a station's upstream basin from HydroBASINS level 12.

    uv run python -m riverforecast.basin_cli trace BDCTB --lat 17.43 --lon -91.492 \\
        --hydrobasins data/raw/hydrobasins/hybas_na_lev12_v1c.zip

HydroBASINS (Lehner & Grill 2013) is published at
https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_na_lev12_v1c.zip.
The basin is written to ``data/basins/<CODE>.geojson`` and versioned.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from riverforecast.basin import trace_basin

DEFAULT_OUT = Path("data/basins")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.basin_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    trace = commands.add_parser("trace", help="trace and store a station's upstream basin")
    trace.add_argument("code")
    trace.add_argument("--lat", type=float, required=True)
    trace.add_argument("--lon", type=float, required=True)
    trace.add_argument("--hydrobasins", type=Path, required=True)
    trace.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    basin, outlet = trace_basin(args.hydrobasins, args.lat, args.lon)
    basin["station"] = args.code
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{args.code}.geojson"
    basin.to_file(target, driver="GeoJSON", COORDINATE_PRECISION=5)
    west, south, east, north = basin.total_bounds
    row = basin.iloc[0]
    print(
        f"{args.code}: outlet {outlet}, {row.subbasins} sub-basins, {row.area_km2:,.0f} km², "
        f"bounds {west:.2f},{south:.2f},{east:.2f},{north:.2f} -> {target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
