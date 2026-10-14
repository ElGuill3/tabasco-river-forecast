"""Quantile forecast model: one gradient-boosting regressor per quantile.

Each regressor minimizes the pinball loss of its quantile. Independent
regressors can cross, so the three predictions of each row are sorted, which
keeps q10 <= q50 <= q90 without changing any well-ordered row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

QUANTILES = (0.1, 0.5, 0.9)
PARAMS = {
    "max_iter": 300,
    "learning_rate": 0.05,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 20,
    "l2_regularization": 1.0,
    "random_state": 0,
}


@dataclass(frozen=True)
class QuantileModel:
    columns: list[str]
    regressors: tuple[HistGradientBoostingRegressor, ...]

    @classmethod
    def fit(cls, x: pd.DataFrame, y: pd.Series) -> QuantileModel:
        regressors = tuple(
            HistGradientBoostingRegressor(loss="quantile", quantile=q, **PARAMS).fit(x, y)
            for q in QUANTILES
        )
        return cls(list(x.columns), regressors)

    def predict(self, x: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if list(x.columns) != self.columns:
            raise ValueError(f"expected columns {self.columns}, got {list(x.columns)}")
        stacked = np.sort(np.column_stack([r.predict(x) for r in self.regressors]), axis=1)
        return stacked[:, 0], stacked[:, 1], stacked[:, 2]


@dataclass(frozen=True)
class ConformalQuantileModel:
    """QuantileModel whose q10-q90 interval is widened by conformalized quantile regression.

    Inner folds of whole years give out-of-fold predictions on the training
    data. Each row scores ``max(q10 - y, y - q90)``, how far the observation
    fell outside its interval (negative when inside). The finite-sample 80%
    quantile of those scores is the margin added to both ends (Romano et al.
    2019). The median is left unchanged.
    """

    inner: QuantileModel
    margin: float

    @classmethod
    def fit(
        cls, x: pd.DataFrame, y: pd.Series, days: pd.DatetimeIndex, coverage: float = 0.8
    ) -> ConformalQuantileModel:
        from riverforecast.evaluation import year_folds

        x = x.reset_index(drop=True)
        y = pd.Series(np.asarray(y, dtype=float))
        positions = pd.Series(np.arange(len(days)), index=pd.DatetimeIndex(days))
        scores = []
        for train, held in year_folds(pd.DatetimeIndex(days), years_per_fold=3, buffer_days=14):
            fit_rows = positions.loc[train].to_numpy()
            held_rows = positions.loc[held].to_numpy()
            q10, _, q90 = QuantileModel.fit(x.iloc[fit_rows], y.iloc[fit_rows]).predict(
                x.iloc[held_rows]
            )
            observed = y.iloc[held_rows].to_numpy()
            scores.append(np.maximum(q10 - observed, observed - q90))
        scores = np.concatenate(scores)
        rank = min(1.0, np.ceil((len(scores) + 1) * coverage) / len(scores))
        margin = max(0.0, float(np.quantile(scores, rank, method="higher")))
        return cls(QuantileModel.fit(x, y), margin)

    def predict(self, x: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        q10, q50, q90 = self.inner.predict(x)
        return np.minimum(q10 - self.margin, q50), q50, np.maximum(q90 + self.margin, q50)
