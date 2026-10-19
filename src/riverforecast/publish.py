"""Offline frozen BDCTB deployment: shared post-audit fitting and inference."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import sklearn

from riverforecast import data, features, live
from riverforecast.model import ConformalQuantileModel

L1_COLUMNS = [*features.COLUMNS, "obs_last", "obs_age", "obs_day_q"]
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
NAMO = 19.21
STATION = {"code": "BDCTB", "name": "Boca del Cerro"}
DISCLAIMER = (
    "Pronóstico modelado, no es una observación ni un aviso oficial. "
    "Consulte el semáforo oficial de IPCET."
)
ZONE = ZoneInfo("America/Mexico_City")


def fit_l1(train, stage, revealed, past) -> ConformalQuantileModel:
    """Exact experiment-13 L1 fit; exclude simulated rows without a live value."""
    sim = train.join(revealed)
    sim["obs_day_q"] = live.obs_day_discharge(sim, past)
    trainable = sim["obs_last"].notna().to_numpy()
    days = pd.DatetimeIndex(sim.loc[trainable, "valid"])
    return ConformalQuantileModel.fit(sim.loc[trainable, L1_COLUMNS], stage.loc[days], days)


def freeze(
    destination: str | Path = "models/bdctb", version: str = "bdctb-L1-postaudit-v1"
) -> dict:
    """Fit final L1/M1 on the experiment-13 deployment training rows only."""
    stage = data.stage("BDCTB")
    past = data.glofas_training_past("BDCTB")
    census = live.levels_census().loc["2021-08-03":"2026-10-17"]
    availability = live.tile_availability(census, pd.date_range("1997-01-01", "2022-12-31"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)
    summary = data.glofas_reforecast_summary("BDCTB")
    days = stage.index[(stage.index >= "1998-01-01") & (stage.index <= "2022-12-31")]
    models = {"live": {}, "glofas_only": {}}
    periods = {}
    for h in (1, 3, 7):
        train = features.build(summary, past, h).drop_duplicates("valid")
        train = train[train["valid"].isin(days)]
        models["glofas_only"][h] = ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
        )
        models["live"][h] = fit_l1(train, stage, revealed, past)
        periods[f"h{h}"] = {
            "start": f"{train.valid.min():%Y-%m-%d}",
            "end": f"{train.valid.max():%Y-%m-%d}",
            "rows": len(train),
        }
    metadata = {
        "model_version": version,
        "training_period": ["1998-01-01", "2022-12-31"],
        "training_rows": periods,
        "features": {"live": L1_COLUMNS, "glofas_only": features.COLUMNS},
        "conformal_margins": {
            mode: {f"h{h}": model.margin for h, model in fits.items()}
            for mode, fits in models.items()
        },
        "sklearn_version": sklearn.__version__,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "namo_m": NAMO,
        "station": STATION,
        "recipe": "experiment 13 / L39; shifted training past; simulated census availability",
    }
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, destination / "models.joblib", compress=3)
    (destination / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    size = sum(path.stat().st_size for path in destination.iterdir() if path.is_file())
    if size > 25_000_000:
        raise ValueError(f"artifact exceeds 25 MB: {size} bytes; stop without changing model")
    return {"artifact_bytes": size, **metadata}


class GlofasProvider(Protocol):
    def inputs(self, issue: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
        """Return ensemble summaries and archived day-0 past discharge."""
        ...


class CemsArchiveProvider:
    """Stored CEMS operational CSVs and day-0 archive; never accesses the network."""

    def __init__(self, raw: str | Path = data.RAW):
        self.raw = Path(raw)

    def inputs(self, issue):
        summary = data.glofas_operational_summary("BDCTB", self.raw, members=MEMBERS_11)
        past = data.glofas_day0("BDCTB", self.raw)
        return summary[summary.issue <= issue], past.loc[:issue]


class OpenMeteoProvider:
    """Reserved for T19's validated live source; deliberately unavailable."""

    def inputs(self, issue):
        raise NotImplementedError("Open-Meteo provider pending T19 source parity validation")


def empty_observations() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "stage_m", "table_last_modified"])


def read_observations(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = set(empty_observations().columns)
    if not required.issubset(frame.columns):
        raise ValueError("IPCET CSV requires date, stage_m, table_last_modified")
    return frame


def forecast(artifact, issue, observations, provider: GlofasProvider, generated_at=None) -> dict:
    """Serve without fitting or loading any training data; deterministic default timestamp."""
    issue = pd.Timestamp(issue)
    if issue.tzinfo is not None or issue != issue.normalize():
        raise ValueError("issue must be a timezone-naive calendar date")
    issue_time = issue.tz_localize(ZONE) + pd.Timedelta(hours=live.ISSUE_HOUR)
    artifact = Path(artifact)
    metadata = json.loads((artifact / "metadata.json").read_text())
    if metadata["sklearn_version"] != sklearn.__version__:
        raise ValueError("artifact scikit-learn version does not match runtime")
    models = joblib.load(artifact / "models.joblib")  # trusted repository artifact only
    obs = observations.copy()
    obs["date"] = pd.to_datetime(obs["date"])
    stamps = pd.to_datetime(obs["table_last_modified"], utc=True, errors="raise")
    obs["stage_m"] = pd.to_numeric(obs["stage_m"], errors="raise")
    if obs["date"].isna().any() or stamps.isna().any() or not np.isfinite(obs.stage_m).all():
        raise ValueError("IPCET inputs must have finite levels, dates and upload timestamps")
    obs = obs[(stamps <= issue_time) & (obs.date <= issue)]
    # The maintained CSV must already contain causally resolved/validated values.
    if obs.date.duplicated().any():
        raise ValueError("duplicate IPCET observation dates")
    series = obs.set_index("date")["stage_m"]
    revealed = live.real_revealed_values(
        series, pd.Series(series.index, index=series.index), pd.DatetimeIndex([issue])
    )
    value = revealed.iloc[0]
    mode = "glofas_only" if pd.isna(value.obs_last) else "live"
    summary, past = provider.inputs(issue)
    revealed["obs_day_q"] = live.obs_day_discharge(revealed, past)
    forecasts = {}
    for h in (1, 3, 7):
        table = features.build(summary, past, h)
        if issue not in table.index or (table.index == issue).sum() != 1:
            raise ValueError(f"missing GloFAS inputs for h{h}")
        row = table.loc[[issue]]
        columns = features.COLUMNS
        if mode == "live":
            row = row.join(revealed)
            columns = L1_COLUMNS
        quantiles = [float(q[0]) for q in models[mode][h].predict(row[columns])]
        if not np.isfinite(quantiles).all():
            raise ValueError("non-finite forecast")
        forecasts[f"h{h}"] = dict(zip(("q10", "q50", "q90"), quantiles, strict=True))
        forecasts[f"h{h}"].update(
            target_date=f"{issue + pd.Timedelta(days=h):%Y-%m-%d}",
            distance_to_namo_m=quantiles[1] - NAMO,
            namo_m=NAMO,
            data_kind="modeled_not_observed",
        )
    return {
        "schema": "bdctb-forecast/v2",
        "station": STATION,
        "issue_time": issue_time.isoformat(),
        "generated_at": issue_time.isoformat()
        if generated_at is None
        else pd.Timestamp(generated_at).isoformat(),
        "model_version": metadata["model_version"],
        "mode": mode,
        "live_input": None
        if mode == "glofas_only"
        else {
            "date": f"{value.obs_day:%Y-%m-%d}",
            "stage_m": float(value.obs_last),
            "age_days": int(value.obs_age),
        },
        "forecasts": forecasts,
        "disclaimer": DISCLAIMER,
    }
