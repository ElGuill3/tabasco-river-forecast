"""L65 offline extraction. Run: timeout 3600 uv run python -B experiments/16_extract_operational.py.

BDCTB is written only under .cache/t24, never over the reference data.
Open-Meteo longitudes label the same cells 0.05 degrees west of CEMS
(cems module documentation); latitude is unchanged. No network is used.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from riverforecast import cems, data

STATIONS = (
    "BDCTB",
    "PNVTB",
    "SAACP",
    "TAPTB",
    "MACTB",
    "GABTB",
    "OXOTB",
    "SJOCP",
    "TEATB",
    "PUYTB",
    "EPOTB",
    "GONTB",
)
CACHE = Path(".cache/t24")
KEYS = ["issue", "valid", "lead_days", "member"]
COMMAND = "timeout 3600 uv run python -B experiments/16_extract_operational.py"


def check_bdctb() -> dict:
    """Compare every extracted issue, independently of five-day file grouping."""

    def load(folder):
        table = pd.concat([pd.read_csv(p) for p in sorted(folder.glob("*.csv"))])
        assert list(table.columns) == cems.COLUMNS
        assert not table.duplicated(KEYS).any()
        return table.set_index(KEYS).sort_index()

    reference = load(data.RAW / "glofas/BDCTB/operational")
    extracted = load(CACHE / "BDCTB/operational")
    assert reference.index.equals(extracted.index), "BDCTB issue/member coverage differs"
    difference = np.abs(reference.discharge_m3s - extracted.discharge_m3s)
    # CSV round trips may differ at floating-point precision, never at source precision.
    assert np.allclose(reference.discharge_m3s, extracted.discharge_m3s, rtol=0, atol=1e-9)
    issues = extracted.index.get_level_values("issue").unique()
    assert len(issues) >= 5
    return {
        "status": "passed",
        "issues_compared": len(issues),
        "rows_compared": len(reference),
        "first_issue": min(issues),
        "last_issue": max(issues),
        "max_absolute_difference_m3s": float(difference.max()),
        "tolerance_m3s": 1e-9,
        "explanation": "Same CEMS cell; file grouping and member ordering are ignored, not values.",
    }


def cell_rows(ds, lat: float, lon: float) -> list[tuple]:
    """Same rows as ``cems._cell_rows``, read with one array load per cell.

    ``cems._cell_rows`` selects every value separately, which made the first
    extraction run stall; the BDCTB check below compares the result with the
    reference files written by the library reader.
    """
    longitude = lon % 360 if float(ds.longitude.min()) >= 0 else (lon + 180) % 360 - 180
    if not (
        np.isclose(ds.latitude.values, lat).any()
        and np.isclose(ds.longitude.values, longitude).any()
    ):
        raise cems.CemsError(f"cell {lat},{longitude} not in the archive")
    values = ds["dis24"].sel(latitude=lat, longitude=longitude, method="nearest")
    if "number" not in values.dims:
        values = values.expand_dims(number=[None])
    values = values.transpose("number", "forecast_reference_time", "forecast_period")
    array = values.values
    if not np.isfinite(array).all():
        raise cems.CemsError(f"missing discharge at {lat},{longitude}")
    rows = []
    for i, member in enumerate(values["number"].values):
        label = "control" if member is None else f"perturbed_{int(member):02d}"
        for j, issue in enumerate(values["forecast_reference_time"].values):
            issue_day = pd.Timestamp(issue)
            for k, period in enumerate(values["forecast_period"].values):
                lead = int(pd.Timedelta(period).days)
                valid = issue_day + pd.Timedelta(days=lead)
                rows.append(
                    (
                        f"{issue_day:%Y-%m-%d}",
                        f"{valid:%Y-%m-%d}",
                        lead,
                        label,
                        float(array[i, j, k]),
                    )
                )
    return rows


def main() -> None:
    start = time.monotonic()
    archives = sorted((data.RAW / "glofas/cems/operational").glob("*.zip"))
    assert len(archives) == 253, "L65 requires the complete 253-file archive"
    CACHE.mkdir(parents=True, exist_ok=True)
    cells, folders, seen = {}, {}, {}
    for code in STATIONS:
        cell = json.loads((data.RAW / "glofas" / code / "cell.json").read_text())
        cells[code] = (cell["latitude"], round(cell["longitude"] + 0.05, 3))
        folders[code] = (
            CACHE / code if code == "BDCTB" else data.RAW / "glofas" / code
        ) / "operational"
        folders[code].mkdir(parents=True, exist_ok=True)
        seen[code] = set()
    # One pass over the archive; every station cell is read from each opened file.
    for n, archive in enumerate(archives, 1):
        rows = {code: [] for code in STATIONS}
        with zipfile.ZipFile(archive) as zipped:
            for name in sorted(zipped.namelist()):
                if not name.endswith(".nc"):
                    continue
                with xr.open_dataset(io.BytesIO(zipped.read(name)), engine="h5netcdf") as ds:
                    ds = ds[["dis24"]].load()
                    for code, (lat, lon) in cells.items():
                        rows[code].extend(cell_rows(ds, lat, lon))
        for code in STATIONS:
            table = pd.DataFrame(rows[code], columns=cems.COLUMNS)
            if table.empty:
                raise cems.CemsError(f"no discharge rows in {archive.name}")
            table = table.sort_values(["issue", "lead_days", "member"]).reset_index(drop=True)
            assert not table.duplicated(KEYS).any()
            for issue, part in table.groupby("issue", sort=True):
                assert issue not in seen[code], f"duplicate issue across archives: {code} {issue}"
                seen[code].add(issue)
                assert set(part.lead_days) == {1, 3, 7}
                assert len(part) == 153 and part.groupby("lead_days").member.nunique().eq(51).all()
                part.to_csv(folders[code] / f"{issue}.csv", index=False)
        if n % 25 == 0 or n == len(archives):
            print(f"{n}/{len(archives)} archives ({time.monotonic() - start:.0f}s)", flush=True)
    counts = {
        code: {"issues": len(seen[code]), "cems_cell": list(cells[code])} for code in STATIONS
    }
    result = {
        "command": COMMAND,
        "archives": len(archives),
        "stations": counts,
        "bdctb_check": check_bdctb(),
        "runtime_seconds": time.monotonic() - start,
    }
    (CACHE / "extraction.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
