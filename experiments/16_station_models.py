"""Experiment 16: the fixed L65 recipe, operational scoring once, offline.

Run from the repository root:
    timeout 5400 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
        uv run python -B experiments/16_station_models.py

No smoke scoring or tuning mode: only the pre-registered final evaluation.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from riverforecast import data, features, live
from riverforecast import evaluation as ev
from riverforecast.baselines import Climatology
from riverforecast.model import ConformalQuantileModel
from riverforecast.publish import L1_COLUMNS, MEMBERS_11, fit_l1

# Reuse experiment 15's bounded strict SIH/reanalysis loaders unchanged, with
# their explicit module boundary advanced to the pre-registered training end.
ceiling = importlib.import_module("15_station_ceiling")
ceiling.CUTOFF = pd.Timestamp("2022-12-31")
STATIONS = ceiling.STATIONS
LEADS = ceiling.LEADS
TRAIN_START = pd.Timestamp("1999-01-01")
TRAIN_END = pd.Timestamp("2022-12-31")
ISSUE_START = pd.Timestamp("2023-08-01")
ISSUE_END = pd.Timestamp("2026-10-13")
OUT = Path("docs/results")
COMMAND = (
    "timeout 5400 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 "
    "uv run python -B experiments/16_station_models.py"
)
LIMITATIONS = [
    "Training on a perfect forecast is expected to give bands that are too narrow "
    "with real forecasts.",
    "The scoring period holds only two to three wet seasons.",
    "GABTB has no valid IPCET reading yet (Gaviotas OCR); even an accepted lead "
    "cannot go live until the reader works.",
    "Issue-day CONAGUA stage simulates an IPCET reading available every day; "
    "actual publication gaps, reading age and OCR errors are not evaluated here.",
    "BDCTB is a reference only; its reforecast-trained production model is unchanged. "
    "Earlier BDCTB operational evaluations are not unseen evidence.",
    "Grijalva stations are regulated by Peñitas releases, which GloFAS may not represent (L61).",
]


def reveal(stage: pd.Series, issues) -> pd.DataFrame:
    revealed = live.real_revealed_values(stage, pd.Series(stage.index, index=stage.index), issues)
    assert (revealed.obs_age == 0).all()
    return revealed


def training_table(stage, discharge, lead):
    """Experiment-15 ceiling construction, but L65's 1999–2022 targets."""
    past = discharge.shift(1, freq="D").loc[:TRAIN_END]
    summary = pd.DataFrame({"valid": discharge.index, "lead_days": lead})
    summary["issue"] = summary.valid - pd.Timedelta(days=lead)
    for column in ("control", "mean", "q10", "q50", "q90"):
        summary[column] = discharge.to_numpy()
    table = features.build(summary, past, lead)
    table = table[
        table.valid.between(TRAIN_START, TRAIN_END)
        & table.valid.isin(stage.index)
        & table.index.isin(stage.index)
    ]
    assert (table.valid <= TRAIN_END).all() and (table.index <= TRAIN_END).all()
    assert (table.valid >= TRAIN_START).all()
    return table, reveal(stage, table.index), past


