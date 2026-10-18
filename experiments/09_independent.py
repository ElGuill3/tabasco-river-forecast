"""Experiment 09: independent verification, pre-registered in the task log (L26).

L26 was written before any comparison: "Model under test: M1 (L24/L25, fitted
on 1998-2022, deployment model) with the 11-member operational summary and
day-0 past discharge, warning rule R2 of L25 (and R0 for reference). Period:
forecasts valid 2025-09-04..2026-10-13, never scored against stage. ... No
threshold or model is fitted on any of this." This script applies that design
exactly; nothing is fitted, tuned or changed on this evidence.

Evidence, in order of strength (L26):
(a) fresh CONAGUA SIH stage after 2025-09-03: not available yet - reported as
    pending, skipped;
(b) a catalog of dated Usumacinta stage values quoted by CONAGUA, Protección
    Civil, IPCET, the state government or the press, collected without looking
    at any forecast (``data/verification/usumacinta_press_2025-2026.csv``):
    scored as |q50 - quoted| and whether the quote falls inside q10-q90 for
    the lead-1 (issued D-1) and lead-3 (issued D-3) forecasts valid that date;
(c) dated reports of Usumacinta high water, overflow or alerts: hit if R2 (and
    R0, for reference) warned at lead 1 within plus or minus one day of the
    publication date; quiet stretches without reports are not counted as
    negatives; downstream (Jonuta) events are reported separately;
(d) IPCET semáforo 2026: predicted q50 by state and rank AUC precaución vs
    normal at leads 1 and 3 (ordinal only, n is tiny), as in experiment 05.

M1 and its warning chain are fitted exactly as in experiment 08 (the same
conformal quantile model, features and hyperparameters, refitted on every
reforecast issue whose target day is 1998-01-01..2022-12-31 and has observed
stage; reanalysis past discharge in training, day-0 in operation; the warning
chain - isotonic calibrator, t* = bias-one threshold, R0/R1/R2 - re-derived on
1998-2022 out-of-fold predictions with 4-year folds and a 14-day buffer).
High water stays 17.83 m. Only leads 1 and 3 are fitted: the L26 design uses
no other lead. If two catalog rows share a date and a value, the date is
counted once.

    uv run python experiments/09_independent.py
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
PRESS = Path("data/verification/usumacinta_press_2025-2026.csv")
HIGH_WATER = 17.83  # development P90 (L11); kept fixed, never recomputed
M1_END = "2022-12-31"
MEMBERS_11 = ["control", *[f"perturbed_{i:02d}" for i in range(1, 11)]]
SEMAFORO_ROW = 13  # Usumacinta in the IPCET table (as in experiment 05)
LEADS = (1, 3)
PERIOD = ("2025-09-04", "2026-10-14")
DAILY_WINDOWS = [("2025-09-15", "2025-10-20"), ("2026-09-25", "2026-10-14")]


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


def fit_m1_chain(
    lead: int,
    reforecast_summary: pd.DataFrame,
    reanalysis: pd.Series,
    stage: pd.Series,
) -> tuple[ConformalQuantileModel, IsotonicRegression, float, dict]:
    """M1 and its warning chain, fitted exactly as in experiment 08."""
    train = features.build(reforecast_summary, reanalysis, lead).drop_duplicates("valid")
    train = train[train["valid"] <= M1_END]
    train = train[train["valid"].isin(stage.index)]
    model = ConformalQuantileModel.fit(
        train[features.COLUMNS], stage.loc[train["valid"]], pd.DatetimeIndex(train["valid"])
    )
    oof = out_of_fold(train, stage)
    event = (stage.loc[oof.index] >= HIGH_WATER).to_numpy()
    calibrator, calibrated = calibrate(oof, event)
    t_star = warning.bias_one_threshold(calibrated, event)
    development = {
        "n": int(len(oof)),
        "events": int(event.sum()),
        "base_rate": float(np.mean(event)),
        "t_star": t_star,
        "bss": warning.brier_skill_score(calibrated, event, float(np.mean(event))),
        "roc_auc": warning.roc_auc(calibrated, event),
    }
    return model, calibrator, t_star, development


def lead_forecasts(
    model: ConformalQuantileModel,
    calibrator: IsotonicRegression,
    t_star: float,
    lead: int,
    operational_summary: pd.DataFrame,
    day0: pd.Series,
) -> pd.DataFrame:
    """One row per operational issue of one lead: quantiles, p, R0/R1/R2."""
    table = features.build(operational_summary, day0, lead)
    q10, q50, q90 = model.predict(table[features.COLUMNS])
    p = calibrator.predict(warning.exceedance_probability(q10, q50, q90, HIGH_WATER))
    r1 = pd.Series(p >= t_star, index=table.index)
    return pd.DataFrame(
        {
            "valid": pd.DatetimeIndex(table["valid"]),
            "q10": q10,
            "q50": q50,
            "q90": q90,
            "p": p,
            "r0": np.asarray(q50) >= HIGH_WATER,
            "r2": warning.persistent(r1).to_numpy(),
        },
        index=table.index,
    ).rename_axis("issue")


def level_scores(rows: pd.DataFrame, forecasts: pd.DataFrame) -> tuple[list[dict], dict]:
    """Per-row comparison of quoted levels with the lead forecasts, plus a summary."""
    per_lead: dict = {}
    for lead in LEADS:
        by_valid = forecasts[lead].set_index("valid")
        issue_of = {row["valid"]: issue for issue, row in forecasts[lead].iterrows()}
        frame = by_valid
        records = []
        for _, row in rows.iterrows():
            date = pd.Timestamp(row["date"])
            if date not in frame.index:
                records.append(
                    {
                        "date": str(row["date"]),
                        "quoted_m": float(row["level_m"]),
                        "lead_days": lead,
                        "forecast_available": False,
                    }
                )
                continue
            f = frame.loc[date]
            quoted = float(row["level_m"])
            records.append(
                {
                    "date": str(row["date"]),
                    "quoted_m": quoted,
                    "lead_days": lead,
                    "forecast_available": True,
                    "issue": f"{issue_of[date]:%Y-%m-%d}",
                    "q10": float(f["q10"]),
                    "q50": float(f["q50"]),
                    "q90": float(f["q90"]),
                    "error_q50_minus_quoted": float(f["q50"] - quoted),
                    "inside_q10_q90": bool(f["q10"] <= quoted <= f["q90"]),
                }
            )
        scored = [r for r in records if r["forecast_available"]]
        per_lead[lead] = {
            "rows": records,
            "n": len(scored),
            "n_no_forecast": len(records) - len(scored),
            "mae": float(np.mean([abs(r["error_q50_minus_quoted"]) for r in scored])),
            "mean_error": float(np.mean([r["error_q50_minus_quoted"] for r in scored])),
            "coverage_q10_q90": float(np.mean([r["inside_q10_q90"] for r in scored])),
        }
    return per_lead


def event_checks(rows: pd.DataFrame, lead1: pd.DataFrame) -> list[dict]:
    """For each event row: did R2 (and R0) warn at lead 1 within +-1 day."""
    records = []
    for _, row in rows.iterrows():
        date = pd.Timestamp(row["date"])
        window = lead1[
            (lead1["valid"] >= date - pd.Timedelta(days=1))
            & (lead1["valid"] <= date + pd.Timedelta(days=1))
        ]
        hit_r2 = bool(window["r2"].any()) if len(window) else False
        hit_r0 = bool(window["r0"].any()) if len(window) else False
        records.append(
            {
                "date": str(row["date"]),
                "place": row["place"],
                "event": row["event"],
                "n_forecasts_in_window": int(len(window)),
                "hit_r2": hit_r2,
                "hit_r0": hit_r0,
                "max_q50": float(window["q50"].max()) if len(window) else None,
                "max_p": float(window["p"].max()) if len(window) else None,
            }
        )
    return records


def rank_auc(values: pd.Series, groups: pd.Series) -> float:
    """Probability that a precaución day has a higher predicted q50 than a normal day."""
    precaucion = groups == "precaucion"
    n1, n0 = int(precaucion.sum()), int((groups == "normal").sum())
    if not n1 or not n0:
        return float("nan")
    ranks = values.rank(method="average")
    return float((ranks[precaucion].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def semaforo_check(
    forecasts: dict[int, pd.DataFrame],
) -> dict:
    """Ordinal comparison of predicted q50 with the 2026 Usumacinta semáforo."""
    states = pd.read_csv(data.RAW / "ipcet" / "states.csv", parse_dates=["date"])
    states = states[states["row"] == SEMAFORO_ROW]
    result: dict = {}
    for lead in LEADS:
        predicted = forecasts[lead].set_index("valid")["q50"]
        part = pd.DataFrame({"date": states["date"].to_numpy(), "state": states["hoy"].to_numpy()})
        part["q50"] = part["date"].map(predicted)
        part = part.dropna()
        per_state = (
            part.groupby("state")["q50"].agg(["count", "mean", "median"]).round(3).reset_index()
        ).to_dict("records")
        paired = part[part["state"].isin(["precaucion", "normal"])]
        result[f"lead_{lead}"] = {
            "n_days": int(len(part)),
            "per_state": per_state,
            "auc_precaucion_vs_normal": rank_auc(paired["q50"], paired["state"]),
            "n_precaucion": int((paired["state"] == "precaucion").sum()),
            "n_normal": int((paired["state"] == "normal").sum()),
        }
    return result


def daily_tables(forecasts: dict[int, pd.DataFrame], level_rows: pd.DataFrame) -> list[dict]:
    """Compact lead-1 daily tables (q10/q50/q90, R2) next to the quoted levels."""
    quoted = (
        level_rows.assign(date=lambda d: pd.to_datetime(d["date"]))
        .set_index("date")["level_m"]
        .to_dict()
    )
    lead1 = forecasts[1]
    tables = []
    for start, end in DAILY_WINDOWS:
        window = lead1[
            (lead1["valid"] >= pd.Timestamp(start)) & (lead1["valid"] <= pd.Timestamp(end))
        ]
        tables.append(
            {
                "window": f"{start}..{end}",
                "rows": [
                    {
                        "valid": f"{row['valid']:%Y-%m-%d}",
                        "issue": f"{issue:%Y-%m-%d}",
                        "q10": float(row["q10"]),
                        "q50": float(row["q50"]),
                        "q90": float(row["q90"]),
                        "r2": bool(row["r2"]),
                        "quoted_m": quoted.get(row["valid"]),
                    }
                    for issue, row in window.iterrows()
                ],
            }
        )
    return tables


def main() -> None:
    stage = data.stage(CODE)
    reanalysis = data.glofas_reanalysis(CODE)
    reforecast_summary = data.glofas_reforecast_summary(CODE)
    operational_summary = data.glofas_operational_summary(CODE, members=MEMBERS_11)
    day0 = data.glofas_day0(CODE)

    forecasts: dict[int, pd.DataFrame] = {}
    development: dict = {}
    for lead in LEADS:
        model, calibrator, t_star, dev = fit_m1_chain(lead, reforecast_summary, reanalysis, stage)
        development[f"lead_{lead}"] = dev
        forecasts[lead] = lead_forecasts(model, calibrator, t_star, lead, operational_summary, day0)

    press = pd.read_csv(PRESS)
    excluded = press[press["use"].isin(["excluded_datum", "excluded_place"])][
        ["date", "place", "level_m", "use", "source"]
    ].to_dict("records")

    # (b) quoted levels; dates sharing a date and value are counted once.
    level_rows = press[press["use"] == "level"].drop_duplicates(["date", "level_m"])
    unnamed_rows = press[press["use"] == "level_unnamed_station"].drop_duplicates(
        ["date", "level_m"]
    )
    levels = level_scores(level_rows, forecasts)
    unnamed = level_scores(unnamed_rows, forecasts)

    # (c) events; quiet stretches are listed only (L26: not counted as negatives).
    events = event_checks(press[press["use"] == "event"], forecasts[1])
    downstream = event_checks(press[press["use"] == "event_downstream"], forecasts[1])
    quiet = press[press["use"] == "quiet"][["date", "place", "event"]].to_dict("records")
    with_forecast = [e for e in events if e["n_forecasts_in_window"] > 0]
    event_summary = {
        "n_events": len(events),
        "n_events_with_forecast_in_window": len(with_forecast),
        "hits_r2": int(sum(e["hit_r2"] for e in with_forecast)),
        "hits_r0": int(sum(e["hit_r0"] for e in with_forecast)),
        "hit_rate_r2": float(np.mean([e["hit_r2"] for e in with_forecast])),
        "hit_rate_r0": float(np.mean([e["hit_r0"] for e in with_forecast])),
    }

    semaforo = semaforo_check(forecasts)
    daily = daily_tables(forecasts, level_rows)

    result = {
        "high_water_m": HIGH_WATER,
        "pre_registration": "L26",
        "period": f"{PERIOD[0]}..{PERIOD[1]} (forecasts valid)",
        "sih_after_2025_09_03": "pending: needs a fresh SIH import",
        "model": "M1 (L24/L25), 11-member operational summary, day-0 past discharge",
        "development_oof": development,
        "level_rows_note": (
            "rows sharing a date and a value are counted once; Boca del Cerro levels "
            "are on the 19.21 m NAMO datum, the same datum as the training stage"
        ),
        "levels": levels,
        "levels_unnamed_station": unnamed,
        "events": events,
        "events_downstream": downstream,
        "event_summary": event_summary,
        "quiet_stretches": quiet,
        "excluded_rows": excluded,
        "semaforo_2026": semaforo,
        "daily_lead1": daily,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "09-independent.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    write_report(result)


def fmt(value, digits: int = 2) -> str:
    """Markdown cell: n/a for None/NaN, fixed digits otherwise."""
    if value is None:
        return "n/a"
    if isinstance(value, float) and np.isnan(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_report(result: dict) -> None:
    lines = [
        "# Experiment 09: independent verification",
        "",
        "Generated by `experiments/09_independent.py`. Independent verification,",
        "pre-registered in L26; none of these dates was scored against stage",
        "before. The model under test is M1 (L24/L25, fitted on 1998-2022 targets)",
        "with the 11-member operational summary and day-0 past discharge, and the",
        "warning chain R0/R2 of L25 (t* re-derived on 1998-2022 out-of-fold",
        "predictions only). No threshold or model is fitted on this evidence.",
        f"(a) Fresh CONAGUA SIH stage after 2025-09-03: {result['sih_after_2025_09_03']}.",
        "",
        "## Development out-of-fold warning chain (M1, 1998-2022 targets)",
        "",
        "| lead | n | events | base rate | t* | BSS | ROC AUC |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lead in LEADS:
        dev = result["development_oof"][f"lead_{lead}"]
        lines.append(
            f"| {lead} | {dev['n']} | {dev['events']} | {dev['base_rate']:.3f} "
            f"| {dev['t_star']:.3f} | {dev['bss']:.3f} | {dev['roc_auc']:.3f} |"
        )
    lines += [
        "",
        "## (b) Press-quoted levels at Boca del Cerro (19.21 m NAMO datum)",
        "",
        "Rows sharing a date and a value are counted once. Error is q50 - quoted.",
        "",
        "| date | quoted | lead | issue | q10 | q50 | q90 | error | inside q10-q90 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for group, label in ((result["levels"], ""), (result["levels_unnamed_station"], " (*)")):
        for lead in LEADS:
            for row in group[lead]["rows"]:
                if not row["forecast_available"]:
                    lines.append(
                        f"| {row['date']}{label} | {fmt(row['quoted_m'])} | {lead} "
                        "| - | - | - | - | - | no forecast |"
                    )
                    continue
                lines.append(
                    f"| {row['date']}{label} | {fmt(row['quoted_m'])} | {lead} "
                    f"| {row['issue']} | {fmt(row['q10'])} | {fmt(row['q50'])} "
                    f"| {fmt(row['q90'])} | {fmt(row['error_q50_minus_quoted'])} "
                    f"| {row['inside_q10_q90']} |"
                )
    lines += [
        "",
        "(*) station not named by the source (governor's statement); scored",
        "separately and flagged, per L26.",
        "",
        "| set | lead | n | MAE | mean error (bias) | coverage q10-q90 | no forecast |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, group in (
        ("Boca del Cerro", result["levels"]),
        ("unnamed station", result["levels_unnamed_station"]),
    ):
        for lead in LEADS:
            part = group[lead]
            lines.append(
                f"| {label} | {lead} | {part['n']} | {fmt(part['mae'])} "
                f"| {fmt(part['mean_error'])} | {fmt(part['coverage_q10_q90'])} "
                f"| {part['n_no_forecast']} |"
            )
    lines += [
        "",
        "## (c) Dated high-water / alert reports (hit: R2 at lead 1 within +-1 day)",
        "",
        "| date | place | n forecasts in window | R2 hit | R0 hit | max q50 | max p |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["events"] + result["events_downstream"]:
        lines.append(
            f"| {row['date']} | {row['place']} | {row['n_forecasts_in_window']} "
            f"| {row['hit_r2']} | {row['hit_r0']} | {fmt(row['max_q50'])} "
            f"| {fmt(row['max_p'])} |"
        )
    summary = result["event_summary"]
    lines += [
        "",
        f"Hit rate over the {summary['n_events_with_forecast_in_window']} events with "
        f"a forecast in the window: R2 {summary['hits_r2']}/"
        f"{summary['n_events_with_forecast_in_window']} "
        f"({summary['hit_rate_r2']:.2f}), R0 {summary['hits_r0']}/"
        f"{summary['n_events_with_forecast_in_window']} "
        f"({summary['hit_rate_r0']:.2f}). Quiet stretches (listed only, not negatives "
        "per L26): "
        + "; ".join(f"{row['date']} {row['place']}" for row in result["quiet_stretches"])
        + ". The Jonuta report is downstream context, scored separately.",
        "",
        "## (d) IPCET semáforo 2026 (Usumacinta, ordinal only)",
        "",
    ]
    for lead in LEADS:
        part = result["semaforo_2026"][f"lead_{lead}"]
        lines += [
            f"### Lead {lead}",
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
            f"Rank AUC precaución vs normal: {auc:.3f} (precaución n="
            f"{part['n_precaucion']}, normal n={part['n_normal']}).",
            "",
        ]
    lines += [
        "## Excluded rows (listed, never scored)",
        "",
        "| date | place | level | reason | source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["excluded_rows"]:
        lines.append(
            f"| {row['date']} | {row['place']} | {fmt(float(row['level_m']))} "
            f"| {row['use']} | {row['source']} |"
        )
    lines += [
        "",
        "The excluded_datum row quotes the Boca del Cerro station on a second, older",
        "~12.56 m NAMO scale that circulates in some bulletins; it is not comparable",
        "with the 19.21 m datum of the training stage and was never scored.",
        "",
        "## Daily lead-1 tables (R2 = warning on two consecutive issues)",
        "",
    ]
    for table in result["daily_lead1"]:
        lines += [
            f"### {table['window']}",
            "",
            "| valid | issue | q10 | q50 | q90 | R2 | quoted |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in table["rows"]:
            lines.append(
                f"| {row['valid']} | {row['issue']} | {fmt(row['q10'])} "
                f"| {fmt(row['q50'])} | {fmt(row['q90'])} | {row['r2']} "
                f"| {fmt(row['quoted_m'])} |"
            )
        lines.append("")
    (OUT / "09-independent.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
