"""Experiment 06: standard probabilistic verification of the high-water warning.

The frozen model (experiments 01-04: conformal quantile model on GloFAS inputs,
``features.COLUMNS``, no rainfall) warns of high water when q50 >= 17.83 m
(development P90). Operationally that rule has a false-alarm ratio of 0.50-0.57
(experiment 05). Following the pre-registered design of the task log (L20,
fixed before any new look at test or operational data), the false alarms are
diagnosed and a warning rule is chosen on development data only, then scored
once on the test and operational periods:

1. Development out-of-fold q10/q50/q90 of the frozen model, folds of four
   whole years with a 14-day buffer, exactly as in experiment 02.
2. Raw exceedance probability of the high-water threshold from the out-of-fold
   quantiles (piecewise-linear CDF through (q10, 0.1), (q50, 0.5), (q90, 0.9)
   with normal tails), recalibrated by isotonic regression fitted on the whole
   development out-of-fold set.
3. Honest development reliability: the calibrator is refitted on the other
   folds (nested cross-fit) before binning.
4. Operating point t*: the probability threshold whose development warning
   frequency equals the event frequency (frequency bias 1).
5. Rules: R0 = current rule (q50 >= 17.83); R1 = calibrated p >= t*; R2 = R1
   on two consecutive issues (Bartholmes et al. 2009 persistence-style).
6. One-time scoring on (i) the 2017+ reforecast test, exactly the target set
   of experiment 04, and (ii) the operational period of experiment 05
   (11-member ensemble summary, day-0 past discharge), with the frozen model
   and the calibrator fitted once on all development data. No bias correction
   from operational data.

Verification measures: contingency table with POD, false-alarm ratio, POFD,
frequency bias and the Peirce skill score; Brier score and Brier skill score
against the development base rate; rank-based ROC area; the reliability table;
and the relative economic value of Richardson (2000). References: Wilks
(2011), Statistical Methods in the Atmospheric Sciences; Jolliffe and
Stephenson (2012), Forecast Verification: A Practitioner's Guide; Richardson
(2000, Meteorol. Appl.) on the cost/loss value; Bartholmes et al. (2009,
Nat. Hazards Earth Syst. Sci.) on EFAS thresholds and persistence. The extreme
dependency score (Ferro and Stephenson 2011) is not reported: with ~10%
base rates it adds little beyond the Peirce score here. Figures are skipped:
matplotlib is not a project dependency.

    uv run python experiments/06_warning.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from riverforecast import data, features, warning
from riverforecast import evaluation as ev
from riverforecast.model import ConformalQuantileModel

CODE = "BDCTB"
OUT = Path("docs/results")
NEAR_MISS = 0.5  # m below the threshold that still counts as a near miss
BIAS_CHECK_MIN = 15.0  # m; days with observed stage above this enter the bias check
ALPHAS = np.round(np.arange(0.05, 1.0, 0.05), 2)
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
    """Out-of-fold conformal quantiles on the same folds as experiment 02."""
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


def raw_probabilities(frame: pd.DataFrame, high_water: float) -> np.ndarray:
    return warning.exceedance_probability(frame.q10, frame.q50, frame.q90, high_water)


def calibrator_fit(p: np.ndarray, event: np.ndarray) -> IsotonicRegression:
    return IsotonicRegression(out_of_bounds="clip").fit(p, event)


def nested_calibrated(frame: pd.DataFrame, event: np.ndarray, high_water: float) -> np.ndarray:
    """Cross-fitted calibrated probabilities: each fold's calibrator sees the other folds."""
    p = raw_probabilities(frame, high_water)
    parts = []
    for train, held in ev.year_folds(pd.DatetimeIndex(frame.index), 4, 14):
        rows_train = frame.index.isin(train)
        rows_held = frame.index.isin(held)
        calibrator = calibrator_fit(p[rows_train], event[rows_train])
        parts.append(calibrator.predict(p[rows_held]))
    return np.concatenate(parts)


