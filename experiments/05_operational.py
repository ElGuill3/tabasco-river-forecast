"""Experiment 05: the frozen model on real GloFAS operational forecasts.

The frozen model (experiments 01-04: conformal quantile model on GloFAS inputs,
no rainfall) is scored the way it would be used in operations: on the GloFAS
forecasts actually issued between 2023-08-01 and 2026-10-13, with past
discharge taken from the archived day-0 forecasts (``forecast_v4``) rather than
the reanalysis, and without changing any modelling choice. The model is fitted
once on development reforecast issues with target days 1998-2016, exactly as in
experiment 04.

Ensemble summary: reforecasts carry 11 members (control + perturbed 1-10), so
the operational ensemble is summarized on the same member set; a second model
row feeds the summary of all 51 operational members as a sensitivity check.

References, fitted on the same development data: day-of-year climatology and
the GloFAS rating curve. Target days are limited to 2023-08-02..2025-09-03,
where observed stage exists.

The second part checks the model ordinally against the 2026 IPCET semáforo:
for every 2026 date with a Usumacinta state, the lead-1 forecast issued the day
before (and the lead-3 forecast issued three days before) is compared with the
published state. With 162 normal, 4 bajo and 2 precaución days and no
alerta/emergencia day, no threshold is fitted and the rank AUC of precaución
against normal days is only indicative.

    uv run python experiments/05_operational.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from riverforecast import data, features
from riverforecast import evaluation as ev
from riverforecast.baselines import Climatology, Rating
from riverforecast.model import ConformalQuantileModel

CODE = "BDCTB"
OUT = Path("docs/results")
REPORTED = [
    "mae",
    "rmse",
    "pinball_q50",
    "coverage_80",
    "width_80",
    "high_water_n",
    "high_water_mae",
    "detection",
    "false_alarm_ratio",
]
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
SEMAFORO_ROW = 13  # Usumacinta in the IPCET table


def operational_rows(
    summary: pd.DataFrame,
    day0: pd.Series,
    lead: int,
    stage: pd.Series,
    model: ConformalQuantileModel,
    climatology: Climatology,
    rating: Rating,
    name: str,
    high_water: float,
) -> tuple[list[dict], list[dict]]:
    """Score rows for one lead: reference rows on the 11-member issues."""
    table = features.build(summary, day0, lead)
    table = table[table["valid"].isin(stage.index)]
    observed = stage.loc[table["valid"]]
    forecasts = {
        "climatology": climatology.predict(pd.DatetimeIndex(table["valid"])),
        "GloFAS rating": rating.predict(np.exp(table["fc_q50"].to_numpy())),
    }
    if name == "model":
        forecasts["model"] = model.predict(table[features.COLUMNS])
    else:
        forecasts[f"model ({name})"] = model.predict(table[features.COLUMNS])
    rows, yearly = [], []
    for forecast, (q10, q50, q90) in forecasts.items():
        scores = ev.summarize(observed, q10, q50, q90, high_water)
        rows.append(
            {
                "forecast": forecast,
                "lead_days": lead,
                "period": f"{table['valid'].min():%Y-%m-%d}..{table['valid'].max():%Y-%m-%d}",
                **scores,
            }
        )
        errors = pd.Series(np.abs(q50 - observed.to_numpy()), index=table["valid"])
        for year, value in errors.groupby(errors.index.year).mean().items():
            yearly.append(
                {"forecast": forecast, "lead_days": lead, "year": int(year), "mae": float(value)}
            )
    return rows, yearly


def fit_development_model(
    lead: int,
    reforecast_summary: pd.DataFrame,
    reanalysis: pd.Series,
    stage: pd.Series,
    development: pd.DatetimeIndex,
) -> tuple[ConformalQuantileModel, Rating]:
    """The frozen model and the rating, fitted once on development targets."""
    train = features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")
    train = train[train["valid"].isin(development)]
    target = stage.loc[train["valid"]]
    model = ConformalQuantileModel.fit(
        train[features.COLUMNS], target, pd.DatetimeIndex(train["valid"])
    )
    rating = Rating.fit(np.exp(train["fc_q50"]), target)
    return model, rating


def semaforo_predicted(
    summary: pd.DataFrame, day0: pd.Series, lead: int, model: ConformalQuantileModel
) -> pd.Series:
    """Model q50 by target day for every issue of one operational lead."""
    table = features.build(summary, day0, lead)
    q50 = model.predict(table[features.COLUMNS])[1]
    return pd.Series(q50, index=pd.DatetimeIndex(table["valid"]))


def semaforo_check(
    models: dict[int, ConformalQuantileModel], summary: pd.DataFrame, day0: pd.Series
) -> dict:
    """Ordinal comparison of predicted q50 with the 2026 Usumacinta semáforo."""
    states = pd.read_csv(data.RAW / "ipcet" / "states.csv", parse_dates=["date"])
    states = states[states["row"] == SEMAFORO_ROW]
    result: dict = {}
    for lead in (1, 3):
        predicted = semaforo_predicted(summary, day0, lead, models[lead])
        part = pd.DataFrame({"date": states["date"].to_numpy(), "state": states["hoy"].to_numpy()})
        part["q50"] = part["date"].map(predicted)
        part = part.dropna()
        per_state = (
            part.groupby("state")["q50"]
            .agg(["count", "mean", "median"])
            .round(3)
            .reset_index()
            .to_dict("records")
        )
        paired = part[part["state"].isin(["precaucion", "normal"])]
        result[f"lead_{lead}"] = {
            "n_days": int(len(part)),
            "per_state": per_state,
            "auc_precaucion_vs_normal": rank_auc(paired["q50"], paired["state"]),
            "n_precaucion": int((paired["state"] == "precaucion").sum()),
            "n_normal": int((paired["state"] == "normal").sum()),
        }
    return result


def rank_auc(values: pd.Series, groups: pd.Series) -> float:
    """Probability that a precaución day has a higher predicted q50 than a normal day."""
    precaucion = groups == "precaucion"
    n1, n0 = int(precaucion.sum()), int((groups == "normal").sum())
    if not n1 or not n0:
        return float("nan")
    ranks = values.rank(method="average")
    return float((ranks[precaucion].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def main() -> None:
    stage = data.stage(CODE)
    development, _ = ev.split_days(stage.index)
    high_water = float(stage.loc[development].quantile(0.9))
    summary_11 = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    summary_51 = data.glofas_operational_summary(CODE)
    day0 = data.glofas_day0(CODE)
    reanalysis = data.glofas_reanalysis(CODE)
    reforecast_summary = data.glofas_reforecast_summary(CODE)
    climatology = Climatology.fit(stage.loc[development])

    rows, yearly, models = [], [], {}
    for lead in (1, 3, 7):
        model, rating = fit_development_model(
            lead, reforecast_summary, reanalysis, stage, development
        )
        models[lead] = model
        for summary, label in ((summary_11, "model"), (summary_51, "51 members")):
            new_rows, new_yearly = operational_rows(
                summary, day0, lead, stage, model, climatology, rating, label, high_water
            )
            rows += new_rows
            yearly += new_yearly

    semaforo = semaforo_check(models, summary_11, day0)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "05-operational.json").write_text(
        json.dumps(
            {
                "high_water_m": high_water,
                "rows": rows,
                "yearly_mae": yearly,
                "semaforo_2026": semaforo,
            },
            indent=2,
            default=float,
        )
        + "\n"
    )
    write_report(rows, yearly, semaforo, high_water)


def write_report(rows: list[dict], yearly: list[dict], semaforo: dict, high_water: float) -> None:
    header = ["forecast", "lead_days", "period", "n", *REPORTED]
    lines = [
        "# Experiment 05: the frozen model on operational forecasts",
        "",
        "Generated by `experiments/05_operational.py`. The model is unchanged",
        "(fitted once on development reforecast issues with target days 1998-2016,",
        "as in experiment 04) and scored on the GloFAS operational forecasts issued",
        "2023-08-01..2026-10-13. Past discharge comes from the archived day-0",
        "forecasts. The ensemble summary uses the 11 reforecast members; the",
        "sensitivity row uses all 51 operational members.",
        f"High water: stage >= {high_water:.2f} m.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows:
        cells = [row["forecast"], str(row["lead_days"]), row["period"], str(row["n"])]
        cells += [f"{row[k]:.3f}" if isinstance(row[k], float) else str(row[k]) for k in REPORTED]
        lines.append("| " + " | ".join(cells) + " |")
    by_year = pd.DataFrame(yearly).pivot_table(
        index="year", columns=["lead_days", "forecast"], values="mae"
    )
    lines += ["", "## MAE by year (m)", "", by_year.round(3).to_markdown()]
    lines += ["", "## Semáforo 2026 (Usumacinta)", ""]
    for lead in (1, 3):
        part = semaforo[f"lead_{lead}"]
        lines += [
            f"### Lead {lead} (issued {lead} day{'s' if lead > 1 else ''} before the state)",
            "",
            f"{part['n_days']} of the 168 semáforo dates have a forecast for that day.",
            "",
            "| state | n | mean q50 (m) | median q50 (m) |",
            "| --- | --- | --- | --- |",
        ]
        for row in part["per_state"]:
            lines.append(
                f"| {row['state']} | {row['count']} | {row['mean']:.3f} | {row['median']:.3f} |"
            )
        auc = part["auc_precaucion_vs_normal"]
        lines += [
            "",
            f"Rank AUC of predicted q50, precaución vs normal: {auc:.3f} "
            f"(precaución n={part['n_precaucion']}, normal n={part['n_normal']}).",
            "Far too few precaución days for any conclusion; no threshold was fitted.",
            "",
        ]
    (OUT / "05-operational.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
