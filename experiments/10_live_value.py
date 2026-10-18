"""Experiment 10: value of the IPCET live stage input, pre-registered in the task log (L32).

The IPCET daily level table is not published every day (L31), so a live input
must carry the age of the latest visible value. Following the pre-registration
written in L32 before any development result (development only; target days
1998-2016; the test period stays untouched until the reader exists):

- Availability: the census sequence 2021-08-03..2026-10-17 (table exists or not
  per day, L31) is tiled over 1997-2016 aligned on weekday; a table for day k
  becomes visible on k if k is Monday-Friday, otherwise on the next Monday; a
  visible table reveals the 06:00 value of k and of k-1. At issue day i the
  live input is the latest revealed value (obs_last) and its age (obs_age); no
  value within 7 days means fallback to M1.
- Model L1 = the same conformal quantile model and hyperparameters as M1 with
  features.COLUMNS plus obs_last, obs_age and the (log) reanalysis discharge on
  the day of obs_last, so the model can turn the GloFAS change since that day
  into a stage change. Folds and buffers as in experiment 02 (four whole years,
  14-day buffer); training rows without a live input are excluded, and held-out
  rows without one fall back to the GloFAS-only prediction of M1 (counted).
- References on identical rows: M1 (GloFAS only, out of fold), persistence of
  obs_last (point forecast; fallback rows use the M1 prediction), and L1 under
  full availability (age 0 every day; where the gauge itself has no value the
  M1 prediction is used) as the ceiling.

Decision rule (L32): the reader is built only if L1 lowers development MAE by
at least 0.10 m at lead 1 against M1 under the simulated availability. Nothing
is scored on 2017+ data.

    uv run python experiments/10_live_value.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from riverforecast import data, features, live
from riverforecast import evaluation as ev
from riverforecast.model import ConformalQuantileModel

CODE = "BDCTB"
OUT = Path("docs/results")
HIGH_WATER = 17.83  # development P90 (L11); kept fixed, never recomputed
CENSUS_START, CENSUS_END = "2021-08-03", "2026-10-17"
TILE_START, TILE_END = "1997-01-01", "2016-12-31"
AGE_GROUPS = ("0", "1", "2", "3", "4+", "fallback")
L1_COLUMNS = [*features.COLUMNS, "obs_last", "obs_age", "obs_day_q"]
METHODS = (
    "M1 (GloFAS only)",
    "L1 (simulated availability)",
    "L1 (full availability)",
    "persistence of obs_last",
)


def out_of_fold(
    frame: pd.DataFrame, stage: pd.Series, columns: list[str], trainable: pd.Series
) -> pd.DataFrame:
    """Out-of-fold conformal quantiles; folds hold out whole years of target days.

    ``frame`` is indexed by target day (valid). Rows where ``trainable`` is
    False are excluded from training. Returns q10/q50/q90 indexed by valid day
    for every row, trained or not.
    """
    parts = []
    for train, held in ev.year_folds(frame.index, 4, 14):
        train_days = train[trainable.loc[train].to_numpy()]
        model = ConformalQuantileModel.fit(
            frame.loc[train_days, columns], stage.loc[train_days], pd.DatetimeIndex(train_days)
        )
        q10, q50, q90 = model.predict(frame.loc[held, columns])
        parts.append(pd.DataFrame({"q10": q10, "q50": q50, "q90": q90}, index=held))
    return pd.concat(parts).sort_index()


def fallback_into(quantiles: pd.DataFrame, fallback: pd.DataFrame, missing: pd.Series) -> None:
    """Replace the quantiles of the ``missing`` rows with the fallback forecast."""
    quantiles.loc[missing.to_numpy()] = fallback.loc[missing.to_numpy()].to_numpy()


def live_inputs(
    issue_days: pd.DatetimeIndex, stage: pd.Series, reanalysis: pd.Series, census: pd.Series
) -> pd.DataFrame:
    """Simulated live input on each issue day under the L32 availability rules."""
    availability = live.tile_availability(census, pd.date_range(TILE_START, TILE_END, freq="D"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)
    sim = revealed.reindex(issue_days)
    log_q = np.log(reanalysis.sort_index())
    sim["obs_day_q"] = sim["obs_day"].map(log_q)
    return sim


def full_availability(
    issue_days: pd.DatetimeIndex, stage: pd.Series, reanalysis: pd.Series
) -> pd.DataFrame:
    """Ceiling live input: today's observed value, age 0, every day."""
    full = pd.DataFrame(index=issue_days)
    full["obs_last"] = stage.reindex(issue_days).to_numpy()
    full["obs_age"] = 0.0
    full["obs_day"] = issue_days
    log_q = np.log(reanalysis.sort_index())
    full["obs_day_q"] = log_q.reindex(issue_days).to_numpy()
    return full