def rule_scores(warn: np.ndarray, event: np.ndarray, n: int) -> dict:
    c = warning.contingency(warn, event)
    return {
        "n": n,
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


def probabilistic_scores(p: np.ndarray, event: np.ndarray, base_rate: float, t_star: float) -> dict:
    values = warning.relative_economic_value(p, event, ALPHAS, t_star)
    return {
        "brier": warning.brier_score(p, event),
        "bss": warning.brier_skill_score(p, event, base_rate),
        "roc_auc": warning.roc_auc(p, event),
        "reliability": warning.reliability_table(p, event).round(3).to_dict("records"),
        "rev": {f"{alpha:.2f}": float(value) for alpha, value in zip(ALPHAS, values, strict=True)},
    }


def false_alarm_diagnosis(
    warn: np.ndarray, event: np.ndarray, observed: np.ndarray, high_water: float
) -> dict:
    """Where the false alarms landed: observed stage on warned non-event days."""
    false_alarms = observed[warn & ~event]
    if false_alarms.size == 0:
        return {"n_false_alarms": 0, "within_0.5m": 0, "within_1.0m": 0, "mean_stage": None}
    return {
        "n_false_alarms": int(false_alarms.size),
        "within_0.5m": int(np.sum(false_alarms >= high_water - NEAR_MISS)),
        "within_1.0m": int(np.sum(false_alarms >= high_water - 2 * NEAR_MISS)),
        "mean_stage": float(np.mean(false_alarms)),
    }


def main() -> None:
    stage = data.stage(CODE)
    development, test = ev.split_days(stage.index)
    high_water = float(stage.loc[development].quantile(0.9))
    reanalysis = data.glofas_reanalysis(CODE)
    reforecast_summary = data.glofas_reforecast_summary(CODE)
    operational_summary = data.glofas_operational_summary(
        CODE, members=["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
    )
    day0 = data.glofas_day0(CODE)

    development_result: dict = {}
    rules_rows: list[dict] = []
    prob_rows: list[dict] = []
    diagnosis_rows: list[dict] = []

    def score_block(
        period: str,
        lead: int,
        q50: np.ndarray,
        p: np.ndarray,
        issues: pd.DatetimeIndex,
        observed: np.ndarray,
        t_star: float,
        base_rate: float,
    ) -> dict[str, np.ndarray]:
        """Score the three rules of one period and append all diagnosis rows."""
        event = observed >= high_water
        r1 = pd.Series(p >= t_star, index=issues)
        warns = {
            "R0": q50 >= high_water,
            "R1": p >= t_star,
            "R2": warning.persistent(r1).to_numpy(),
        }
        for name, mask in warns.items():
            rules_rows.append(
                {
                    "period": period,
                    "lead_days": lead,
                    "rule": name,
                    **rule_scores(mask, event, int(q50.size)),
                }
            )
        prob_rows.append(
            {
                "period": period,
                "lead_days": lead,
                **probabilistic_scores(p, event, base_rate, t_star),
            }
        )
        diagnosis_rows.append(
            {
                "period": period,
                "lead_days": lead,
                **false_alarm_diagnosis(warns["R0"], event, observed, high_water),
            }
        )
        high = observed >= BIAS_CHECK_MIN
        diagnosis_rows.append(
            {
                "period": period,
                "lead_days": lead,
                "kind": "mean_error_above_15m",
                "n": int(high.sum()),
                "mean_error": float(np.mean(q50[high] - observed[high])),
            }
        )
        return warns

    for lead in (1, 3, 7):
        # --- development: out-of-fold quantiles, calibrator, threshold -------
        dev_table = features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")
        dev_table = dev_table[
            dev_table["valid"].isin(development) & dev_table["valid"].isin(stage.index)
        ]
        oof = out_of_fold(dev_table, stage)
        observed = stage.loc[oof.index]
        event = (observed >= high_water).to_numpy()
        base_rate = float(np.mean(event))
        calibrator = calibrator_fit(raw_probabilities(oof, high_water), event)
        calibrated = calibrator.predict(raw_probabilities(oof, high_water))
        nested = nested_calibrated(oof, event, high_water)
        t_star = warning.bias_one_threshold(calibrated, event)
        development_result[f"lead_{lead}"] = {
            "n": int(len(oof)),
            "events": int(event.sum()),
            "base_rate": base_rate,
            "t_star": t_star,
            "reliability_nested": warning.reliability_table(nested, event)
            .round(3)
            .to_dict("records"),
            "brier": warning.brier_score(calibrated, event),
            "bss": warning.brier_skill_score(calibrated, event, base_rate),
            "roc_auc": warning.roc_auc(calibrated, event),
        }

        # The frozen model and calibrator, fitted once on all development data.
        model = ConformalQuantileModel.fit(
            dev_table[features.COLUMNS],
            stage.loc[dev_table["valid"]],
            pd.DatetimeIndex(dev_table["valid"]),
        )
        dev_issues = pd.DatetimeIndex(oof.index - pd.Timedelta(days=lead))
        score_block(
            "development",
            lead,
            oof.q50.to_numpy(),
            calibrated,
            dev_issues,
            observed.to_numpy(),
            t_star,
            base_rate,
        )

        # --- one-time scoring: test and operational --------------------------
        for period, table in (
            ("test", features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")),
            ("operational", features.build(operational_summary, day0, lead)),
        ):
            table = table[table["valid"].isin(stage.index)]
            if period == "test":
                table = table[table["valid"].isin(test)]
            q10, q50, q90 = model.predict(table[features.COLUMNS])
            p = calibrator.predict(warning.exceedance_probability(q10, q50, q90, high_water))
            score_block(
                period,
                lead,
                q50,
                p,
                pd.DatetimeIndex(table.index),
                stage.loc[table["valid"]].to_numpy(),
                t_star,
                base_rate,
            )

    OUT.mkdir(parents=True, exist_ok=True)
    result = {
        "high_water_m": high_water,
        "development_oof": development_result,
        "rules": rules_rows,
        "probabilistic": prob_rows,
        "diagnosis": diagnosis_rows,
        "figures": "skipped: matplotlib is not a project dependency",
    }
    (OUT / "06-warning.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


def write_report(result: dict) -> None:
    lines = [
        "# Experiment 06: probabilistic verification of the high-water warning",
        "",
        "Generated by `experiments/06_warning.py`. The frozen model is unchanged;",
        "the exceedance probability of the high-water threshold is recalibrated",
        "with isotonic regression fitted on development out-of-fold predictions,",
        "and the operating point t* (warning frequency = event frequency) and the",
        "persistence rule were chosen on development data only, then scored once",
        "on the 2017+ reforecast test and the operational period of experiment 05.",
        f"High water: stage >= {result['high_water_m']:.2f} m.",
        "",
        "Figures are skipped: matplotlib is not a project dependency.",
        "",
    ]
    alphas = sorted(next(r for r in result["probabilistic"])["rev"])
    for lead in (1, 3, 7):
        dev = result["development_oof"][f"lead_{lead}"]
        lines += [
            f"## Lead {lead} day{'s' if lead > 1 else ''}",
            "",
            f"Development out-of-fold: n={dev['n']}, events={dev['events']} "
            f"(base rate {dev['base_rate']:.3f}); t* = {dev['t_star']:.3f}; "
            f"Brier {dev['brier']:.3f}, BSS {dev['bss']:.3f}, ROC AUC {dev['roc_auc']:.3f}.",
            "",
            "Rules (contingency on stage >= threshold):",
            "",
            "| period | rule | n | events | hits | misses | false alarms | POD | FAR | POFD "
            "| bias | Peirce |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in result["rules"]:
            if row["lead_days"] != lead:
                continue
            cells = [row["period"], row["rule"]] + [
                f"{row[k]:.3f}" if isinstance(row[k], float) else str(row[k]) for k in CONTINGENCY
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines += [
            "",
            "Probabilistic scores of the calibrated exceedance probability:",
            "",
            "| period | Brier | BSS | ROC AUC |",
            "| --- | --- | --- | --- |",
        ]
        for row in result["probabilistic"]:
            if row["lead_days"] != lead:
                continue
            lines.append(
                f"| {row['period']} | {row['brier']:.3f} | {row['bss']:.3f} "
                f"| {row['roc_auc']:.3f} |"
            )
        lines += [
            "",
            "Reliability of the calibrated probability (nested cross-fit for development):",
            "",
            "| period | bin | mean p | observed frequency | count |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in result["probabilistic"]:
            if row["lead_days"] != lead:
                continue
            source = (
                dev["reliability_nested"] if row["period"] == "development" else row["reliability"]
            )
            for bin_row in source:
                lines.append(
                    f"| {row['period']} | {bin_row['bin_low']:.1f}-{bin_row['bin_high']:.1f} "
                    f"| {bin_row['mean_p']:.3f} | {bin_row['frequency']:.3f} "
                    f"| {bin_row['count']} |"
                )
        lines += [
            "",
            "Relative economic value at t*:",
            "",
            "| period | " + " | ".join(alphas) + " |",
            "| ---" + " | ---" * len(alphas) + " |",
        ]
        for row in result["probabilistic"]:
            if row["lead_days"] != lead:
                continue
            values = [
                f"{row['rev'][a]:.2f}" if row["rev"][a] == row["rev"][a] else "n/a" for a in alphas
            ]
            lines.append(f"| {row['period']} | " + " | ".join(values) + " |")
        lines += [
            "",
            "Diagnosis of the R0 false alarms and of the median bias:",
            "",
            "| period | false alarms | within 0.5 m | within 1.0 m | mean stage on FA days "
            "| n (obs >= 15 m) | mean q50 error (m) |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for period in ("development", "test", "operational"):
            fa = next(
                d
                for d in result["diagnosis"]
                if d["lead_days"] == lead and d["period"] == period and "n_false_alarms" in d
            )
            bias = next(
                d
                for d in result["diagnosis"]
                if d["lead_days"] == lead
                and d["period"] == period
                and d.get("kind") == "mean_error_above_15m"
            )
            mean_stage = "n/a" if fa["mean_stage"] is None else f"{fa['mean_stage']:.2f}"
            lines.append(
                f"| {period} | {fa['n_false_alarms']} | {fa['within_0.5m']} "
                f"| {fa['within_1.0m']} | {mean_stage} | {bias['n']} "
                f"| {bias['mean_error']:+.3f} |"
            )
        lines += [""]
    (OUT / "06-warning.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
