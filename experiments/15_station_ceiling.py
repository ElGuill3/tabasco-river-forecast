"""Experiment 15: L63 station ceilings, development only; score once, no tuning.

Run offline from the repository root:
    timeout 3600 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
        uv run python experiments/15_station_ceiling.py

Only date prefixes are inspected at the 2017 boundary: the loaders stop before
reading the first post-development measurement. No test values are loaded.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from riverforecast import conagua, features, live
from riverforecast import evaluation as ev
from riverforecast.baselines import Climatology
from riverforecast.model import ConformalQuantileModel
from riverforecast.publish import L1_COLUMNS, fit_l1

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
LEADS = (1, 3, 7)
RAW = Path("data/raw")
OUT = Path("docs/results")
CUTOFF = pd.Timestamp(ev.DEVELOPMENT[1])
INTERPRETATION = "unlikely to beat persistence at that lead even with perfect GloFAS"
COMMAND = (
    "timeout 3600 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 "
    "uv run python experiments/15_station_ceiling.py"
)


class MissingData(ValueError):
    """A required input or fold cannot be constructed without imputation."""


def assert_development_or_earlier(days) -> None:
    """Hard boundary for every loaded, fitted, predicted and scored date."""
    days = pd.DatetimeIndex(days)
    assert not days.hasnans and (days <= CUTOFF).all(), "date after 2016-12-31"


def bounded_csv(path: Path, sih: bool = False) -> str:
    """Read a chronological CSV only through 2016, not its later measurements.

    SIH has a metadata preamble and two supported layouts. Pass retained SIH
    text to the library's strict parser; do not reimplement numeric parsing.
    The ten-character date is read separately from the measurement fields so
    even the first 2017 measurement is never read/decoded into a model input.
    """
    lines = []
    with path.open(encoding="utf-8-sig") as stream:
        keyed = False
        if sih:
            for line in stream:
                lines.append(line)
                fields = [field.strip() for field in line.split(",")[:2]]
                if fields[0] == "Fecha" or fields == ["Estacion", "Fecha"]:
                    keyed = fields[0] == "Estacion"
                    break
            else:
                raise MissingData("SIH has no date header")
        else:
            lines.append(stream.readline())
        previous = None
        while True:
            prefix = ""
            if keyed:
                # Station key precedes the date in the Chiapas layout.
                while char := stream.read(1):
                    prefix += char
                    if char == ",":
                        break
                if not prefix:
                    break
            date = stream.read(10)
            if not date:
                break
            day = pd.Timestamp(date.strip().replace("/", "-"))
            if day > CUTOFF:
                break  # do not read the measurement fields of this row
            assert_development_or_earlier([day])
            if previous is not None and day <= previous:
                raise MissingData("input dates are not strictly increasing")
            previous = day
            lines.append(prefix + date + stream.readline())
    return "".join(lines)


def load_station(code: str) -> tuple[pd.Series, pd.Series]:
    station = conagua.parse_sih_csv(bounded_csv(RAW / "conagua/hidros" / f"{code}.csv", True))
    if "level_m" not in station.data:
        raise MissingData("SIH has no daily stage column")
    stage = station.data["level_m"].dropna()
    discharge = pd.read_csv(
        io.StringIO(bounded_csv(RAW / "glofas" / code / "consolidated_v4.csv")),
        index_col="date",
        parse_dates=True,
    )["discharge_m3s"].dropna()
    # Log-discharge features need positive, finite inputs. Never fill targets.
    stage = stage[np.isfinite(stage)]
    discharge = discharge[np.isfinite(discharge) & (discharge > 0)]
    assert_development_or_earlier(stage.index)
    assert_development_or_earlier(discharge.index)
    if stage.empty or discharge.empty:
        raise MissingData("no observed stage or positive reanalysis discharge through 2016")
    return stage, discharge


def ceiling_table(stage: pd.Series, discharge: pd.Series, lead: int):
    """Experiment-13 features, replacing the ensemble by perfect target discharge.

    All ensemble summaries equal the target-day reanalysis (zero spread).
    Past discharge retains experiment 13's one-day label shift (L39 D3);
    obs_day_q comes from that same series (L39 D1). Stage is always age zero.
    """
    past = discharge.shift(1, freq="D").loc[:CUTOFF]
    assert_development_or_earlier(past.index)
    summary = pd.DataFrame({"valid": discharge.index, "lead_days": lead})
    summary["issue"] = summary["valid"] - pd.Timedelta(days=lead)
    for column in ("control", "mean", "q10", "q50", "q90"):
        summary[column] = discharge.to_numpy()
    assert_development_or_earlier(summary["issue"])
    assert_development_or_earlier(summary["valid"])
    table = features.build(summary, past, lead)
    table = table[
        table["valid"].between(*ev.DEVELOPMENT)
        & table["valid"].isin(stage.index)
        & table.index.isin(stage.index)
    ]
    # Reuse the library's reveal/age rules; only exact issue-day readings enter
    # this ceiling, never an older observation carried through a missing day.
    revealed = live.real_revealed_values(
        stage, pd.Series(stage.index, index=stage.index), table.index
    )
    assert (revealed["obs_age"] == 0).all()
    return table, revealed, past


def station_lead(stage: pd.Series, discharge: pd.Series, lead: int) -> dict:
    table, revealed, past = ceiling_table(stage, discharge, lead)
    days = pd.DatetimeIndex(table["valid"])
    assert_development_or_earlier(days)
    if days.empty:
        raise MissingData("no common target, issue-day stage and discharge feature rows")
    folds = list(ev.year_folds(days, years_per_fold=4, buffer_days=14))
    # Check all data requirements before any model for this station/lead is fit.
    for train_days, held_days in folds:
        if train_days.empty or held_days.empty:
            raise MissingData("empty outer training or held-out fold")
        for inner_train, inner_held in ev.year_folds(train_days, years_per_fold=3, buffer_days=14):
            if inner_train.empty or inner_held.empty:
                raise MissingData("insufficient years for the existing conformal inner folds")
        doy = train_days.dayofyear.to_numpy()
        for day in range(1, 367):
            distance = np.abs(doy - day)
            if not (np.minimum(distance, 366 - distance) <= 15).any():
                raise MissingData("training fold lacks a day-of-year climatology window")

    parts = []
    for train_days, held_days in folds:
        assert_development_or_earlier(train_days)
        assert_development_or_earlier(held_days)
        train = table[table["valid"].isin(train_days)]
        held = table[table["valid"].isin(held_days)]
        m1 = ConformalQuantileModel.fit(train[features.COLUMNS], stage.loc[train_days], train_days)
        l1 = fit_l1(train, stage, revealed, past)
        sim = held.join(revealed)
        sim["obs_day_q"] = live.obs_day_discharge(sim, past)
        l10, l50, l90 = l1.predict(sim[L1_COLUMNS])
        m10, m50, m90 = m1.predict(held[features.COLUMNS])
        _, clim, _ = Climatology.fit(stage.loc[train_days]).predict(held_days)
        parts.append(
            pd.DataFrame(
                {
                    "observed": stage.loc[held_days].to_numpy(),
                    "persistence": stage.loc[held.index].to_numpy(),
                    "climatology": clim,
                    "l10": l10,
                    "l50": l50,
                    "l90": l90,
                    "m10": m10,
                    "m50": m50,
                    "m90": m90,
                },
                index=held_days,
            )
        )
    scored = pd.concat(parts).sort_index()
    assert scored.index.equals(days.sort_values())
    assert_development_or_earlier(scored.index)
    assert np.isfinite(scored.to_numpy()).all()
    persistence = ev.mae(scored.observed, scored.persistence)
    l1_mae = ev.mae(scored.observed, scored.l50)
    m1_mae = ev.mae(scored.observed, scored.m50)
    return {
        "status": "scored",
        "n": len(scored),
        "target_start": f"{scored.index.min():%Y-%m-%d}",
        "target_end": f"{scored.index.max():%Y-%m-%d}",
        "l1_mae_m": l1_mae,
        "m1_mae_m": m1_mae,
        "persistence_mae_m": persistence,
        "climatology_mae_m": ev.mae(scored.observed, scored.climatology),
        "l1_skill_vs_persistence": 1 - l1_mae / persistence if persistence else None,
        "m1_skill_vs_persistence": 1 - m1_mae / persistence if persistence else None,
        "l1_coverage_80": ev.coverage(scored.observed, scored.l10, scored.l90),
        "m1_coverage_80": ev.coverage(scored.observed, scored.m10, scored.m90),
    }


def write_report(result: dict) -> None:
    """Checkpoint completed scores without refitting or rescoring."""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "15-station-ceiling.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    lines = [
        "# Experiment 15: per-station perfect-GloFAS ceiling",
        "",
        "Pre-registered in L63. This is an upper bound, not a real forecast,",
        "publication decision or exclusion from T9e. The L61 acceptance rule is unchanged.",
        "The test years 2017–2025 were not read: loaders stop at the date prefix",
        "of the first later row, before reading its measurements. Only development",
        "targets 1998–2016 are fitted/scored; four-year folds have a 14-day buffer.",
        "Earlier observations are used only for issue-day inputs and discharge lags.",
        "No target is imputed. Every comparator uses identical held-out rows.",
        "",
        "L1-ceiling reuses fit_l1 with issue-day stage always revealed (age 0).",
        "M1-ceiling has no stage input. Both reuse the unchanged conformal model:",
        "three-year inner folds, 14-day buffer, existing hyperparameters.",
        "Forecast control/mean/q10/q50/q90 all equal target-day reanalysis; spread is zero.",
        "Past discharge is shifted one day as in experiment 13; obs_day_q uses the",
        "same shifted series. Climatology is the existing training-fold day-of-year",
        "median (±15-day window). Skill = 1 − model MAE / persistence MAE.",
        "MAE is in metres; coverage is the fraction inside q10–q90 (nominal 0.80).",
        "Zero persistence MAE gives undefined skill (n/a), not a substituted denominator.",
        "",
        f"Run status: **{result['status']}**. Scoring runs: 1; bug-forced reruns: 0.",
        f"Command: `{result['command']}`.",
        f"Elapsed through last checkpoint: {result['runtime_seconds']:.2f} seconds.",
        "",
    ]
    metrics = (
        "l1_mae_m",
        "m1_mae_m",
        "persistence_mae_m",
        "climatology_mae_m",
        "l1_skill_vs_persistence",
        "m1_skill_vs_persistence",
        "l1_coverage_80",
        "m1_coverage_80",
    )
    for lead in LEADS:
        lines += [
            f"## Lead {lead} day{'s' if lead != 1 else ''}",
            "",
            "| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE "
            "| L1 skill | M1 skill | L1 coverage | M1 coverage |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        interpretations = []
        for row in result["rows"]:
            if row["lead_days"] != lead or row["status"] != "scored":
                continue
            cells = ["n/a" if row[key] is None else f"{row[key]:.3f}" for key in metrics]
            lines.append(f"| {row['station']} | {row['n']} | " + " | ".join(cells) + " |")
            skill = row["l1_skill_vs_persistence"]
            if skill is not None and skill <= 0:
                interpretations.append(f"- {row['station']}: {INTERPRETATION} (lead {lead}).")
        lines += ["", *interpretations, ""]
    lines += ["## Skipped station/leads", ""]
    skipped = [r for r in result["rows"] if r["status"] == "skipped"]
    lines += [f"- {r['station']}, lead {r['lead_days']}: {r['reason']}" for r in skipped] or [
        "None."
    ]
    (OUT / "15-station-ceiling.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    start = time.monotonic()
    result = {
        "pre_registration": "L63",
        "specs": ["S1", "S3"],
        "development": list(ev.DEVELOPMENT),
        "test_years_read": False,
        "scoring_runs": 1,
        "bug_forced_reruns": 0,
        "status": "in_progress",
        "command": COMMAND,
        "runtime_seconds": 0.0,
        "rows": [],
    }
    # Bound CPU parallelism, not the model or training sample size.
    with threadpool_limits(limits=1):
        for code in STATIONS:
            try:
                stage, discharge = load_station(code)
            except (OSError, ValueError, KeyError) as exc:
                for lead in LEADS:
                    result["rows"].append(
                        {
                            "station": code,
                            "lead_days": lead,
                            "status": "skipped",
                            "reason": f"input unavailable: {exc}",
                        }
                    )
                print(f"{code}: skipped: {exc}", flush=True)
                continue
            for lead in LEADS:
                print(f"{code} h{lead}: fitting fixed L63 recipe", flush=True)
                try:
                    row = station_lead(stage, discharge, lead)
                except MissingData as exc:
                    row = {"status": "skipped", "reason": str(exc)}
                result["rows"].append({"station": code, "lead_days": lead, **row})
                result["runtime_seconds"] = time.monotonic() - start
                write_report(result)
                print(f"{code} h{lead}: {row}", flush=True)
    result["status"] = "completed"
    result["runtime_seconds"] = time.monotonic() - start
    write_report(result)
    print(
        f"Completed in {result['runtime_seconds']:.2f} seconds; reports: {OUT}/15-station-ceiling.*"
    )


if __name__ == "__main__":
    main()
