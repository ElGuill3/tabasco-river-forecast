"""Evaluation protocol: periods, year folds and forecast metrics.

Periods
    Development 1998-2016: fitting and every modelling choice, with whole-year
    folds. Test 2017-2025: used once, at the end, for the reported results.

Metrics (stage in metres)
    MAE and RMSE of the median; pinball loss of q10, q50 and q90; coverage and
    width of the q10-q90 interval (nominal 80%); MAE on high-water days; and,
    for exceedance of a high-water threshold, probability of detection and
    false alarm ratio.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd

DEVELOPMENT = ("1998-01-01", "2016-12-31")
TEST = ("2017-01-01", "2025-12-31")


def mae(observed, predicted) -> float:
    return float(np.mean(np.abs(np.asarray(predicted) - np.asarray(observed))))


def rmse(observed, predicted) -> float:
    return float(np.sqrt(np.mean((np.asarray(predicted) - np.asarray(observed)) ** 2)))


def pinball(observed, predicted, quantile: float) -> float:
    """Mean quantile (pinball) loss; lower is better."""
    diff = np.asarray(observed) - np.asarray(predicted)
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def coverage(observed, low, high) -> float:
    observed = np.asarray(observed)
    return float(np.mean((observed >= np.asarray(low)) & (observed <= np.asarray(high))))


def width(low, high) -> float:
    return float(np.mean(np.asarray(high) - np.asarray(low)))


@dataclass(frozen=True)
class Exceedance:
    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int

    @property
    def probability_of_detection(self) -> float:
        events = self.hits + self.misses
        return self.hits / events if events else float("nan")

    @property
    def false_alarm_ratio(self) -> float:
        warnings = self.hits + self.false_alarms
        return self.false_alarms / warnings if warnings else float("nan")

    @property
    def critical_success_index(self) -> float:
        total = self.hits + self.misses + self.false_alarms
        return self.hits / total if total else float("nan")


def exceedance_scores(observed, predicted, threshold: float) -> Exceedance:
    """Contingency table for 'stage reaches the threshold' (>=)."""
    event = np.asarray(observed) >= threshold
    warned = np.asarray(predicted) >= threshold
    return Exceedance(
        hits=int(np.sum(event & warned)),
        misses=int(np.sum(event & ~warned)),
        false_alarms=int(np.sum(~event & warned)),
        correct_negatives=int(np.sum(~event & ~warned)),
    )


def split_days(days: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Development and test days of ``days``."""
    development = days[(days >= DEVELOPMENT[0]) & (days <= DEVELOPMENT[1])]
    test = days[(days >= TEST[0]) & (days <= TEST[1])]
    return development, test


def year_folds(
    days: pd.DatetimeIndex, years_per_fold: int = 4, buffer_days: int = 14
) -> Iterator[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]:
    """Folds that hold out consecutive whole years.

    Training days closer than ``buffer_days`` to the held-out block are dropped,
    so lagged inputs and the forecast horizon cannot reach across the boundary.
    """
    years = sorted(set(days.year))
    for start in range(0, len(years), years_per_fold):
        block = years[start : start + years_per_fold]
        held = days[days.year.isin(block)]
        margin = pd.Timedelta(days=buffer_days)
        near = (days >= held.min() - margin) & (days <= held.max() + margin)
        yield days[~near], held


def summarize(observed, q10, q50, q90, high_water: float) -> dict[str, float]:
    """Every metric of one quantile forecast over aligned arrays."""
    observed = np.asarray(observed)
    high = observed >= high_water
    scores = exceedance_scores(observed, q50, high_water)
    return {
        "n": int(observed.size),
        "mae": mae(observed, q50),
        "rmse": rmse(observed, q50),
        "pinball_q10": pinball(observed, q10, 0.1),
        "pinball_q50": pinball(observed, q50, 0.5),
        "pinball_q90": pinball(observed, q90, 0.9),
        "coverage_80": coverage(observed, q10, q90),
        "width_80": width(q10, q90),
        "high_water_n": int(high.sum()),
        "high_water_mae": mae(observed[high], np.asarray(q50)[high])
        if high.any()
        else float("nan"),
        "detection": scores.probability_of_detection,
        "false_alarm_ratio": scores.false_alarm_ratio,
    }
