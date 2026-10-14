"""GloFAS v4 reforecasts: past forecasts issued as GloFAS would have issued them.

Reforecasts carry the forecast error that a live model faces, which the
reanalysis does not. The first research repository downloaded them from the
Copernicus CEMS Early Warning Data Store (``cems-glofas-reforecast``, version 4)
for the BDCTB river cell and saved one JSON file per issue date; this module
reads those files into a tidy table.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["issue", "valid", "lead_days", "member", "discharge_m3s"]


class ReforecastError(ValueError):
    """A reforecast file is inconsistent."""


def folder_cell(folder: Path) -> str:
    """Grid cell (``lat,lon_0_360``) of the first issue file in a folder."""
    first = next(iter(sorted(folder.glob("*.json"))), None)
    if first is None:
        raise ReforecastError(f"{folder}: no issue files")
    receipt = json.loads(first.read_text(encoding="utf-8"))["cell_selection_receipt"]
    return f"{receipt['latitude']},{receipt['longitude_0_360']}"


def read_normalized(folder: Path) -> pd.DataFrame:
    """One row per issue, valid day and ensemble member, sorted.

    All files must come from the same grid cell; duplicate records and
    non-finite or negative discharge are rejected.
    """
    rows, seen, cell = [], set(), None
    for path in sorted(folder.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        receipt = document["cell_selection_receipt"]
        this_cell = f"{receipt['latitude']},{receipt['longitude_0_360']}"
        if cell is None:
            cell = this_cell
        elif this_cell != cell:
            raise ReforecastError(f"{path.name}: cell {this_cell}, expected {cell}")
        for record in document["records"]:
            issue, valid = record["issue_time"][:10], record["valid_time"][:10]
            member, value = record["ensemble_member"], record["value"]
            if (issue, valid, member) in seen:
                raise ReforecastError(f"{path.name}: duplicate record {issue} {valid} {member}")
            if not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise ReforecastError(f"{path.name}: invalid discharge {value}")
            seen.add((issue, valid, member))
            lead = (pd.Timestamp(valid) - pd.Timestamp(issue)).days
            rows.append((issue, valid, lead, member, float(value)))
    table = pd.DataFrame(rows, columns=COLUMNS)
    return table.sort_values(["issue", "lead_days", "member"]).reset_index(drop=True)


def ensemble_summary(table: pd.DataFrame) -> pd.DataFrame:
    """Per issue and lead: member count, control run, mean and 10/50/90th percentiles."""

    def summarize(group: pd.DataFrame) -> pd.Series:
        values = group["discharge_m3s"].to_numpy()
        control = group.loc[group["member"] == "control", "discharge_m3s"]
        q10, q50, q90 = np.percentile(values, [10, 50, 90])
        return pd.Series(
            {
                "members": len(values),
                "control": float(control.iloc[0]) if len(control) else math.nan,
                "mean": float(values.mean()),
                "q10": float(q10),
                "q50": float(q50),
                "q90": float(q90),
            }
        )

    summary = table.groupby(["issue", "lead_days"]).apply(summarize, include_groups=False)
    summary = summary.reset_index()
    summary["members"] = summary["members"].astype(int)
    return summary
