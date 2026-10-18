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


def glofas_day0(code: str, raw: Path = RAW) -> pd.Series:
    """Day-0 archived forecast discharge (m3/s), like ``glofas_reanalysis``.

    Operationally the ``past_*`` features come from this series: the value the
    forecast itself assigned to the issue day, available up to today.
    """
    path = raw / "glofas" / code / "forecast_v4.csv"
    return pd.read_csv(path, index_col="date", parse_dates=True)["discharge_m3s"].dropna()


def glofas_training_past(code: str, raw: Path = RAW) -> pd.Series:
    """Training past discharge (m3/s): the reanalysis shifted one day forward.

    The reanalysis and the day-0 archive label the same daily mean one day
    apart (D3, L39): on their 730-day overlap, ``day0[d]`` matches
    ``reanalysis[d-1]`` with a median |log difference| of 0.0115, against
    0.0323 for ``reanalysis[d]``. Features built on the reanalysis must
    therefore be shifted so that their labels match the day-0 series used in
    operation: ``past[d] = reanalysis[d-1]``. Fit every deployment model on
    this series; build operational features on :func:`glofas_day0`.
    """
    return glofas_reanalysis(code, raw).sort_index().shift(1, freq="D")


def glofas_operational_summary(
    code: str, raw: Path = RAW, members: list[str] | None = None
) -> pd.DataFrame:
    """Ensemble summary of the real operational forecasts, with the valid day.

    One row per issue and lead, same shape as ``glofas_reforecast_summary``.
    ``members`` optionally keeps only the given member labels; reforecasts
    carry 11 members, so summaries comparable with the training data must pass
    control plus ``perturbed_01``..``perturbed_10``. Duplicate (issue,
    lead_days, member) rows are rejected.
    """
    folder = raw / "glofas" / code / "operational"
    table = pd.concat(
        [pd.read_csv(path) for path in sorted(folder.glob("*.csv"))], ignore_index=True
    )
    if members is not None:
        table = table[table["member"].isin(members)]
    duplicated = table.duplicated(["issue", "lead_days", "member"], keep="first")
    if duplicated.any():
        first = table.loc[duplicated].iloc[0]
        raise ValueError(
            "duplicate operational rows: issue "
            f"{first['issue']} lead {first['lead_days']} member {first['member']}"
        )
    summary = reforecast.ensemble_summary(table)
    summary["issue"] = pd.to_datetime(summary["issue"])
    summary["valid"] = summary["issue"] + pd.to_timedelta(summary["lead_days"], unit="D")
    return summary


def imerg_basin_rain(code: str, raw: Path = RAW) -> pd.Series:
    """Daily basin-mean IMERG rainfall (mm), sorted by date (rows are appended unordered)."""
    path = raw / "imerg" / code / "daily.csv"
    rain = pd.read_csv(path, index_col="date", parse_dates=True)["basin_mean_mm"]
    duplicated = rain.index[rain.index.duplicated()]
    if len(duplicated):
        raise ValueError(f"duplicate dates in {path}: {duplicated[0]:%Y-%m-%d}")
    return rain.sort_index()
