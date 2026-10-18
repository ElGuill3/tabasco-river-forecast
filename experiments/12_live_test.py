"""Experiment 12: final scoring of the IPCET live input, pre-registered in L35.

The design was written in L35 of the task log before any score, and is applied
exactly: model L1 as in L32/L33 (the same conformal quantile model and
hyperparameters as M1, features.COLUMNS plus obs_last, obs_age and the log
reanalysis discharge on the day of obs_last; GloFAS-only fallback when nothing
is visible within 7 days). Scored once; re-runs only to fix crashes.

(A) Test, scored once: L1 fitted on development targets 1998-2016, live values
simulated from SIH with the tiled census availability of L32 (census
2021-08-03..2026-10-17 tiled over 1997-2023 aligned on weekday; a table for
day k is visible on k if Monday-Friday else on the next Monday and reveals
the 06:00 value of k and k-1), scored on the 2017+ reforecast target days of
experiment 04, next to the frozen model of L18 on identical rows.

(B) Operational, scored once: L1 refitted on targets 1998-2022 (as M1, L24)
with the same L32/L33 training recipe (simulated availability; the real IPCET
series only starts 2021-08-03, which cannot train the pre-registered conformal
model on 1998-2022 targets), and fed the real IPCET series
(data/raw/ipcet/bdctb_levels.csv from levels.station_series) at scoring time:
the value of day k becomes visible on the page date of the earliest table that
carries it (its own table or the next day's, via live.boca_visible_on), never
before k. GloFAS operational forecasts (11 members, day-0 past discharge) as
in L19/L25. Targets: SIH stage for valid days 2023-08-02..2025-09-03, and the
IPCET series itself for 2025-09-04..2026-10-16, the period never scored
against stage. Next to M1 on identical rows.

No threshold, feature or hyperparameter is changed after seeing these numbers.

    uv run python experiments/12_live_test.py
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
SERIES = data.RAW / "ipcet" / "bdctb_levels.csv"
HIGH_WATER = 17.83  # development P90 (L11); kept fixed, never recomputed
M1_END = "2022-12-31"
CENSUS_START, CENSUS_END = "2021-08-03", "2026-10-17"
TILE_START = "1997-01-01"
TILE_END_A = "2023-12-31"  # part (A): reforecast test issues end 2023
AGE_GROUPS = ("0", "1", "2", "3", "4+", "fallback")
L1_COLUMNS = [*features.COLUMNS, "obs_last", "obs_age", "obs_day_q"]
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
PERIODS_B = (
    ("SIH target", "2023-08-02", "2025-09-03"),
    ("IPCET target", "2025-09-04", "2026-10-16"),
)
METHODS = ("M1 (GloFAS only)", "L1 (IPCET live input)")


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


def fit_l1(
    train: pd.DataFrame,
    stage: pd.Series,
    revealed: pd.DataFrame,
    log_q: pd.Series,
) -> ConformalQuantileModel:
    """L1 fitted on ``train`` rows (indexed by issue) with the given live inputs.

    Training rows without a live input are excluded, as in experiment 10.
    """
    sim = train.join(revealed)
    sim["obs_day_q"] = sim["obs_day"].map(log_q)
    trainable = sim["obs_last"].notna().to_numpy()
    days = pd.DatetimeIndex(sim.loc[trainable, "valid"])
    return ConformalQuantileModel.fit(sim.loc[trainable, L1_COLUMNS], stage.loc[days], days)


def score_rows(
    observed: np.ndarray,
    valid: pd.DatetimeIndex,
    quantiles: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    groups: pd.Series,
    missing: np.ndarray,
    lead: int,
    period: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Score rows, MAE by obs_age, MAE by year and fallback share, identical rows."""
    rows: list[dict] = []
    mae_by_age: list[dict] = []
    mae_by_year: list[dict] = []
    fallback: list[dict] = []
    for name, (q10, q50, q90) in quantiles.items():
        contingency = ev.exceedance_scores(observed, q50, HIGH_WATER)
        rows.append(
            {
                "forecast": name,
                "lead_days": lead,
                "period": period,
                "n": int(observed.size),
                "mae": ev.mae(observed, q50),
                "coverage_80": ev.coverage(observed, q10, q90),
                "pod": contingency.probability_of_detection,
                "far": contingency.false_alarm_ratio,
            }
        )
        errors = pd.Series(np.abs(q50 - observed), index=valid)
        mae_by_year += [
            {
                "forecast": name,
                "lead_days": lead,
                "period": period,
                "year": int(year),
                "mae": float(value),
            }
            for year, value in errors.groupby(errors.index.year).mean().items()
        ]
        for label in AGE_GROUPS:
            inside = (groups == label).to_numpy()
            mae_by_age.append(
                {
                    "forecast": name,
                    "lead_days": lead,
                    "period": period,
                    "age_group": label,
                    "n": int(inside.sum()),
                    "mae": ev.mae(observed[inside], q50[inside]) if inside.any() else float("nan"),
                }
            )
        fallback.append(
            {
                "forecast": name,
                "lead_days": lead,
                "period": period,
                "share": float(missing.mean()),
                "n": int(missing.sum()),
            }
        )
    return rows, mae_by_age, mae_by_year, fallback