def age_groups(ages: pd.Series) -> pd.Series:
    """Group labels 0, 1, 2, 3, 4+ (ages 4..7) and fallback (no live input)."""
    groups = pd.Series("fallback", index=ages.index, dtype=object)
    for label, inside in (
        ("0", ages == 0),
        ("1", ages == 1),
        ("2", ages == 2),
        ("3", ages == 3),
        ("4+", (ages >= 4) & (ages <= 7)),
    ):
        groups[inside.fillna(False).to_numpy()] = label
    return groups


def main() -> None:
    stage = data.stage(CODE)
    development, _ = ev.split_days(stage.index)
    reanalysis = data.glofas_reanalysis(CODE)
    summary = data.glofas_reforecast_summary(CODE)
    census = live.levels_census().loc[CENSUS_START:CENSUS_END]

    rows: list[dict] = []
    mae_by_age: list[dict] = []
    age_share: list[dict] = []
    decision: dict = {}

    for lead in (1, 3, 7):
        table = features.build(summary, reanalysis, lead).drop_duplicates("valid")
        table = table[table["valid"].isin(development) & table["valid"].isin(stage.index)]
        valid = pd.DatetimeIndex(table["valid"])
        observed = stage.loc[valid].to_numpy()

        sim = live_inputs(table.index, stage, reanalysis, census)
        full = full_availability(table.index, stage, reanalysis)
        table_sim = table.join(sim).set_index("valid")
        table_full = table.join(full).set_index("valid")
        sim_trainable = pd.Series(table_sim["obs_last"].notna().to_numpy(), index=valid)
        full_trainable = pd.Series(table_full["obs_last"].notna().to_numpy(), index=valid)
        sim_missing = ~sim_trainable
        full_missing = ~full_trainable

        m1 = out_of_fold(
            table.set_index("valid"), stage, features.COLUMNS, pd.Series(True, index=valid)
        )
        l1_sim = out_of_fold(table_sim, stage, L1_COLUMNS, sim_trainable)
        l1_full = out_of_fold(table_full, stage, L1_COLUMNS, full_trainable)
        fallback_into(l1_sim, m1, sim_missing)
        fallback_into(l1_full, m1, full_missing)
        persistence = pd.DataFrame(
            {
                "q10": table_sim["obs_last"].to_numpy(),
                "q50": table_sim["obs_last"].to_numpy(),
                "q90": table_sim["obs_last"].to_numpy(),
            },
            index=valid,
        )
        fallback_into(persistence, m1, sim_missing)

        forecasts = {
            "M1 (GloFAS only)": m1,
            "L1 (simulated availability)": l1_sim,
            "L1 (full availability)": l1_full,
            "persistence of obs_last": persistence,
        }
        assert all(set(frame.index) == set(valid) for frame in forecasts.values())
        groups = age_groups(sim["obs_age"].set_axis(valid))
        for name, frame in forecasts.items():
            q10, q50, q90 = (frame[key].to_numpy() for key in ("q10", "q50", "q90"))
            contingency = ev.exceedance_scores(observed, q50, HIGH_WATER)
            rows.append(
                {
                    "forecast": name,
                    "lead_days": lead,
                    "n": int(observed.size),
                    "mae": ev.mae(observed, q50),
                    "coverage_80": ev.coverage(observed, q10, q90),
                    "pod": contingency.probability_of_detection,
                    "far": contingency.false_alarm_ratio,
                }
            )
            for label in AGE_GROUPS:
                inside = (groups == label).to_numpy()
                mae_by_age.append(
                    {
                        "forecast": name,
                        "lead_days": lead,
                        "age_group": label,
                        "n": int(inside.sum()),
                        "mae": ev.mae(observed[inside], q50[inside])
                        if inside.any()
                        else float("nan"),
                    }
                )
        for label in AGE_GROUPS:
            inside = (groups == label).to_numpy()
            age_share.append(
                {
                    "lead_days": lead,
                    "age_group": label,
                    "n": int(inside.sum()),
                    "share": float(inside.mean()),
                }
            )
        if lead == 1:
            m1_mae = ev.mae(observed, m1["q50"].to_numpy())
            l1_mae = ev.mae(observed, l1_sim["q50"].to_numpy())
            decision = {
                "lead_1_mae_m1": m1_mae,
                "lead_1_mae_l1_simulated": l1_mae,
                "improvement_m": m1_mae - l1_mae,
                "required_m": 0.10,
                "passes": bool(m1_mae - l1_mae >= 0.10),
            }

    result = {
        "pre_registration": "L32",
        "period": "development only, target days 1998-2016 (reforecast issues 2004-2016)",
        "high_water_m": HIGH_WATER,
        "census": f"data/raw/ipcet/levels_census.csv tiled {CENSUS_START}..{CENSUS_END} "
        f"over {TILE_START}..{TILE_END} aligned on weekday",
        "fallback_rows_by_lead": {
            str(lead): next(
                r["n"] for r in age_share if r["lead_days"] == lead and r["age_group"] == "fallback"
            )
            for lead in (1, 3, 7)
        },
        "rows": rows,
        "mae_by_age": mae_by_age,
        "age_share": age_share,
        "decision": decision,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "10-live-value.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


def write_report(result: dict) -> None:
    lines = [
        "# Experiment 10: value of the IPCET live stage input",
        "",
        "Generated by `experiments/10_live_value.py`. **Development only** (target",
        "days 1998-2016; nothing is scored on 2017+), applying the design",
        "pre-registered in L32 exactly: the IPCET census of L31 tiled over 1997-2016",
        "aligned on weekday, tables visible on their day if Monday-Friday else on",
        "the next Monday, each table revealing the 06:00 value of its day and of the",
        "previous one, live input = latest revealed value with its age, fallback to",
        "M1 when nothing within 7 days. L1 is the same conformal quantile model as",
        "M1 with obs_last, obs_age and the log reanalysis discharge on the day of",
        "obs_last; training rows without a live input are excluded and held-out",
        "rows without one fall back to the GloFAS-only prediction. High water:",
        f"stage >= {result['high_water_m']:.2f} m. Identical rows for all methods.",
        "",
        "## Scores by lead",
        "",
        "| forecast | lead | n | MAE | coverage (80%) | high-water POD | high-water FAR |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['forecast']} | {row['lead_days']} | {row['n']} | {row['mae']:.3f} "
            f"| {row['coverage_80']:.3f} | {row['pod']:.3f} | {row['far']:.3f} |"
        )
    lines += [
        "",
        "Persistence of obs_last is a point forecast (q10 = q50 = q90), so its",
        "interval has zero width and its coverage is ~0 by construction; its MAE,",
        "POD and FAR are still comparable on the same rows.",
        "",
        "## MAE by obs_age under the simulated availability (m)",
        "",
        "| forecast | lead | " + " | ".join(AGE_GROUPS) + " |",
        "| --- | --- | " + " | ".join("---" for _ in AGE_GROUPS) + " |",
    ]
    by_key = {
        (r["forecast"], r["lead_days"], r["age_group"]): r["mae"] for r in result["mae_by_age"]
    }
    for name in METHODS:
        for lead in (1, 3, 7):
            cells = [
                f"{by_key[(name, lead, label)]:.3f}"
                if not np.isnan(by_key[(name, lead, label)])
                else "n/a"
                for label in AGE_GROUPS
            ]
            lines.append(f"| {name} | {lead} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Share of rows by obs_age under the simulated availability",
        "",
        "| lead | " + " | ".join(AGE_GROUPS) + " |",
        "| --- | " + " | ".join("---" for _ in AGE_GROUPS) + " |",
    ]
    for lead in (1, 3, 7):
        cells = [
            f"{r['share']:.3f} (n={r['n']})" for r in result["age_share"] if r["lead_days"] == lead
        ]
        lines.append(f"| {lead} | " + " | ".join(cells) + " |")
    decision = result["decision"]
    lines += [
        "",
        "## Decision rule (L32)",
        "",
        f"L1 must lower development MAE by at least {decision['required_m']:.2f} m at",
        f"lead 1 against M1 under the simulated availability: M1 "
        f"{decision['lead_1_mae_m1']:.3f} m, L1 {decision['lead_1_mae_l1_simulated']:.3f} m, "
        f"improvement "
        f"{decision['improvement_m']:.3f} m -> "
        f"**{'passes' if decision['passes'] else 'does not pass'}**.",
        "",
    ]
    (OUT / "10-live-value.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
