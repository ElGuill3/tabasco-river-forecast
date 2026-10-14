"""Model inputs for one forecast lead, built only from what is known at issue time.

Each row is one forecast issue ``t`` for lead ``h``; its target is the stage on
``t + h``. Inputs:

- ``fc_*``: the GloFAS ensemble forecast for ``t + h`` issued on ``t`` (log
  discharge of the median, mean and control run, and the log 10-90% spread).
- ``past_*``: GloFAS simulated discharge up to ``t`` (log values on ``t``,
  ``t - 3`` and ``t - 7``, and the three-day trend). Operationally these come
  from the day-0 value of each daily forecast; in training from the reanalysis,
  which matches it closely.
- ``fc_vs_now``: how much the forecast expects the discharge to change.
- ``season_*``: day of year of the target day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

COLUMNS = [
    "fc_q50",
    "fc_mean",
    "fc_control",
    "fc_spread",
    "past_q0",
    "past_q3",
    "past_q7",
    "past_trend3",
    "fc_vs_now",
    "season_sin",
    "season_cos",
]


def build(summary: pd.DataFrame, reanalysis: pd.Series, lead: int) -> pd.DataFrame:
    """Feature table indexed by issue day, with the target day in ``valid``.

    ``summary`` is the reforecast ensemble summary (issue, lead_days, valid,
    control, mean, q10, q50, q90); ``reanalysis`` is daily simulated discharge.
    Issues missing any past discharge value are dropped.
    """
    rows = summary[summary["lead_days"] == lead].set_index("issue").sort_index()
    issues = rows.index
    log_q = np.log(reanalysis.sort_index())

    def past(days: int) -> np.ndarray:
        return log_q.reindex(issues - pd.Timedelta(days=days)).to_numpy()

    valid = pd.DatetimeIndex(rows["valid"])
    table = pd.DataFrame(
        {
            "valid": valid,
            "fc_q50": np.log(rows["q50"].to_numpy()),
            "fc_mean": np.log(rows["mean"].to_numpy()),
            "fc_control": np.log(rows["control"].to_numpy()),
            "fc_spread": np.log(rows["q90"].to_numpy()) - np.log(rows["q10"].to_numpy()),
            "past_q0": past(0),
            "past_q3": past(3),
            "past_q7": past(7),
        },
        index=issues,
    )
    table["past_trend3"] = table["past_q0"] - table["past_q3"]
    table["fc_vs_now"] = table["fc_q50"] - table["past_q0"]
    angle = 2 * np.pi * valid.dayofyear.to_numpy() / 365.25
    table["season_sin"] = np.sin(angle)
    table["season_cos"] = np.cos(angle)
    table.index.name = "issue"
    return table.dropna()[["valid", *COLUMNS]]