def run_part_a(
    stage: pd.Series, reanalysis: pd.Series, census: pd.Series, log_q: pd.Series
) -> dict:
    """(A) L1 (simulated availability) against the frozen model, test rows of 04."""
    development, test = ev.split_days(stage.index)
    summary = data.glofas_reforecast_summary(CODE)
    availability = live.tile_availability(census, pd.date_range(TILE_START, TILE_END_A, freq="D"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)

    rows: list[dict] = []
    mae_by_age: list[dict] = []
    fallback: list[dict] = []
    for lead in (1, 3, 7):
        table = features.build(summary, reanalysis, lead).drop_duplicates("valid")
        table = table[table["valid"].isin(stage.index)]
        train = table[table["valid"].isin(development)]
        held = table[table["valid"].isin(test)]
        valid = pd.DatetimeIndex(held["valid"])
        observed = stage.loc[valid].to_numpy()

        frozen = ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
        )
        l1 = fit_l1(train, stage, revealed, log_q)

        sim = held.join(revealed.reindex(held.index))
        sim["obs_day_q"] = sim["obs_day"].map(log_q)
        groups = age_groups(sim["obs_age"].set_axis(valid))
        missing = sim["obs_last"].isna().to_numpy()
        f_q10, f_q50, f_q90 = frozen.predict(held[features.COLUMNS])
        l_q10, l_q50, l_q90 = l1.predict(sim[L1_COLUMNS])
        l_q10[missing], l_q50[missing], l_q90[missing] = (
            f_q10[missing],
            f_q50[missing],
            f_q90[missing],
        )
        part_rows, part_ages, _, part_fallback = score_rows(
            observed,
            valid,
            {
                "M1 (GloFAS only)": (f_q10, f_q50, f_q90),
                "L1 (IPCET live input)": (l_q10, l_q50, l_q90),
            },
            groups,
            missing,
            lead,
            f"{valid.min():%Y-%m-%d}..{valid.max():%Y-%m-%d}",
        )
        rows += part_rows
        mae_by_age += part_ages
        fallback += part_fallback
    return {
        "design": "L35 (A): L1 fitted on development targets 1998-2016, live values "
        "simulated from SIH with the tiled census availability of L32, scored on the "
        "2017+ reforecast target days of experiment 04 next to the frozen model (L18), "
        "identical rows",
        "rows": rows,
        "mae_by_age": mae_by_age,
        "fallback_share": fallback,
    }


