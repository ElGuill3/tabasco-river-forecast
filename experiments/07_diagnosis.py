"""Experiment 07: why the frozen model over-forecasts operationally. Diagnosis only.

The frozen model (experiments 01-06: conformal quantile model on GloFAS inputs,
no rainfall) under-forecasts high stages on the development out-of-fold and test
reforecasts (mean q50 error on observed >= 15 m: -0.4 m) but over-forecasts the
same stages on the operational forecasts of experiment 05 (+0.1..+0.3 m), which
drives operational false-alarm ratios of 0.50-0.57 (L21). This experiment
diagnoses four candidate mechanisms. Nothing is corrected, refitted or tuned:
no new model, no fix, no change to any earlier result.

H1 Past-discharge source. In training, ``past_*`` features come from the GloFAS
reanalysis (consolidated_v4, ends 2025-05-31); operationally from the archived
day-0 forecast (forecast_v4). (a) Day-by-day ratio day0/reanalysis on the
overlap 2023-06-01..2025-05-31, overall, by reanalysis tercile and for high
flows (reanalysis >= its development P90). (b) The frozen model, fitted once on
development reforecast issues exactly as in experiment 05, scored on operational
issues up to 2025-05-31 twice: with the day-0 past discharge (as issued) and
with the reanalysis as past discharge, on the same days.

H2 Ensemble system. Reforecasts (2004-2023, 11 members) against operational
forecasts (2023-08+, 11-member subset). Where issue dates overlap (lead 1,
2023-08-01..2023-11-25) the ensemble medians are compared day by day. Each
system is also compared with the reanalysis on its own period (reforecast
2004-2022, operational 2023-08..2025-05): ratio of the ensemble median (lead
1/3/7) to the reanalysis discharge of the valid day, overall and for high flows
(reanalysis >= its development P90). No stage enters this comparison.

H3 Gauge rating shift. Observed stage and observed discharge (SIH ``Gasto``
column) at BDCTB. Caveat: SIH discharge may itself be derived from level
through CONAGUA's own rating, so the stage-at-fixed-discharge and the
discharge comparisons are not fully independent. Stage-discharge relation by
period (1998-2016, 2017-2022, 2023-2025): stage at fixed discharges
(1500/3000/4500/6000 m3/s, binned medians) and the year-by-year mean residual
of an isotonic rating fitted on 1998-2016. Independently of the model: the
GloFAS reanalysis-to-stage relation by period (isotonic rating fitted on
1998-2016, mean residual by year on observed >= 15 m), split into a gauge
rating part and a GloFAS discharge part by comparing the reanalysis with the
observed discharge (distribution medians and P90, robust to the two-day GloFAS
peak lead, L4).

H4 Climate/sample. High-stage day counts per period and the shape of the high
flows (event runs of consecutive days >= 15 m, duration, peak), to see whether
the operational period simply has fewer or shorter events.

Output: docs/results/07-diagnosis.json and docs/results/07-diagnosis.md.

    uv run python experiments/07_diagnosis.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from riverforecast import conagua, data, features
from riverforecast import evaluation as ev
from riverforecast.model import ConformalQuantileModel

CODE = "BDCTB"
OUT = Path("docs/results")
BIAS_MIN = 15.0  # m; high-stage days of the L21 bias check
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
FIXED_DISCHARGE = (1500, 3000, 4500, 6000)  # m3/s
BIN_HALF_WIDTH = 250.0  # m3/s; window of the binned stage medians
PERIODS = (("1998-2016", 1998, 2016), ("2017-2022", 2017, 2022), ("2023-2025", 2023, 2025))


def observed_discharge(raw: Path = data.RAW) -> pd.Series:
    """Observed daily discharge (m3/s) from the CONAGUA hydrometric file."""
    path = raw / "conagua" / "hidros" / f"{CODE}.csv"
    parsed = conagua.parse_sih_csv(path.read_text(encoding="utf-8"))
    return parsed.data["discharge_m3s"].dropna()


def period_mask(index: pd.DatetimeIndex, low: int, high: int) -> np.ndarray:
    return (index.year >= low) & (index.year <= high)


# ---------------------------------------------------------------- H1: past source


def past_source_ratios(day0: pd.Series, reanalysis: pd.Series, dev_p90: float) -> dict:
    """Day-by-day ratio of the day-0 archive to the reanalysis on their overlap."""
    overlap = day0.index.intersection(reanalysis.index)
    ratio = (day0.loc[overlap] / reanalysis.loc[overlap]).dropna()

    def block(mask: np.ndarray | pd.Series) -> dict:
        part = ratio[mask]
        return {
            "n": int(part.size),
            "median_ratio": float(part.median()),
            "mean_ratio": float(part.mean()),
        }

    terciles = reanalysis.loc[overlap].quantile([1 / 3, 2 / 3])
    rean = reanalysis.loc[overlap]
    return {
        "overlap": f"{overlap.min():%Y-%m-%d}..{overlap.max():%Y-%m-%d}",
        "reanalysis_m3s": {
            "median": float(rean.median()),
            "mean": float(rean.mean()),
            "tercile_low_high_m3s": [float(terciles.iloc[0]), float(terciles.iloc[1])],
        },
        "overall": block(np.ones(len(ratio), dtype=bool)),
        "by_reanalysis_tercile": {
            "low": block(rean <= terciles.iloc[0]),
            "middle": block((rean > terciles.iloc[0]) & (rean < terciles.iloc[1])),
            "high": block(rean >= terciles.iloc[1]),
        },
        "high_flows_reanalysis_ge_dev_p90": block(rean >= dev_p90),
    }


def fit_development_model(
    lead: int,
    reforecast_summary: pd.DataFrame,
    reanalysis: pd.Series,
    stage: pd.Series,
    development: pd.DatetimeIndex,
) -> ConformalQuantileModel:
    """The frozen model of experiment 05, fitted once on development issues."""
    train = features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")
    train = train[train["valid"].isin(development)]
    return ConformalQuantileModel.fit(
        train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
    )


def past_source_model_scores(
    lead: int,
    model: ConformalQuantileModel,
    operational_summary: pd.DataFrame,
    day0: pd.Series,
    reanalysis: pd.Series,
    stage: pd.Series,
    last_valid: pd.Timestamp,
) -> list[dict]:
    """Frozen model on operational issues with day-0 vs reanalysis past discharge."""
    tables = {
        name: table[table["valid"].isin(stage.index)]
        for name, table in (
            ("day0", features.build(operational_summary, day0, lead)),
            ("reanalysis", features.build(operational_summary, reanalysis, lead)),
        )
    }
    shared = tables["day0"].index.intersection(tables["reanalysis"].index)
    shared = shared[shared <= last_valid - pd.Timedelta(days=lead)]
    rows = []
    for name, table in tables.items():
        rows_part = table.loc[shared]
        valid = pd.DatetimeIndex(rows_part["valid"])
        observed = stage.loc[valid].to_numpy()
        _, q50, _ = model.predict(rows_part[features.COLUMNS])
        high = observed >= BIAS_MIN
        rows.append(
            {
                "lead_days": lead,
                "past_source": name,
                "n": int(len(rows_part)),
                "mae": float(np.mean(np.abs(q50 - observed))),
                "high_stage_n": int(high.sum()),
                "high_stage_mean_error": float(np.mean(q50[high] - observed[high])),
            }
        )
    return rows


def past_source_yearly_errors(
    lead: int,
    operational_summary: pd.DataFrame,
    day0: pd.Series,
    stage: pd.Series,
    last_valid: pd.Timestamp,
    models: dict[int, ConformalQuantileModel],
) -> dict[int, pd.Series]:
    """Per-year mean q50 error on observed >= 15 m, operational issues, day-0 source."""
    table = features.build(operational_summary, day0, lead)
    table = table[table["valid"].isin(stage.index)]
    table = table[table["valid"] <= last_valid - pd.Timedelta(days=lead)]
    valid = pd.DatetimeIndex(table["valid"])
    observed = stage.loc[valid].to_numpy()
    _, q50, _ = models[lead].predict(table[features.COLUMNS])
    error = pd.Series(q50 - observed, index=valid)
    high = error[observed >= BIAS_MIN]
    return {int(year): part for year, part in high.groupby(high.index.year)}


# ---------------------------------------------------------------- H2: ensemble system


def ensemble_vs_reanalysis(
    summary: pd.DataFrame, reanalysis: pd.Series, dev_p90: float, valid_until: pd.Timestamp
) -> list[dict]:
    """Ratio of the ensemble median to the reanalysis of the valid day, per lead."""
    rows = []
    for lead in (1, 3, 7):
        part = summary[(summary["lead_days"] == lead) & (summary["valid"] <= valid_until)]
        rean_valid = reanalysis.reindex(part["valid"]).to_numpy()
        ok = np.isfinite(rean_valid)
        ratio = part["q50"].to_numpy()[ok] / rean_valid[ok]
        high = rean_valid[ok] >= dev_p90
        rows.append(
            {
                "lead_days": lead,
                "n": int(ok.sum()),
                "median_ratio": float(np.median(ratio)),
                "mean_ratio": float(np.mean(ratio)),
                "high_n": int(high.sum()),
                "high_median_ratio": float(np.median(ratio[high])),
                "high_mean_ratio": float(np.mean(ratio[high])),
            }
        )
    return rows


def issue_overlap_medians(
    reforecast_summary: pd.DataFrame, operational_summary: pd.DataFrame
) -> dict | None:
    """Operational vs reforecast ensemble median on the same issue dates (lead 1)."""
    ref = reforecast_summary[reforecast_summary["lead_days"] == 1].set_index("issue")["q50"]
    op = operational_summary[operational_summary["lead_days"] == 1].set_index("issue")["q50"]
    common = ref.index.intersection(op.index)
    if common.empty:
        return None
    ratio = (op.loc[common] / ref.loc[common]).to_numpy()
    return {
        "lead_days": 1,
        "issues": f"{common.min():%Y-%m-%d}..{common.max():%Y-%m-%d}",
        "n": int(len(common)),
        "median_ratio_op_over_reforecast": float(np.median(ratio)),
        "mean_ratio_op_over_reforecast": float(np.mean(ratio)),
        "median_abs_difference_m3s": float(np.median(np.abs(op.loc[common] - ref.loc[common]))),
    }


# ---------------------------------------------------------------- H3: rating shift


def stage_at_fixed_discharge(
    stage: pd.Series, discharge: pd.Series, fixed: tuple[float, ...]
) -> list[dict]:
    """Median observed stage within +-BIN_HALF_WIDTH of each fixed discharge, by period."""
    pairs = pd.concat([stage, discharge], axis=1, keys=["h", "q"], sort=True).dropna()
    rows = []
    for label, low, high in PERIODS:
        part = pairs[period_mask(pairs.index, low, high)]
        for q0 in fixed:
            near = part[np.abs(part["q"] - q0) <= BIN_HALF_WIDTH]
            rows.append(
                {
                    "period": label,
                    "discharge_m3s": q0,
                    "n": int(len(near)),
                    "median_stage_m": float(near["h"].median()) if len(near) else None,
                }
            )
    return rows


def rating_residuals_by_year(
    stage: pd.Series, discharge: pd.Series, min_stage: float | None
) -> list[dict]:
    """Mean residual (observed - fitted) per year of an isotonic rating on 1998-2016."""
    pairs = pd.concat([stage, discharge], axis=1, keys=["h", "q"], sort=True).dropna()
    if min_stage is not None:
        pairs = pairs[pairs["h"] >= min_stage]
    train = pairs[period_mask(pairs.index, 1998, 2016)]
    curve = IsotonicRegression(out_of_bounds="clip").fit(train["q"], train["h"])
    residual = pairs["h"] - curve.predict(pairs["q"])
    rows = []
    for label, low, high in PERIODS:
        mask = period_mask(pairs.index, low, high)
        rows.append(
            {
                "period": label,
                "n": int(mask.sum()),
                "mean_residual_m": float(residual[mask].mean()),
            }
        )
    by_year = [
        {"year": int(year), "n": int(len(part)), "mean_residual_m": float(part.mean())}
        for year, part in residual.groupby(residual.index.year)
    ]
    return rows, by_year, float(curve.predict([[6000]])[0])


def discharge_source_distributions(
    reanalysis: pd.Series, obs_discharge: pd.Series
) -> tuple[list[dict], list[dict]]:
    """GloFAS reanalysis vs observed gauge discharge, by period and year.

    Distribution comparison (medians and 90th percentiles), not day-by-day:
    the GloFAS reanalysis peaks about two days early at this cell (L4), so a
    lag-0 ratio would mix timing error into the level comparison. This splits
    the GloFAS-to-stage shift (H3) into a gauge rating part and a GloFAS
    discharge part.
    """
    pairs = pd.concat(
        [reanalysis, obs_discharge], axis=1, keys=["glofas", "obs"], sort=True
    ).dropna()
    pairs = pairs[pairs["obs"] > 0]

    def stats(part: pd.DataFrame) -> dict:
        return {
            "n": int(len(part)),
            "glofas_median_m3s": float(part["glofas"].median()),
            "obs_median_m3s": float(part["obs"].median()),
            "median_ratio": float(part["glofas"].median() / part["obs"].median()),
            "glofas_p90_m3s": float(part["glofas"].quantile(0.9)),
            "obs_p90_m3s": float(part["obs"].quantile(0.9)),
            "p90_ratio": float(part["glofas"].quantile(0.9) / part["obs"].quantile(0.9)),
        }

    rows = []
    for label, low, high in PERIODS:
        rows.append({"period": label, **stats(pairs[period_mask(pairs.index, low, high)])})
    by_year = [{"year": int(year), **stats(part)} for year, part in pairs.groupby(pairs.index.year)]
    return rows, by_year


# ---------------------------------------------------------------- H4: sample


def high_water_sample(stage: pd.Series, high_water: float) -> list[dict]:
    """High-stage day counts and event-shape summary per period."""
    rows = []
    for label, low, high in PERIODS:
        part = stage[period_mask(stage.index, low, high)]
        events: list[pd.Series] = []
        current: list[pd.Timestamp] = []
        for day, level in part.items():
            if level >= BIAS_MIN:
                current.append(day)
            elif current:
                events.append(part.loc[current])
                current = []
        if current:
            events.append(part.loc[current])
        durations = [len(event) for event in events]
        peaks = [event.max() for event in events]
        rows.append(
            {
                "period": label,
                "n_days": int(len(part)),
                "n_ge_15m": int((part >= BIAS_MIN).sum()),
                "n_ge_high_water": int((part >= high_water).sum()),
                "n_events_ge_15m": len(events),
                "mean_event_days": float(np.mean(durations)) if durations else None,
                "median_event_days": float(np.median(durations)) if durations else None,
                "mean_peak_m": float(np.mean(peaks)) if peaks else None,
            }
        )
    return rows


# ---------------------------------------------------------------- driver


def main() -> None:
    stage = data.stage(CODE)
    development, _ = ev.split_days(stage.index)
    high_water = float(stage.loc[development].quantile(0.9))
    reanalysis = data.glofas_reanalysis(CODE)
    day0 = data.glofas_day0(CODE)
    reforecast_summary = data.glofas_reforecast_summary(CODE)
    operational_summary = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    dev_p90 = float(reanalysis[period_mask(reanalysis.index, 1998, 2016)].quantile(0.9))
    reanalysis_end = reanalysis.index.max()
    obs_discharge = observed_discharge()

    # H1 ------------------------------------------------------------------
    h1_ratios = past_source_ratios(day0, reanalysis, dev_p90)
    h1_model: list[dict] = []
    models: dict[int, ConformalQuantileModel] = {}
    for lead in (1, 3, 7):
        models[lead] = fit_development_model(
            lead, reforecast_summary, reanalysis, stage, development
        )
        h1_model += past_source_model_scores(
            lead, models[lead], operational_summary, day0, reanalysis, stage, reanalysis_end
        )

    # H2 ------------------------------------------------------------------
    h2_overlap = issue_overlap_medians(reforecast_summary, operational_summary)
    h2_reforecast = ensemble_vs_reanalysis(
        reforecast_summary[
            (reforecast_summary["issue"] >= "2004-01-01")
            & (reforecast_summary["valid"] <= "2022-12-31")
        ],
        reanalysis,
        dev_p90,
        reanalysis_end,
    )
    h2_operational = ensemble_vs_reanalysis(
        operational_summary, reanalysis, dev_p90, reanalysis_end
    )

    # H3 ------------------------------------------------------------------
    h3_fixed = stage_at_fixed_discharge(stage, obs_discharge, FIXED_DISCHARGE)
    h3_rating, h3_rating_years, stage_at_6000 = rating_residuals_by_year(stage, obs_discharge, None)
    h3_glofas, h3_glofas_years, _ = rating_residuals_by_year(stage, reanalysis, BIAS_MIN)
    h3_source, h3_source_years = discharge_source_distributions(reanalysis, obs_discharge)
    h1_model_yearly = [
        {
            "lead_days": lead,
            "year": int(year),
            "mean_error": float(part.mean()),
            "n": int(len(part)),
        }
        for lead in (1, 3, 7)
        for year, part in past_source_yearly_errors(
            lead, operational_summary, day0, stage, reanalysis_end, models
        ).items()
    ]

    # H4 ------------------------------------------------------------------
    h4 = high_water_sample(stage, high_water)

    result = {
        "high_water_m": high_water,
        "reanalysis_dev_p90_m3s": dev_p90,
        "h1_past_source": {
            "day0_vs_reanalysis": h1_ratios,
            "frozen_model_operational": h1_model,
        },
        "h2_ensemble": {
            "issue_overlap": h2_overlap,
            "reforecast_2004_2022_vs_reanalysis": h2_reforecast,
            "operational_2023_08_2025_05_vs_reanalysis": h2_operational,
        },
        "h3_rating": {
            "stage_at_fixed_discharge": h3_fixed,
            "observed_rating_1998_2016_residuals": h3_rating,
            "observed_rating_residuals_by_year": h3_rating_years,
            "observed_rating_stage_at_6000_m3s_1998_2016": stage_at_6000,
            "glofas_rating_1998_2016_residuals_ge_15m": h3_glofas,
            "glofas_rating_residuals_by_year_ge_15m": h3_glofas_years,
            "reanalysis_vs_observed_discharge": h3_source,
            "reanalysis_vs_observed_discharge_by_year": h3_source_years,
        },
        "h4_sample": h4,
        "h1_high_stage_error_by_year": h1_model_yearly,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "07-diagnosis.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


def ratio_row(row: dict) -> str:
    return (
        f"| {row['lead_days']} | {row['n']} | {row['median_ratio']:.3f} "
        f"| {row['mean_ratio']:.3f} | {row['high_n']} | {row['high_median_ratio']:.3f} "
        f"| {row['high_mean_ratio']:.3f} |"
    )


def write_report(result: dict) -> None:
    h1 = result["h1_past_source"]
    lines = [
        "# Experiment 07: diagnosis of the operational over-forecast",
        "",
        "Generated by `experiments/07_diagnosis.py`. Diagnosis only: the frozen",
        "model of experiments 01-06 is unchanged and nothing is corrected or",
        f"refitted. High water: stage >= {result['high_water_m']:.2f} m; the",
        "high-stage bias is the mean q50 error on observed >= 15 m, as in L21.",
        f"Reanalysis discharge development P90: {result['reanalysis_dev_p90_m3s']:.0f} m3/s.",
        "",
        "## H1 Past-discharge source (day-0 archive vs reanalysis)",
        "",
        f"Overlap {h1['day0_vs_reanalysis']['overlap']}, "
        f"{h1['day0_vs_reanalysis']['overall']['n']} days. Ratio = day0 / reanalysis.",
        "",
        "| subset | n | median ratio | mean ratio |",
        "| --- | --- | --- | --- |",
    ]
    subsets = {
        "overall": h1["day0_vs_reanalysis"]["overall"],
        **{
            f"reanalysis tercile {name}": block
            for name, block in h1["day0_vs_reanalysis"]["by_reanalysis_tercile"].items()
        },
        "high flows (rean >= dev P90)": h1["day0_vs_reanalysis"][
            "high_flows_reanalysis_ge_dev_p90"
        ],
    }
    for name, block in subsets.items():
        lines.append(
            f"| {name} | {block['n']} | {block['median_ratio']:.3f} | {block['mean_ratio']:.3f} |"
        )
    lines += [
        "",
        "Frozen model on operational issues up to 2025-05-31, past discharge from the",
        "day-0 archive (as issued) vs from the reanalysis, same days:",
        "",
        "| lead | past source | n | MAE (m) | n (obs >= 15 m) | high-stage mean error (m) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in h1["frozen_model_operational"]:
        lines.append(
            f"| {row['lead_days']} | {row['past_source']} | {row['n']} | {row['mae']:.3f} "
            f"| {row['high_stage_n']} | {row['high_stage_mean_error']:+.3f} |"
        )
    lines += [
        "",
        "High-stage mean q50 error by year (day-0 source):",
        "",
        "| lead | year | n | mean error (m) |",
        "| --- | --- | --- | --- |",
    ]
    for row in result["h1_high_stage_error_by_year"]:
        lines.append(
            f"| {row['lead_days']} | {row['year']} | {row['n']} | {row['mean_error']:+.3f} |"
        )
    h2 = result["h2_ensemble"]
    lines += [
        "",
        "## H2 Ensemble system (reforecast vs operational, no stage)",
        "",
    ]
    if h2["issue_overlap"]:
        ov = h2["issue_overlap"]
        lines += [
            f"Issue-date overlap (lead 1): {ov['issues']}, n={ov['n']}. "
            f"Median operational/reforecast median ratio "
            f"{ov['median_ratio_op_over_reforecast']:.3f} "
            f"(mean {ov['mean_ratio_op_over_reforecast']:.3f}); median |difference| "
            f"{ov['median_abs_difference_m3s']:.0f} m3/s.",
        ]
    else:
        lines += ["No overlapping issue dates between reforecasts and operational forecasts."]
    lines += [
        "",
        "Ensemble median / reanalysis discharge of the valid day, each system on its",
        "own period (high = reanalysis >= its development P90):",
        "",
        "| system | lead | n | median ratio | mean ratio | n high | median high | mean high |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, rows in (
        ("reforecast 2004-2022", h2["reforecast_2004_2022_vs_reanalysis"]),
        ("operational 2023-08..2025-05", h2["operational_2023_08_2025_05_vs_reanalysis"]),
    ):
        for row in rows:
            lines.append("| " + name + " | " + ratio_row(row)[2:])
    h3 = result["h3_rating"]
    lines += [
        "",
        "## H3 Gauge rating shift (observed stage vs observed discharge, BDCTB)",
        "",
        "Caveat: the SIH `Gasto` discharge may itself be derived from level through",
        "CONAGUA's own rating, so the two comparisons below are not fully independent.",
        "",
        "Median observed stage within +-250 m3/s of each fixed discharge:",
        "",
        "| period | discharge (m3/s) | n | median stage (m) |",
        "| --- | --- | --- | --- |",
    ]
    for row in h3["stage_at_fixed_discharge"]:
        stage_text = "n/a" if row["median_stage_m"] is None else f"{row['median_stage_m']:.2f}"
        lines.append(f"| {row['period']} | {row['discharge_m3s']} | {row['n']} | {stage_text} |")
    lines += [
        "",
        "Isotonic rating fitted on observed pairs 1998-2016 (stage at 6000 m3/s: "
        f"{h3['observed_rating_stage_at_6000_m3s_1998_2016']:.2f} m). "
        "Mean residual (observed - fitted) by period:",
        "",
        "| period | n | mean residual (m) |",
        "| --- | --- | --- |",
    ]
    for row in h3["observed_rating_1998_2016_residuals"]:
        lines.append(f"| {row['period']} | {row['n']} | {row['mean_residual_m']:+.3f} |")
    lines += [
        "",
        "Mean residual by year:",
        "",
        "| year | n | mean residual (m) |",
        "| --- | --- | --- |",
    ]
    for row in h3["observed_rating_residuals_by_year"]:
        lines.append(f"| {row['year']} | {row['n']} | {row['mean_residual_m']:+.3f} |")
    lines += [
        "",
        "GloFAS reanalysis-to-stage relation, isotonic rating on 1998-2016 pairs,",
        "mean residual on observed >= 15 m:",
        "",
        "| period | n | mean residual (m) |",
        "| --- | --- | --- |",
    ]
    for row in h3["glofas_rating_1998_2016_residuals_ge_15m"]:
        lines.append(f"| {row['period']} | {row['n']} | {row['mean_residual_m']:+.3f} |")
    lines += [
        "",
        "Mean residual by year (observed >= 15 m):",
        "",
        "| year | n | mean residual (m) |",
        "| --- | --- | --- |",
    ]
    for row in h3["glofas_rating_residuals_by_year_ge_15m"]:
        lines.append(f"| {row['year']} | {row['n']} | {row['mean_residual_m']:+.3f} |")
    lines += [
        "",
        "GloFAS reanalysis vs observed gauge discharge (distribution medians and P90;",
        "splits the GloFAS-to-stage shift into gauge rating and GloFAS discharge):",
        "",
        "| period | n | GloFAS median | obs median | median ratio | GloFAS P90 | obs P90 "
        "| P90 ratio |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in h3["reanalysis_vs_observed_discharge"]:
        lines.append(
            f"| {row['period']} | {row['n']} | {row['glofas_median_m3s']:.0f} "
            f"| {row['obs_median_m3s']:.0f} | {row['median_ratio']:.3f} "
            f"| {row['glofas_p90_m3s']:.0f} | {row['obs_p90_m3s']:.0f} "
            f"| {row['p90_ratio']:.3f} |"
        )
    lines += [
        "",
        "GloFAS/observed discharge ratios by year:",
        "",
        "| year | n | median ratio | P90 ratio |",
        "| --- | --- | --- | --- |",
    ]
    for row in h3["reanalysis_vs_observed_discharge_by_year"]:
        lines.append(
            f"| {row['year']} | {row['n']} | {row['median_ratio']:.3f} | {row['p90_ratio']:.3f} |"
        )
    lines += [
        "",
        "## H4 Climate/sample",
        "",
        "High-stage days per period; an event is a run of consecutive days >= 15 m.",
        "",
        "| period | n days | >= 15 m | >= 17.83 m | events >= 15 m | mean event days "
        "| median event days | mean peak (m) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["h4_sample"]:
        mean_days = "n/a" if row["mean_event_days"] is None else f"{row['mean_event_days']:.1f}"
        med_days = "n/a" if row["median_event_days"] is None else f"{row['median_event_days']:.1f}"
        lines.append(
            f"| {row['period']} | {row['n_days']} | {row['n_ge_15m']} "
            f"| {row['n_ge_high_water']} | {row['n_events_ge_15m']} | {mean_days} "
            f"| {med_days} | {row['mean_peak_m']:.2f} |"
        )
    lines += [""]
    (OUT / "07-diagnosis.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