def station_lead(stage, discharge, operational, day0, scoring_stage, lead) -> dict:
    train, revealed, past = training_table(stage, discharge, lead)
    op = features.build(operational, day0, lead)
    op = op[
        op.index.to_series().between(ISSUE_START, ISSUE_END).to_numpy()
        & op.index.isin(scoring_stage.index)
        & op.valid.isin(scoring_stage.index)
    ]
    if train.empty or op.empty:
        raise ceiling.MissingData(
            "no common issue-day stage, target stage and discharge feature rows"
        )
    assert (op.index >= ISSUE_START).all() and (op.index <= ISSUE_END).all()
    assert (train.valid <= TRAIN_END).all(), "training row later than 2022-12-31"
    assert (op.index >= ISSUE_START).all(), "scoring issue earlier than 2023-08-01"
    days = pd.DatetimeIndex(train.valid)
    # Both estimators use exactly experiment 15's unchanged fit recipes.
    m1 = ConformalQuantileModel.fit(train[features.COLUMNS], stage.loc[days], days)
    l1 = fit_l1(train, stage, revealed, past)
    sim = op.join(reveal(scoring_stage, op.index))
    sim["obs_day_q"] = live.obs_day_discharge(sim, day0)
    assert np.isfinite(sim[L1_COLUMNS].to_numpy()).all()
    l10, l50, l90 = l1.predict(sim[L1_COLUMNS])
    m10, m50, m90 = m1.predict(op[features.COLUMNS])
    valid = pd.DatetimeIndex(op.valid)
    observed = scoring_stage.loc[valid].to_numpy()
    persistence = scoring_stage.loc[op.index].to_numpy()
    training_stage = stage.loc[TRAIN_START:TRAIN_END]
    assert (training_stage.index <= TRAIN_END).all()
    _, clim, _ = Climatology.fit(training_stage).predict(valid)
    assert np.isfinite(
        np.column_stack([observed, persistence, clim, l10, l50, l90, m10, m50, m90])
    ).all()
    pmae, lmae, mmae = (ev.mae(observed, p) for p in (persistence, l50, m50))
    return {
        "status": "scored",
        "n": len(op),
        "n_training": len(train),
        "training_target_start": f"{days.min():%Y-%m-%d}",
        "training_target_end": f"{days.max():%Y-%m-%d}",
        "issue_start": f"{op.index.min():%Y-%m-%d}",
        "issue_end": f"{op.index.max():%Y-%m-%d}",
        "target_start": f"{valid.min():%Y-%m-%d}",
        "target_end": f"{valid.max():%Y-%m-%d}",
        "l1_mae_m": lmae,
        "m1_mae_m": mmae,
        "persistence_mae_m": pmae,
        "climatology_mae_m": ev.mae(observed, clim),
        "l1_skill_vs_persistence": 1 - lmae / pmae if pmae else None,
        "m1_skill_vs_persistence": 1 - mmae / pmae if pmae else None,
        "l1_coverage_80": ev.coverage(observed, l10, l90),
        "m1_coverage_80": ev.coverage(observed, m10, m90),
        "accepted_l61": bool(lmae < pmae),
    }


