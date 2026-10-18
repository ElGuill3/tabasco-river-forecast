"""Experiment 13: post-audit re-score with the four L39 defect fixes.

The fixes were pre-registered in L39 of the task log, after the read-only
audit of T17, before this experiment was written; the re-score runs once and
is reported next to L36 (experiment 12) with the same models, features and
hyperparameters. Earlier experiments are kept as they were.

Fixes applied (L39):
1. (D3) training past discharge = ``data.glofas_training_past``, the reanalysis
   shifted one day (past[d] = reanalysis[d-1]), for every fitted model: the
   frozen 1998-2016 model, M1 1998-2022 and both L1 fits, so the labels match
   the day-0 archive used in operation.
2. (D1) obs_day_q from the same past-discharge series the features use: the
   shifted reanalysis in training, the day-0 archive in operation
   (``live.obs_day_discharge``).
3. (D4) issue time fixed at 11:00 local (UTC-6); a table's visibility date is
   moved to its HTTP Last-Modified upload day (local date, +1 day when
   uploaded after 11:00) whenever that is 0-14 days after the page date
   (``live.boca_table_visibility`` with the manifest Last-Modified).
4. (D2) strictly causal live series: for each issue day, the station series
   rebuilt from the table rows visible by that day only
   (``live.causal_revealed_values``).

(A) and (B) follow L35 exactly otherwise: (A) L1 fitted on development targets
1998-2016, live values simulated from SIH with the tiled census availability
of L32, scored on the 2017+ reforecast target days of experiment 04 next to
the frozen model (refitted with fix 1); (B) L1 refitted on targets 1998-2022
with the L32/L33 training recipe and fed the real IPCET series at scoring
time, operational forecasts (11 members, day-0 past discharge) as in
L19/L25, targets SIH stage 2023-08-02..2025-09-03 and the IPCET series
2025-09-04..2026-10-16, next to M1 (refitted with fix 1) on identical rows.

``--smoke`` limits every part to a handful of issue days and a short training
slice so the whole run finishes in a few minutes; its numbers are meaningless
and are never reported.

    uv run python experiments/13_postaudit.py [--smoke]
"""

from __future__ import annotations

import argparse
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
MANIFEST = live.MANIFEST
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
# --smoke limits: last training rows per fit, held/scoring issue days per part.
SMOKE_TRAIN_ROWS = 1200
SMOKE_ISSUE_DAYS = 6


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


def manifest_last_modified(path: Path | None = None) -> pd.Series:
    """Earliest HTTP Last-Modified per image sha256 (GMT timestamps).

    The same file can be probed twice; the earliest stamp is the first upload.
    """
    if path is None:
        path = MANIFEST
    manifest = pd.read_csv(path).dropna(subset=["last_modified"])
    stamps = pd.to_datetime(manifest["last_modified"])
    frame = pd.DataFrame({"sha256": manifest["sha256"], "last_modified": stamps})
    return frame.groupby("sha256")["last_modified"].min()


def fit_l1(
    train: pd.DataFrame,
    stage: pd.Series,
    revealed: pd.DataFrame,
    past: pd.Series,
) -> ConformalQuantileModel:
    """L1 fitted on ``train`` rows (indexed by issue) with the given live inputs.

    Training rows without a live input are excluded, as in experiment 10.
    ``obs_day_q`` comes from ``past``, the same shifted-reanalysis series the
    ``past_*`` features are built on (L39 fix 2).
    """
    sim = train.join(revealed)
    sim["obs_day_q"] = live.obs_day_discharge(sim, past)
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
    stage: pd.Series,
    training_past: pd.Series,
    census: pd.Series,
    smoke: bool = False,
) -> dict:
    """(A) L1 (simulated availability) against the frozen model, test rows of 04.

    Every fitted model uses the shifted reanalysis as its past discharge
    (L39 fix 1) and the shifted series for obs_day_q (fix 2).
    """
    development, test = ev.split_days(stage.index)
    summary = data.glofas_reforecast_summary(CODE)
    availability = live.tile_availability(census, pd.date_range(TILE_START, TILE_END_A, freq="D"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)

    rows: list[dict] = []
    mae_by_age: list[dict] = []
    fallback: list[dict] = []
    for lead in (1, 3, 7):
        table = features.build(summary, training_past, lead).drop_duplicates("valid")
        table = table[table["valid"].isin(stage.index)]
        train = table[table["valid"].isin(development)]
        held = table[table["valid"].isin(test)]
        if smoke:
            train = train.iloc[-SMOKE_TRAIN_ROWS:]
            held = held.iloc[-SMOKE_ISSUE_DAYS:]
        valid = pd.DatetimeIndex(held["valid"])
        observed = stage.loc[valid].to_numpy()

        frozen = ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
        )
        l1 = fit_l1(train, stage, revealed, training_past)

        sim = held.join(revealed.reindex(held.index))
        sim["obs_day_q"] = live.obs_day_discharge(sim, training_past)
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
        "design": "L35 (A) with the L39 fixes: L1 fitted on development targets "
        "1998-2016 on the shifted reanalysis (fix 1, obs_day_q from the same "
        "series, fix 2), live values simulated from SIH with the tiled census "
        "availability of L32, scored on the 2017+ reforecast target days of "
        "experiment 04 next to the frozen model refitted with fix 1, identical "
        "rows",
        "rows": rows,
        "mae_by_age": mae_by_age,
        "fallback_share": fallback,
    }


