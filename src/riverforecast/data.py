"""Load the stored inputs of one station as aligned pandas objects."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from riverforecast import conagua, reforecast

RAW = Path("data/raw")


def stage(code: str, raw: Path = RAW) -> pd.Series:
    """Observed daily stage (m) from the CONAGUA hydrometric file."""
    path = raw / "conagua" / "hidros" / f"{code}.csv"
    data = conagua.parse_sih_csv(path.read_text(encoding="utf-8")).data
    return data["level_m"].dropna()


def glofas_reanalysis(code: str, raw: Path = RAW) -> pd.Series:
    path = raw / "glofas" / code / "consolidated_v4.csv"
    return pd.read_csv(path, index_col="date", parse_dates=True)["discharge_m3s"].dropna()


def glofas_reforecast_summary(code: str, raw: Path = RAW) -> pd.DataFrame:
    """Ensemble summary per issue and lead, with the valid day added."""
    table = pd.read_csv(raw / "glofas" / code / "reforecast_v4.csv")
    summary = reforecast.ensemble_summary(table)
    summary["issue"] = pd.to_datetime(summary["issue"])
    summary["valid"] = summary["issue"] + pd.to_timedelta(summary["lead_days"], unit="D")
    return summary


def imerg_basin_rain(code: str, raw: Path = RAW) -> pd.Series:
    path = raw / "imerg" / code / "daily.csv"
    return pd.read_csv(path, index_col="date", parse_dates=True)["basin_mean_mm"]