def write_report(result):
    (OUT / "16-station-models.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    check = result["extraction"]["bdctb_check"]
    lines = [
        "# Experiment 16: per-station operational models",
        "",
        "Pre-registered in L65; covers S1 and S3. No tuning or recalibration on scoring data.",
        "Training targets: 1999-01-01–2022-12-31 for every station, "
        "avoiding the TEATB datum change.",
        "Strict bounded SIH/reanalysis loaders are reused from experiment 15 "
        "with the 2022 boundary.",
        "The perfect-forecast construction is unchanged: all forecast summaries equal target-day",
        "reanalysis, with zero ensemble spread. Past discharge is shifted one day "
        "as in experiment 13.",
        "L1 uses fit_l1 with issue-day stage (age 0); M1 uses the same unchanged conformal recipe",
        "without stage. Scoring uses the 11-member CEMS summary, day-0 past discharge "
        "and obs_day_q",
        "from that same day-0 archive, as in experiment 13 part B. No target is imputed.",
        "Issues: 2023-08-01–2026-10-13, restricted to observed issue and target stages "
        "and complete features.",
        "All four methods use identical scoring rows. Climatology is the day-of-year median of",
        "observed training stages (±15-day window). MAE units are metres; "
        "coverage is inclusive q10–q90.",
        "Skill = 1 − model MAE / persistence MAE (undefined for zero persistence MAE).",
        "Accepted (L61) means strictly L1 q50 MAE < persistence MAE; "
        "M1 does not decide acceptance.",
        "Acceptance is an experimental eligibility result, not deployment "
        "or a production-model change.",
        "",
        f"Status: **{result['status']}**. Scoring runs: 1; bug-forced reruns: 0.",
        f"Command: `{COMMAND}`. Elapsed: {result['runtime_seconds']:.2f} seconds.",
        "",
        "## Extraction check",
        "",
        f"BDCTB: {check['issues_compared']} issues / {check['rows_compared']} rows compared, "
        f"{check['first_issue']}–{check['last_issue']}; maximum absolute difference "
        f"{check['max_absolute_difference_m3s']:.12g} m³/s (tolerance 1e-9).",
        "Extracted BDCTB lives only in `.cache/t24/BDCTB/operational/`; "
        "originals were not overwritten.",
        "Open-Meteo cell longitudes are shifted +0.05° to the CEMS labels (same physical cell).",
        f"Extraction command: `{result['extraction']['command']}`; "
        f"elapsed {result['extraction']['runtime_seconds']:.2f} seconds.",
        "",
        "## Limitations",
        "",
        *[f"- {s}" for s in LIMITATIONS],
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
            f"## Lead {lead} day(s)",
            "",
            "| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE "
            "| L1 skill | M1 skill | L1 coverage | M1 coverage | accepted (L61) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in result["rows"]:
            if row["lead_days"] == lead and row["status"] == "scored":
                cells = ["n/a" if row[k] is None else f"{row[k]:.3f}" for k in metrics]
                accepted = "yes" if row["accepted_l61"] else "no"
                lines.append(
                    f"| {row['station']} | {row['n']} | " + " | ".join(cells) + f" | {accepted} |"
                )
        lines += [""]
    lines += ["## Skipped station/leads", ""]
    skipped = [r for r in result["rows"] if r["status"] == "skipped"]
    lines += [f"- {r['station']} h{r['lead_days']}: {r['reason']}" for r in skipped] or ["None."]
    lines += [
        "",
        "## Observed periods",
        "",
        "| Station | Lead | Training n | Training targets | Scoring issues | Scoring targets |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["rows"]:
        if r["status"] == "scored":
            lines.append(
                f"| {r['station']} | {r['lead_days']} | {r['n_training']} "
                f"| {r['training_target_start']}–{r['training_target_end']} "
                f"| {r['issue_start']}–{r['issue_end']} "
                f"| {r['target_start']}–{r['target_end']} |"
            )
    (OUT / "16-station-models.md").write_text("\n".join(lines) + "\n")


def main():
    start = time.monotonic()
    extraction = json.loads(Path(".cache/t24/extraction.json").read_text())
    assert extraction["bdctb_check"]["status"] == "passed"
    result = {
        "pre_registration": "L65",
        "specs": ["S1", "S3"],
        "training_period": [str(TRAIN_START.date()), str(TRAIN_END.date())],
        "scoring_runs": 1,
        "bug_forced_reruns": 0,
        "command": COMMAND,
        "extraction": extraction,
        "limitations": LIMITATIONS,
        "status": "in_progress",
        "runtime_seconds": 0.0,
        "rows": [],
    }
    with threadpool_limits(limits=1):
        for code in STATIONS:
            try:
                stage, discharge = ceiling.load_station(code)
                scoring_stage = data.stage(code)
                scoring_stage = scoring_stage[np.isfinite(scoring_stage)]
                day0 = data.glofas_day0(code)
                day0 = day0[np.isfinite(day0) & (day0 > 0)]
                operational = data.glofas_operational_summary(code, members=MEMBERS_11)
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
                print(f"{code} h{lead}: fitting fixed L65 recipe", flush=True)
                try:
                    row = station_lead(stage, discharge, operational, day0, scoring_stage, lead)
                except ceiling.MissingData as exc:
                    row = {"status": "skipped", "reason": str(exc)}
                result["rows"].append({"station": code, "lead_days": lead, **row})
                result["runtime_seconds"] = time.monotonic() - start
                write_report(result)
                print(f"{code} h{lead}: {row}", flush=True)
    result["status"] = "completed"
    result["runtime_seconds"] = time.monotonic() - start
    write_report(result)
    print(f"Completed in {result['runtime_seconds']:.2f} seconds", flush=True)


if __name__ == "__main__":
    main()