def run_part_b(
    stage: pd.Series, reanalysis: pd.Series, census: pd.Series, log_q: pd.Series
) -> dict:
    """(B) L1 (real IPCET feed) against M1, operational rows, two target periods."""
    ipcet = pd.read_csv(SERIES, parse_dates=["date"], index_col="date")["stage_m"]
    visible_on = live.boca_visible_on(live.boca_rows())
    assert visible_on.reindex(ipcet.index).notna().all(), "series day without visibility date"
    reforecast = data.glofas_reforecast_summary(CODE)
    operational = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    day0 = data.glofas_day0(CODE)
    m1_days = stage.index[(stage.index >= "1998-01-01") & (stage.index <= M1_END)]
    # L1 refit on targets 1998-2022 with the L32/L33 training recipe (simulated
    # availability); the real IPCET series starts 2021-08-03 and cannot train
    # the pre-registered conformal model on 1998-2022 targets.
    availability = live.tile_availability(census, pd.date_range(TILE_START, M1_END, freq="D"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)

    rows: list[dict] = []
    mae_by_age: list[dict] = []
    mae_by_year: list[dict] = []
    fallback: list[dict] = []
    obs_day_q_missing = 0
    for lead in (1, 3, 7):
        train = features.build(reforecast, reanalysis, lead).drop_duplicates("valid")
        train = train[train["valid"].isin(m1_days)]
        m1 = ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
        )
        l1 = fit_l1(train, stage, revealed, log_q)

        op = features.build(operational, day0, lead)
        parts = []
        for name, start, end in PERIODS_B:
            target = stage if name == "SIH target" else ipcet
            inside = op["valid"].isin(target.index) & (op["valid"] >= start) & (op["valid"] <= end)
            part = op[inside].copy()
            part["period"] = name
            parts.append(part)
        op_rows = pd.concat(parts)
        observed = np.concatenate(
            [
                target.loc[op_rows.loc[op_rows["period"] == name, "valid"]].to_numpy()
                for name, target in (("SIH target", stage), ("IPCET target", ipcet))
            ]
        )
        real = live.real_revealed_values(ipcet, visible_on, op_rows.index)
        real["obs_day_q"] = real["obs_day"].map(log_q)
        obs_day_q_missing += int(real["obs_day_q"].isna().sum())
        missing = real["obs_last"].isna().to_numpy()

        f_q10, f_q50, f_q90 = m1.predict(op_rows[features.COLUMNS])
        l_q10, l_q50, l_q90 = l1.predict(op_rows.join(real)[L1_COLUMNS])
        l_q10[missing], l_q50[missing], l_q90[missing] = (
            f_q10[missing],
            f_q50[missing],
            f_q90[missing],
        )
        all_quantiles = {
            "M1 (GloFAS only)": (f_q10, f_q50, f_q90),
            "L1 (IPCET live input)": (l_q10, l_q50, l_q90),
        }
        for name, _, _ in PERIODS_B:
            inside = (op_rows["period"] == name).to_numpy()
            part_valid = pd.DatetimeIndex(op_rows.loc[inside, "valid"])
            part_quantiles = {
                forecast: (q10[inside], q50[inside], q90[inside])
                for forecast, (q10, q50, q90) in all_quantiles.items()
            }
            part_rows, part_ages, part_years, part_fallback = score_rows(
                observed[inside],
                part_valid,
                part_quantiles,
                age_groups(real["obs_age"].loc[inside]),
                missing[inside],
                lead,
                name,
            )
            rows += part_rows
            mae_by_age += part_ages
            mae_by_year += part_years
            fallback += part_fallback
    return {
        "design": "L35 (B): L1 refitted on targets 1998-2022 (as M1, L24) with the "
        "L32/L33 training recipe and fed the real IPCET series at scoring time "
        "(visibility: page date of the earliest table carrying the value, never "
        "before the day), operational forecasts as in L19/L25, targets SIH stage "
        "2023-08-02..2025-09-03 and the IPCET series 2025-09-04..2026-10-16, next "
        "to M1 on identical rows",
        "rows": rows,
        "mae_by_age": mae_by_age,
        "mae_by_year": mae_by_year,
        "fallback_share": fallback,
        "obs_day_q_missing_rows": obs_day_q_missing,
    }


