"""Reference forecasts every model has to beat.

Climatology
    The stage usually seen on that day of the year: 10th, 50th and 90th
    percentiles of the training stages within a window of days around it. It
    needs no input at all.

Rating
    GloFAS discharge converted to stage with a monotonic (isotonic) curve fitted
    on training pairs, plus the 10th and 90th percentiles of its residuals. It
    is what a user would get by reading GloFAS against the local gauge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

QUANTILES = (0.1, 0.5, 0.9)


@dataclass(frozen=True)
class Climatology:
    table: pd.DataFrame  # index: day of year 1-366; columns q10, q50, q90

    @classmethod
    def fit(cls, levels: pd.Series, window_days: int = 15) -> Climatology:
        levels = levels.dropna()
        doy = levels.index.dayofyear.to_numpy()
        values = levels.to_numpy()
        rows = {}
        for day in range(1, 367):
            distance = np.abs(doy - day)
            distance = np.minimum(distance, 366 - distance)
            near = values[distance <= window_days]
            rows[day] = np.quantile(near, QUANTILES)
        table = pd.DataFrame.from_dict(rows, orient="index", columns=["q10", "q50", "q90"])
        return cls(table)

    def predict(self, days: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rows = self.table.loc[days.dayofyear]
        return rows["q10"].to_numpy(), rows["q50"].to_numpy(), rows["q90"].to_numpy()


@dataclass(frozen=True)
class Rating:
    curve: IsotonicRegression
    low: float
    high: float

    @classmethod
    def fit(cls, discharge: pd.Series, stage: pd.Series) -> Rating:
        pairs = pd.concat([discharge, stage], axis=1, keys=["q", "h"]).dropna()
        curve = IsotonicRegression(out_of_bounds="clip").fit(pairs["q"], pairs["h"])
        residuals = pairs["h"].to_numpy() - curve.predict(pairs["q"])
        low, high = np.quantile(residuals, [QUANTILES[0], QUANTILES[2]])
        return cls(curve, float(low), float(high))

    def predict(self, discharge) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        median = self.curve.predict(np.asarray(discharge, dtype=float))
        return median + self.low, median, median + self.high
