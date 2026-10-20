"""T19: reproduce the frozen L45 comparison offline; never makes HTTP requests.

Run: timeout 900 uv run python experiments/14_live_source.py
Inputs: .cache/t19/om/{ensemble,control}.json and the parent-supplied two-issue
CEMS CSV under .cache/t19/raw/. Alignment is fixed to the 2026-10-19 00 UTC
run by the control/member trajectories, not by the L45 acceptance statistic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from riverforecast import glofas

ROOT = Path(__file__).resolve().parents[1]
MATCHED_ISSUE = "2026-10-19"


def fingerprint(path):
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def score_values(om, cems):
    """Apply L45 inclusively, using CEMS as the relative-difference denominator."""
    om, cems = np.asarray(om, dtype=float), np.asarray(cems, dtype=float)
    if om.shape != cems.shape or not om.size or not np.isfinite([om, cems]).all():
        raise ValueError("comparison requires equal nonempty finite arrays")
    absolute = np.abs(om - cems)
    accepted = absolute <= np.maximum(0.5, 0.005 * np.abs(cems))
    relative = np.divide(absolute, np.abs(cems), out=np.zeros_like(absolute), where=cems != 0)
    undefined_relative = bool(((cems == 0) & (absolute != 0)).any())
    return {
        "within_tolerance": int(accepted.sum()),
        "total": int(accepted.size),
        "share_within_tolerance": float(accepted.mean()),
        "max_abs_difference_m3s": float(absolute.max()),
        "max_relative_difference": None if undefined_relative else float(relative.max()),
        "passes": bool(100 * accepted.sum() >= 99 * accepted.size),
    }


def compare():
    folder = ROOT / ".cache/t19/om"
    ensemble_path, control_path = folder / "ensemble.json", folder / "control.json"
    cems_path = ROOT / ".cache/t19/raw/glofas/BDCTB/operational/2026-10-18.csv"
    answer = json.loads(ensemble_path.read_text())
    control = json.loads(control_path.read_text())["daily"]
    om = glofas.fetch_ensemble_forecast(
        17.425, -91.525, forecast_days=30, past_days=7, get_json=lambda _: answer
    )
    cems = pd.read_csv(cems_path)
    cems["member"] = cems.member.map(lambda m: 0 if m == "control" else int(m.split("_")[1]))
    cems["date"] = pd.to_datetime(cems.valid)
    if set(cems.issue) != {"2026-10-18", "2026-10-19"} or len(cems) != 714:
        raise ValueError("expected exactly two CEMS issues, 714 rows")
    expected = {(lead, member) for lead in range(1, 8) for member in range(51)}
    for issue, rows in cems.groupby("issue"):
        if len(rows) != 357 or set(zip(rows.lead_days, rows.member, strict=True)) != expected:
            raise ValueError("CEMS requires all 51 members at every lead 1-7 without duplicates")
        if not (rows.date == pd.Timestamp(issue) + pd.to_timedelta(rows.lead_days, unit="D")).all():
            raise ValueError("CEMS valid must equal issue + lead")
    paired = cems.merge(
        om, on=["date", "member"], validate="many_to_one", suffixes=("_cems", "_om")
    )
    if (
        len(paired) != 714
        or not np.isfinite(paired[["discharge_m3s_cems", "discharge_m3s_om"]]).all().all()
    ):
        raise ValueError("both issues must have complete finite Open-Meteo overlap")
    # Alignment evidence is recorded separately from the L45 score. October 18's
    # lead 1 is a historical daily value; its subsequent trajectory is not served.
    signatures = []
    for row in paired[paired.member == 0].sort_values(["issue", "lead_days"]).itertuples():
        signatures.append(
            {
                "issue": row.issue,
                "lead": row.lead_days,
                "valid": row.valid,
                "cems_control_m3s": row.discharge_m3s_cems,
                "om_control_m3s": row.discharge_m3s_om,
            }
        )
    selected = paired[paired.issue == MATCHED_ISSUE].sort_values(["lead_days", "member"])
    # Direct numeric labels are fixed, never permuted to improve the score.
    mapping_evidence = []
    for lead, rows in selected.groupby("lead_days"):
        values = rows.sort_values("member")
        a = values.discharge_m3s_om.to_numpy()
        b = values.discharge_m3s_cems.to_numpy()
        mapping_evidence.append(
            {
                "lead": int(lead),
                "identity_rmse_m3s": float(np.sqrt(np.mean((a - b) ** 2))),
                "demeaned_member_signature_correlation": float(
                    np.corrcoef(a - a.mean(), b - b.mean())[0, 1]
                ),
                "members": [
                    {
                        "member": int(r.member),
                        "om": r.discharge_m3s_om,
                        "cems": r.discharge_m3s_cems,
                    }
                    for r in values.itertuples()
                ],
            }
        )
    # The sole pre-registered current-run score. No threshold variants or search.
    score = score_values(selected.discharge_m3s_om, selected.discharge_m3s_cems)
    per_lead = [
        {"lead": int(lead), **score_values(rows.discharge_m3s_om, rows.discharge_m3s_cems)}
        for lead, rows in selected.groupby("lead_days")
    ]
    return {
        "specs": ["S5", "S8"],
        "status": "scored",
        "decision_rule": "L45: abs(OM-CEMS) <= max(0.5 m3/s, 0.005*abs(CEMS)) for >=99% "
        "of 51 x 7 member-by-lead values on every compared issue day",
        "verdict": "adopted" if score["passes"] else "not adopted",
        "live_source": "Open-Meteo" if score["passes"] else "CEMS",
        "open_meteo": {
            "workflow_run": 39339164931,
            "fetched_at": (folder / "fetched_at.txt").read_text().strip(),
            "dates": [str(om.date.min().date()), str(om.date.max().date())],
            "daily_dates": int(om.date.nunique()),
            "members": int(om.member.nunique()),
            "control_response_equals_member0": control["river_discharge"]
            == answer["daily"]["river_discharge"],
            "past_days": "Seven archived daily values, not seven full historical "
            "issue trajectories",
        },
        "inputs": [fingerprint(p) for p in [ensemble_path, control_path, cems_path]],
        "alignment": {
            "matched_issue": MATCHED_ISSUE,
            "issue_time": "00:00 UTC",
            "date_mapping": "Open-Meteo date = CEMS valid = issue + lead",
            "compared_valid_dates": ["2026-10-20", "2026-10-26"],
            "cems_cell": [17.425, 268.525],
            "open_meteo_cell": [17.425, -91.525],
            "cell_evidence": "cems.py documents the longitude-label offset",
            "reason": "Control trajectory for Oct 19 follows OM on Oct 20-26, including "
            "the Oct 21 peak. Oct 18 only matches its lead-1 archived value on Oct 19; "
            "its later control and perturbed trajectories are different. OM past_days "
            "does not retain Oct 18 leads 2-7. Therefore only Oct 19 is honestly matched. "
            "The API has no issue timestamp: identification uses trajectory fingerprints, "
            "not the adoption share. No issue or member permutation maximizes L45.",
            "control_signatures_both_issues": signatures,
        },
        "member_mapping": {
            "mapping": "river_discharge -> control (0); river_discharge_memberNN -> "
            "CEMS number NN / perturbed_NN",
            "evidence": mapping_evidence,
        },
        "scores": [{"issue": MATCHED_ISSUE, **score, "per_lead": per_lead}],
        "relative_difference_definition": "abs(OM-CEMS)/abs(CEMS); "
        "all real CEMS values are positive",
        "network_requests_this_resume": 0,
        "cems_request_history": [
            json.loads(p.read_text())
            for p in sorted((ROOT / ".cache/t19").glob("cems-*-timing.json"))
        ],
        "limitations": "One matched issue day only; this tests source equivalence, "
        "not stage forecast skill. "
        "Raw inputs live in ignored .cache/t19 and must be retained for reproduction.",
    }


def main():
    report = compare()
    out = ROOT / "docs/results/14-live-source"
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    score = report["scores"][0]
    signatures = "\n".join(
        f"| {r['issue']} | {r['lead']} | {r['valid']} | "
        f"{r['cems_control_m3s']} | {r['om_control_m3s']} |"
        for r in report["alignment"]["control_signatures_both_issues"]
    )
    leads = "\n".join(
        f"| {r['lead']} | {r['within_tolerance']}/{r['total']} | {r['share_within_tolerance']} | "
        f"{r['max_abs_difference_m3s']} | {r['max_relative_difference']} |"
        for r in score["per_lead"]
    )
    text = (
        "# T19 — live GloFAS source (S5, S8)\n\n"
        f"**L45 verdict: {report['verdict']}. Live source: {report['live_source']}.**\n\n"
        "## Reproduction and provenance\n\n"
        "Run `timeout 900 uv run python experiments/14_live_source.py`. All inputs are local; "
        "the script makes no HTTP requests. SHA-256 hashes are in the JSON. Open-Meteo: "
        "GitHub run 39339164931, fetched 2026-10-19T23:03:54Z; 37 dates "
        "2026-10-12–2026-11-17, 51 members. The separate control response equals member 0. "
        "Retain `.cache/t19/om/` and `.cache/t19/raw/` (ignored files).\n\n"
        "## Alignment established before scoring\n\n" + report["alignment"]["reason"] + "\n\n"
        "Matched run: **2026-10-19 00 UTC**, leads 1–7 = valid dates October 20–26. "
        "CEMS valid = issue + lead; Open-Meteo dates equal CEMS valid dates, without a shift. "
        "CEMS 17.425/268.525 is OM 17.425/-91.525 per the documented cell-label offset.\n\n"
        "| CEMS issue | lead | valid/OM date | CEMS control m³/s | OM control m³/s |\n"
        "|---|---:|---|---:|---:|\n" + signatures + "\n\n"
        "`river_discharge` maps to CEMS control (member 0); `river_discharge_memberNN` "
        "maps to CEMS `number` NN (01–50). Numeric mapping is fixed, not optimized. "
        "The JSON records all member-pair fingerprints and demeaned member correlations "
        "at every lead. `past_days=7` returns seven archived daily values, not seven "
        "complete issue trajectories; October 18 cannot be scored as a second full run.\n\n"
        "## Single pre-registered L45 result\n\n"
        "Tolerance unchanged: abs(OM − CEMS) ≤ max(0.5 m³/s, 0.005 × abs(CEMS)); "
        "adopt if at least 99% of all member×lead values pass on every matched issue. "
        "Relative differences use CEMS as denominator. No tuning or alternate threshold.\n\n"
        f"Issue {MATCHED_ISSUE}: **{score['within_tolerance']}/{score['total']}** "
        "within tolerance; "
        f"share **{score['share_within_tolerance']}**. Maximum absolute difference: "
        f"**{score['max_abs_difference_m3s']} m³/s**. Maximum relative difference: "
        f"**{score['max_relative_difference']}** (fraction).\n\n"
        "| lead | within/total | share | max abs m³/s | max relative (fraction) |\n"
        "|---:|---:|---:|---:|---:|\n" + leads + "\n\n"
        "## Requests and limits\n\n"
        "Zero network requests on this resume. Earlier worker CEMS request: rejected "
        "after 13.761870116002683 s. Parent's successful request: two issues, "
        "714 records, 393.98245247999876 s, seven leads, 51 members. Operational "
        "archive untouched. Total CEMS attempts across these stages: 2. "
        "This is one-day source equivalence evidence, not a new stage-model evaluation.\n"
    )
    out.with_suffix(".md").write_text(text)
    print(json.dumps({"verdict": report["verdict"], "score": score}, indent=2))


if __name__ == "__main__":
    main()