def write_report(result: dict) -> None:
    lines = [
        "# Experiment 12: final scoring of the IPCET live input",
        "",
        "Generated by `experiments/12_live_test.py`. **Pre-registered in L35;",
        "scored once.** Model L1 exactly as L32/L33: the same conformal quantile",
        "model and hyperparameters as M1 with obs_last, obs_age and the log",
        "reanalysis discharge on the day of obs_last, GloFAS-only fallback when",
        "nothing is visible within 7 days. High water: stage >="
        f" {result['high_water_m']:.2f} m. Identical rows for L1 and its",
        "comparator in each part.",
        "",
        "## (A) Test, scored once (reforecast target days of experiment 04)",
        "",
        "L1 fitted on development targets 1998-2016, live values simulated from",
        "SIH with the tiled census availability of L32; comparator: the frozen",
        "model of L18.",
        "",
        "| forecast | lead | period | n | MAE | coverage (80%) | high-water POD | high-water FAR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["part_A_test"]["rows"]:
        lines.append(
            f"| {row['forecast']} | {row['lead_days']} | {row['period']} | {row['n']} "
            f"| {row['mae']:.3f} | {row['coverage_80']:.3f} | {row['pod']:.3f} "
            f"| {row['far']:.3f} |"
        )
    lines += [
        "",
        "### (A) MAE by obs_age (m)",
        "",
        "| forecast | lead | " + " | ".join(AGE_GROUPS) + " |",
        "| --- | --- | " + " | ".join("---" for _ in AGE_GROUPS) + " |",
    ]
    by_key = {
        (r["forecast"], r["lead_days"], r["age_group"]): r["mae"]
        for r in result["part_A_test"]["mae_by_age"]
    }
    for lead in (1, 3, 7):
        for name in METHODS:
            cells = [
                f"{by_key[(name, lead, label)]:.3f}"
                if not np.isnan(by_key[(name, lead, label)])
                else "n/a"
                for label in AGE_GROUPS
            ]
            lines.append(f"| {name} | {lead} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "### (A) Fallback share (no visible value within 7 days)",
        "",
        "| lead | share | n |",
        "| --- | --- | --- |",
    ]
    for row in result["part_A_test"]["fallback_share"]:
        if row["forecast"] == METHODS[0]:
            lines.append(f"| {row['lead_days']} | {row['share']:.3f} | {row['n']} |")
    lines += [
        "",
        "## (B) Operational, scored once",
        "",
        "L1 refitted on targets 1998-2022 (as M1, L24) with the L32/L33 training",
        "recipe and fed the real IPCET series at scoring time (value of day k",
        "visible on the page date of the earliest table carrying it, never before",
        "k). Targets: SIH stage 2023-08-02..2025-09-03 and the IPCET series",
        "2025-09-04..2026-10-16 (never scored against stage). Comparator: M1.",
        "",
        "| forecast | lead | period | n | MAE | coverage (80%) | high-water POD | high-water FAR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["part_B_operational"]["rows"]:
        lines.append(
            f"| {row['forecast']} | {row['lead_days']} | {row['period']} | {row['n']} "
            f"| {row['mae']:.3f} | {row['coverage_80']:.3f} | {row['pod']:.3f} "
            f"| {row['far']:.3f} |"
        )
    lines += [
        "",
        "### (B) MAE by obs_age (m)",
        "",
        "| forecast | lead | period | " + " | ".join(AGE_GROUPS) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in AGE_GROUPS) + " |",
    ]
    by_key = {
        (r["forecast"], r["lead_days"], r["period"], r["age_group"]): r["mae"]
        for r in result["part_B_operational"]["mae_by_age"]
    }
    for lead in (1, 3, 7):
        for period in ("SIH target", "IPCET target"):
            for name in METHODS:
                cells = [
                    f"{by_key[(name, lead, period, label)]:.3f}"
                    if not np.isnan(by_key[(name, lead, period, label)])
                    else "n/a"
                    for label in AGE_GROUPS
                ]
                lines.append(f"| {name} | {lead} | {period} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "### (B) Fallback share",
        "",
        "| lead | period | share | n |",
        "| --- | --- | --- | --- |",
    ]
    for row in result["part_B_operational"]["fallback_share"]:
        if row["forecast"] == METHODS[0]:
            lines.append(
                f"| {row['lead_days']} | {row['period']} | {row['share']:.3f} | {row['n']} |"
            )
    lines += [
        "",
        "### (B) MAE by year (m)",
        "",
    ]
    for period in ("SIH target", "IPCET target"):
        yearly = pd.DataFrame(
            [r for r in result["part_B_operational"]["mae_by_year"] if r["period"] == period]
        )
        pivot = yearly.pivot_table(index="year", columns=["lead_days", "forecast"], values="mae")
        lines += [f"**{period}**", "", pivot.round(3).to_markdown(), ""]
    missing = result["part_B_operational"]["obs_day_q_missing_rows"]
    lines += [
        "Rows scored with the obs-day reanalysis discharge missing (reanalysis ends",
        f"its record before the obs day): {missing}. The gradient-boosting model",
        "handles a missing value natively; training rows all have the feature.",
        "",
    ]
    (OUT / "12-live-test.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    stage = data.stage(CODE)
    reanalysis = data.glofas_reanalysis(CODE)
    log_q = np.log(reanalysis.sort_index())
    census = live.levels_census().loc[CENSUS_START:CENSUS_END]

    part_a = run_part_a(stage, reanalysis, census, log_q)
    part_b = run_part_b(stage, reanalysis, census, log_q)
    result = {
        "pre_registration": "L35",
        "scored_once": True,
        "high_water_m": HIGH_WATER,
        "part_A_test": part_a,
        "part_B_operational": part_b,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "12-live-test.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


if __name__ == "__main__":
    main()