def run_part_b(
    stage: pd.Series,
    training_past: pd.Series,
    day0: pd.Series,
    census: pd.Series,
    boca: pd.DataFrame,
    visible_rows: pd.Series,
    smoke: bool = False,
) -> dict:
    """(B) L1 (real IPCET feed, causal) against M1, operational rows.

    Training on the shifted reanalysis (fix 1); operation on the day-0 archive
    with obs_day_q from day-0 (fix 2), upload-aware visibility (fix 3) and the
    strictly causal live series (fix 4).
    """
    ipcet = pd.read_csv(SERIES, parse_dates=["date"], index_col="date")["stage_m"]
    reforecast = data.glofas_reforecast_summary(CODE)
    operational = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    m1_days = stage.index[(stage.index >= "1998-01-01") & (stage.index <= M1_END)]
    # L1 refit on targets 1998-2022 with the L32/L33 training recipe (simulated
    # availability on the shifted reanalysis); the real IPCET series starts
    # 2021-08-03 and cannot train the pre-registered conformal model.
    availability = live.tile_availability(census, pd.date_range(TILE_START, M1_END, freq="D"))
    revealed = live.revealed_values(stage.reindex(availability.index), availability)

    rows: list[dict] = []
    mae_by_age: list[dict] = []
    mae_by_year: list[dict] = []
    fallback: list[dict] = []
    for lead in (1, 3, 7):
        train = features.build(reforecast, training_past, lead).drop_duplicates("valid")
        train = train[train["valid"].isin(m1_days)]
        if smoke:
            train = train.iloc[-SMOKE_TRAIN_ROWS:]
        m1 = ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
        )
        l1 = fit_l1(train, stage, revealed, training_past)

        op = features.build(operational, day0, lead)
        parts = []
        for name, start, end in PERIODS_B:
            target = stage if name == "SIH target" else ipcet
            inside = op["valid"].isin(target.index) & (op["valid"] >= start) & (op["valid"] <= end)
            part = op[inside].copy()
            part["period"] = name
            parts.append(part)
        op_rows = pd.concat(parts)
        if smoke:
            op_rows = pd.concat(
                [part.iloc[-SMOKE_ISSUE_DAYS:] for _, part in op_rows.groupby("period")]
            ).sort_index()
        observed = np.concatenate(
            [
                target.loc[op_rows.loc[op_rows["period"] == name, "valid"]].to_numpy()
                for name, target in (("SIH target", stage), ("IPCET target", ipcet))
            ]
        )
        # Strictly causal live series (L39 fix 4): each issue day sees only the
        # table rows visible by then, with upload-aware visibility (fix 3).
        real = live.causal_revealed_values(boca, visible_rows, op_rows.index)
        real["obs_day_q"] = live.obs_day_discharge(real, day0)
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
        "design": "L35 (B) with the L39 fixes: L1 refitted on targets 1998-2022 "
        "(as M1, L24) with the L32/L33 training recipe on the shifted reanalysis "
        "(fix 1) and fed the real IPCET series at scoring time; operational "
        "forecasts (11 members) built on the day-0 archive with obs_day_q from "
        "day-0 (fix 2), visibility moved to the HTTP Last-Modified upload day "
        "when 0-14 days after the page date (fix 3) and the strictly causal "
        "live series of tables visible by each issue day (fix 4); targets SIH "
        "stage 2023-08-02..2025-09-03 and the IPCET series "
        "2025-09-04..2026-10-16, next to M1 refitted with fix 1 on identical rows",
        "rows": rows,
        "mae_by_age": mae_by_age,
        "mae_by_year": mae_by_year,
        "fallback_share": fallback,
    }


