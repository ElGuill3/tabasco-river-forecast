"""Experiment 08: post-diagnosis re-score, pre-registered in the task log (L24).

The L21 operational warning scores over-warn, and experiment 07 diagnosed the
mechanism: a year-specific GloFAS discharge error (running ~25-33% high against
the gauge in 2024, low in 2025), not a systematic shift that a correction
estimated before 2023 could remove. Following the pre-registration written in
L24 before any re-score (no variants, nothing tuned on 2023-2025 stage):

1. M1 = the same conformal quantile model, features and hyperparameters,
   refitted on every reforecast issue whose target day is 1998-01-01..2022-12-31
   and has observed stage (reanalysis past discharge in training, day-0 in
   operation, as in experiment 05), one model per lead. The GloFAS rating
   reference is refitted the same way; climatology is fitted on 1998-2022 stage.
2. Warning rules on M1: out-of-fold predictions over 1998-2022 (folds of four
   whole years, 14-day buffer, as in experiment 06), raw exceedance probability
   recalibrated by isotonic regression on those out-of-fold predictions,
   t* = bias-one threshold; R0 = q50 >= 17.83 m, R1 = calibrated p >= t*,
   R2 = R1 on two consecutive issues. High water stays 17.83 m (the original
   development P90 of L11); it is not recomputed.
3. One re-score on the operational target days of experiment 05 (2023-08-02..
   2025-09-03, 11-member ensemble summary, day-0 past discharge): climatology,
   rating, the frozen model of 05 (refitted on 1998-2016 exactly as there, for
   reference on identical rows) and M1; and R0/R1/R2 under the frozen model and
   under M1 (the frozen calibrator and t* are re-derived on 1998-2016
   out-of-fold predictions exactly as in experiment 06). Reported once, next to
   L19/L21, labelled post-diagnosis. Expectation stated in L24: a small gain
   (about 0.1 m at high stage); the 2024 GloFAS error remains. Nothing else is
   tried on 2023-2025.

    uv run python experiments/08_rescore.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from riverforecast import data, features, warning
from riverforecast import evaluation as ev
from riverforecast.baselines import Climatology, Rating
from riverforecast.model import ConformalQuantileModel

CODE = "BDCTB"
OUT = Path("docs/results")
HIGH_WATER = 17.83  # development P90 (L11); kept fixed, never recomputed
M1_END = "2022-12-31"
BIAS_MIN = 15.0  # m; days with observed stage above this enter the high-stage bias
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
SCORES = ["n", "mae", "rmse", "pinball_q50", "coverage_80", "width_80", "high_stage_bias"]
CONTINGENCY = [
    "n",
    "events",
    "hits",
    "misses",
    "false_alarms",
    "pod",
    "far",
    "pofd",
    "bias",
    "peirce",
]


def out_of_fold(table: pd.DataFrame, stage: pd.Series) -> pd.DataFrame:
    """Out-of-fold conformal quantiles on folds of four whole years, 14-day buffer."""
    by_valid = table.set_index("valid")
    parts = []
    for train, held in ev.year_folds(pd.DatetimeIndex(by_valid.index), 4, 14):
        model = ConformalQuantileModel.fit(
            by_valid.loc[train, features.COLUMNS],
            stage.loc[train],
            pd.DatetimeIndex(train),
        )
        q10, q50, q90 = model.predict(by_valid.loc[held, features.COLUMNS])
        parts.append(pd.DataFrame({"q10": q10, "q50": q50, "q90": q90}, index=held))
    return pd.concat(parts).sort_index()


def calibrate(oof: pd.DataFrame, event: np.ndarray) -> tuple[IsotonicRegression, np.ndarray]:
    """Isotonic calibrator fitted on the raw exceedance probability of the OOF quantiles."""
    raw = warning.exceedance_probability(oof.q10, oof.q50, oof.q90, HIGH_WATER)
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw, event)
    return calibrator, calibrator.predict(raw)


def high_stage_bias(observed: np.ndarray, q50: np.ndarray) -> float:
    """Mean q50 - observed on days with observed stage >= BIAS_MIN."""
    high = observed >= BIAS_MIN
    if not high.any():
        return float("nan")
    return float(np.mean(np.asarray(q50)[high] - observed[high]))


def quantile_scores(
    name: str,
    lead: int,
    period: str,
    observed: np.ndarray,
    quantiles: tuple[np.ndarray, np.ndarray, np.ndarray],
    years: np.ndarray,
) -> tuple[dict, list[dict], list[dict]]:
    """One overall score row plus per-year MAE and per-year high-stage-bias rows."""
    q10, q50, q90 = (np.asarray(q) for q in quantiles)
    row = {
        "forecast": name,
        "lead_days": lead,
        "period": period,
        "n": int(observed.size),
        "mae": ev.mae(observed, q50),
        "rmse": ev.rmse(observed, q50),
        "pinball_q50": ev.pinball(observed, q50, 0.5),
        "coverage_80": ev.coverage(observed, q10, q90),
        "width_80": ev.width(q10, q90),
        "high_stage_bias": high_stage_bias(observed, q50),
    }
    errors = pd.Series(np.abs(q50 - observed), index=years)
    yearly_mae = [
        {"forecast": name, "lead_days": lead, "year": int(year), "mae": float(group.mean())}
        for year, group in errors.groupby(level=0)
    ]
    yearly_bias = []
    for year in sorted(set(years)):
        inside = years == year
        yearly_bias.append(
            {
                "forecast": name,
                "lead_days": lead,
                "year": int(year),
                "bias": high_stage_bias(observed[inside], q50[inside]),
            }
        )
    return row, yearly_mae, yearly_bias


def rule_scores(warn: np.ndarray, event: np.ndarray) -> dict:
    """Contingency scores of one warning rule."""
    c = warning.contingency(warn, event)
    return {
        "n": int(event.size),
        "events": c.events,
        "hits": c.hits,
        "misses": c.misses,
        "false_alarms": c.false_alarms,
        "pod": c.pod,
        "far": c.false_alarm_ratio,
        "pofd": c.pofd,
        "bias": c.frequency_bias,
        "peirce": c.peirce,
    }


def probability_scores(p: np.ndarray, event: np.ndarray, base_rate: float) -> dict:
    """Brier, BSS against ``base_rate`` and ROC AUC of one calibrated probability."""
    return {
        "brier": warning.brier_score(p, event),
        "bss": warning.brier_skill_score(p, event, base_rate),
        "roc_auc": warning.roc_auc(p, event),
    }


def fmt(value, digits: int = 3) -> str:
    """Markdown cell: n/a for NaN, fixed digits otherwise."""
    if isinstance(value, float) and np.isnan(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main() -> None:
    stage = data.stage(CODE)
    m1_days = stage.index[(stage.index >= "1998-01-01") & (stage.index <= M1_END)]
    dev_days, _ = ev.split_days(stage.index)
    reanalysis = data.glofas_reanalysis(CODE)
    reforecast_summary = data.glofas_reforecast_summary(CODE)
    operational_summary = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    day0 = data.glofas_day0(CODE)
    climatology = Climatology.fit(stage.loc[m1_days])

    development: dict = {}
    score_rows: list[dict] = []
    yearly_mae: list[dict] = []
    yearly_bias: list[dict] = []
    rule_rows: list[dict] = []
    yearly_rules: list[dict] = []
    prob_rows: list[dict] = []
    yearly_prob: list[dict] = []

    for lead in (1, 3, 7):
        table = features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")
        m1_train = table[table["valid"].isin(m1_days)]
        frozen_train = table[table["valid"].isin(dev_days)]

        # --- M1 and its warning chain, all fitted on 1998-2022 targets --------
        m1 = ConformalQuantileModel.fit(
            m1_train[features.COLUMNS],
            stage.loc[m1_train["valid"]],
            pd.DatetimeIndex(m1_train["valid"]),
        )
        rating = Rating.fit(np.exp(m1_train["fc_q50"]), stage.loc[m1_train["valid"]])
        m1_oof = out_of_fold(m1_train, stage)
        m1_event = (stage.loc[m1_oof.index] >= HIGH_WATER).to_numpy()
        m1_base_rate = float(np.mean(m1_event))
        m1_calibrator, m1_calibrated = calibrate(m1_oof, m1_event)
        m1_t_star = warning.bias_one_threshold(m1_calibrated, m1_event)
        development[f"lead_{lead}"] = {
            "n": int(len(m1_oof)),
            "events": int(m1_event.sum()),
            "base_rate": m1_base_rate,
            "t_star": m1_t_star,
            **probability_scores(m1_calibrated, m1_event, m1_base_rate),
        }

        # --- frozen model of 05/06, refitted exactly as there -----------------
        frozen = ConformalQuantileModel.fit(
            frozen_train[features.COLUMNS],
            stage.loc[frozen_train["valid"]],
            pd.DatetimeIndex(frozen_train["valid"]),
        )
        frozen_oof = out_of_fold(frozen_train, stage)
        frozen_event = (stage.loc[frozen_oof.index] >= HIGH_WATER).to_numpy()
        frozen_calibrator, frozen_calibrated = calibrate(frozen_oof, frozen_event)
        frozen_t_star = warning.bias_one_threshold(frozen_calibrated, frozen_event)
        development[f"lead_{lead}"]["frozen_t_star"] = frozen_t_star

        # --- one re-score on the operational target days of 05 ----------------
        op = features.build(operational_summary, day0, lead)
        op = op[op["valid"].isin(stage.index)]
        observed = stage.loc[op["valid"]].to_numpy()
        years = pd.DatetimeIndex(op["valid"]).year.to_numpy()
        period = f"{min(op['valid']):%Y-%m-%d}..{max(op['valid']):%Y-%m-%d}"

        forecasts = {
            "climatology": climatology.predict(pd.DatetimeIndex(op["valid"])),
            "GloFAS rating": rating.predict(np.exp(op["fc_q50"].to_numpy())),
            "frozen model (05)": frozen.predict(op[features.COLUMNS]),
            "M1": m1.predict(op[features.COLUMNS]),
        }
        for name, quantiles in forecasts.items():
            row, mae_by_year, bias_by_year = quantile_scores(
                name, lead, period, observed, quantiles, years
            )
            score_rows.append(row)
            yearly_mae += mae_by_year
            yearly_bias += bias_by_year

        for name, model, calibrator, t_star in (
            ("frozen", frozen, frozen_calibrator, frozen_t_star),
            ("M1", m1, m1_calibrator, m1_t_star),
        ):
            q10, q50, q90 = model.predict(op[features.COLUMNS])
            p = calibrator.predict(warning.exceedance_probability(q10, q50, q90, HIGH_WATER))
            event = observed >= HIGH_WATER
            r1 = pd.Series(p >= t_star, index=op.index)
            warns = {
                "R0": q50 >= HIGH_WATER,
                "R1": p >= t_star,
                "R2": warning.persistent(r1).to_numpy(),
            }
            for rule, mask in warns.items():
                rule_rows.append(
                    {"model": name, "lead_days": lead, "rule": rule, **rule_scores(mask, event)}
                )
                for year in sorted(set(years)):
                    inside = years == year
                    yearly_rules.append(
                        {
                            "model": name,
                            "lead_days": lead,
                            "year": int(year),
                            "rule": rule,
                            **rule_scores(mask[inside], event[inside]),
                        }
                    )
            prob_rows.append(
                {"model": name, "lead_days": lead, **probability_scores(p, event, m1_base_rate)}
            )
            for year in sorted(set(years)):
                inside = years == year
                year_rate = float(np.mean(event[inside]))
                yearly_prob.append(
                    {
                        "model": name,
                        "lead_days": lead,
                        "year": int(year),
                        "events": int(event[inside].sum()),
                        **probability_scores(p[inside], event[inside], year_rate),
                    }
                )

    result = {
        "high_water_m": HIGH_WATER,
        "pre_registration": "L24",
        "target_days": f"1998-01-01..{M1_END} (training), 2023-08-02..2025-09-03 (re-score)",
        "development_oof": development,
        "scores": score_rows,
        "yearly_mae": yearly_mae,
        "yearly_high_stage_bias": yearly_bias,
        "rules": rule_rows,
        "yearly_rules": yearly_rules,
        "probabilistic": prob_rows,
        "yearly_probabilistic": yearly_prob,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "08-rescore.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


def write_report(result: dict) -> None:
    lines = [
        "# Experiment 08: post-diagnosis re-score",
        "",
        "Generated by `experiments/08_rescore.py`. This is a post-diagnosis",
        "re-score, pre-registered in L24; the period was already consulted in",
        "L19/L21. The model M1 is the same conformal quantile model, features and",
        "hyperparameters, refitted with target days 1998-01-01..2022-12-31 (the",
        "standard final refit before deployment; it absorbs the 2017-2022 rating",
        "shift diagnosed in experiment 07). The warning chain (isotonic",
        "calibrator, t* = bias-one threshold, R0/R1/R2) was re-derived on 1998-2022",
        "out-of-fold predictions only. High water stays 17.83 m. The frozen model",
        "of experiments 05/06 (fitted on 1998-2016) is refitted unchanged for",
        "reference on identical rows. One re-score on the operational target days",
        "of experiment 05 (11-member ensemble summary, day-0 past discharge);",
        "nothing was tuned on 2023-2025 stage. Expectation from L24, stated before",
        "the re-score: a small gain (about 0.1 m at high stage); the 2024 GloFAS",
        "error remains.",
        f"High water: stage >= {result['high_water_m']:.2f} m.",
        "",
        "## Development out-of-fold warning chain (M1, 1998-2022 targets)",
        "",
        "| lead | n | events | base rate | t* | BSS | ROC AUC | frozen t* (L21 check) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lead in (1, 3, 7):
        dev = result["development_oof"][f"lead_{lead}"]
        lines.append(
            f"| {lead} | {dev['n']} | {dev['events']} | {dev['base_rate']:.3f} "
            f"| {dev['t_star']:.3f} | {dev['bss']:.3f} | {dev['roc_auc']:.3f} "
            f"| {dev['frozen_t_star']:.3f} |"
        )
    lines += [
        "",
        "## One re-score on the operational target days (2023-08-02..2025-09-03)",
        "",
        "| forecast | lead | period | n | MAE | RMSE | pinball q50 | coverage (80%) "
        "| width | high-stage bias |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["scores"]:
        cells = [row["forecast"], str(row["lead_days"]), row["period"]] + [
            fmt(row[k]) for k in SCORES
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## MAE by year (m)", "", by_year_table(result["yearly_mae"], "mae")]
    lines += [
        "",
        "## High-stage bias by year (mean q50 - observed on observed >= 15 m)",
        "",
        by_year_table(result["yearly_high_stage_bias"], "bias"),
    ]
    lines += [
        "",
        "## Warning rules on the operational target days (event: stage >= 17.83 m)",
        "",
        "| model | lead | rule | n | events | hits | misses | false alarms | POD | FAR "
        "| POFD | bias | Peirce |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["rules"]:
        cells = [row["model"], str(row["lead_days"]), row["rule"]] + [
            fmt(row[k]) for k in CONTINGENCY
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "### Warning rules by year",
        "",
        "| model | lead | year | rule | events | POD | FAR |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["yearly_rules"]:
        lines.append(
            f"| {row['model']} | {row['lead_days']} | {row['year']} | {row['rule']} "
            f"| {row['events']} | {fmt(row['pod'])} | {fmt(row['far'])} |"
        )
    lines += [
        "",
        "## Probabilistic scores of the calibrated exceedance probability",
        "",
        "BSS is against the development out-of-fold base rate of M1 (one common",
        "reference for both models).",
        "",
        "| model | lead | Brier | BSS | ROC AUC |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["probabilistic"]:
        lines.append(
            f"| {row['model']} | {row['lead_days']} | {row['brier']:.3f} "
            f"| {row['bss']:.3f} | {row['roc_auc']:.3f} |"
        )
    lines += [
        "",
        "### By year (BSS against that year's base rate)",
        "",
        "| model | lead | year | events | BSS | ROC AUC |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["yearly_probabilistic"]:
        lines.append(
            f"| {row['model']} | {row['lead_days']} | {row['year']} | {row['events']} "
            f"| {fmt(row['bss'])} | {fmt(row['roc_auc'])} |"
        )
    (OUT / "08-rescore.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def by_year_table(rows: list[dict], value: str) -> str:
    """Pivot of per-year rows: year x (lead, forecast)."""
    frame = pd.DataFrame(rows).pivot_table(
        index="year", columns=["lead_days", "forecast"], values=value
    )
    return frame.round(3).to_markdown()


if __name__ == "__main__":
    main()