def load_l36_mae() -> dict[tuple[str, int, str], float]:
    """MAE of experiment 12 (L36) per (forecast, lead, period), for comparison."""
    path = OUT / "12-live-test.json"
    if not path.exists():
        return {}
    result = json.loads(path.read_text())
    mapping: dict[tuple[str, int, str], float] = {}
    for part in ("part_A_test", "part_B_operational"):
        for row in result.get(part, {}).get("rows", []):
            mapping[(row["forecast"], row["lead_days"], row["period"])] = row["mae"]
    return mapping


def write_report(result: dict) -> None:
    l36 = load_l36_mae()
    header = (
        "**Post-audit re-score, pre-registered in L39; scored once.** "
        "The `MAE (L36)` column repeats the corresponding experiment 12 number "
        "for comparison."
    )
    lines = [
        "# Experiment 13: post-audit re-score with the L39 defect fixes",
        "",
        "Generated by `experiments/13_postaudit.py`.",
        header,
        "Fixes: training past discharge = reanalysis shifted one day (D3),",
        "obs_day_q from the same past-discharge series the features use (D1),",
        "visibility moved to the HTTP Last-Modified upload day when 0-14 days",
        "after the page date with the issue hour fixed at 11:00 local (D4), and",
        "the strictly causal live series of tables visible by each issue day",
        "(D2). Same models, features and hyperparameters as L35/L36. High water:",
        f" stage >= {result['high_water_m']:.2f} m. Identical rows for L1 and",
        "its comparator in each part.",
        "",
        "## (A) Test, scored once (reforecast target days of experiment 04)",
        "",
        "L1 fitted on development targets 1998-2016, live values simulated from",
        "SIH with the tiled census availability of L32; comparator: the frozen",
        "model of L18, refitted with the shifted reanalysis.",
        "",
        "| forecast | lead | period | n | MAE | MAE (L36) "
        "| coverage (80%) | high-water POD | high-water FAR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["part_A_test"]["rows"]:
        previous = l36.get((row["forecast"], row["lead_days"], row["period"]))
        lines.append(
            f"| {row['forecast']} | {row['lead_days']} | {row['period']} | {row['n']} "
            f"| {row['mae']:.3f} | "
            + (f"{previous:.3f}" if previous is not None else "n/a")
            + f" | {row['coverage_80']:.3f} | {row['pod']:.3f} "
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
        "recipe and fed the real IPCET series at scoring time with the fixed",
        "visibility and the strictly causal live series. Targets: SIH stage",
        "2023-08-02..2025-09-03 and the IPCET series 2025-09-04..2026-10-16",
        "(never scored against stage). Comparator: M1. Both refitted with the",
        "shifted reanalysis; operational features and obs_day_q from the day-0",
        "archive.",
        "",
        "| forecast | lead | period | n | MAE | MAE (L36) "
        "| coverage (80%) | high-water POD | high-water FAR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["part_B_operational"]["rows"]:
        previous = l36.get((row["forecast"], row["lead_days"], row["period"]))
        lines.append(
            f"| {row['forecast']} | {row['lead_days']} | {row['period']} | {row['n']} "
            f"| {row['mae']:.3f} | "
            + (f"{previous:.3f}" if previous is not None else "n/a")
            + f" | {row['coverage_80']:.3f} | {row['pod']:.3f} "
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
    lines += [
        "obs_day_q now comes from the day-0 archive in operation (L39 fix 2),",
        "so no operational row is scored with a missing obs-day discharge.",
        "",
    ]
    (OUT / "13-postaudit.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="limit every part to a handful of issue days (numbers are meaningless)",
    )
    smoke = parser.parse_args().smoke
    stage = data.stage(CODE)
    training_past = data.glofas_training_past(CODE)  # L39 fix 1
    day0 = data.glofas_day0(CODE)
    census = live.levels_census().loc[CENSUS_START:CENSUS_END]
    boca = live.boca_rows()
    visible_rows = live.boca_table_visibility(boca, manifest_last_modified())  # L39 fix 3

    part_a = run_part_a(stage, training_past, census, smoke=smoke)
    part_b = run_part_b(stage, training_past, day0, census, boca, visible_rows, smoke=smoke)
    result = {
        "pre_registration": "L39",
        "scored_once": True,
        "header": "post-audit re-score, pre-registered in L39; scored once",
        "high_water_m": HIGH_WATER,
        "part_A_test": part_a,
        "part_B_operational": part_b,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "13-postaudit.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


if __name__ == "__main__":
    main()
