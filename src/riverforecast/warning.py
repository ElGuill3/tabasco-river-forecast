"""Probabilistic verification of the high-water warning rule.

The frozen model issues q10/q50/q90 of the stage. This module turns them into
an exceedance probability of the high-water threshold and scores a binary
warning rule with the standard verification measures of Wilks (2011),
"Statistical Methods in the Atmospheric Sciences", and Jolliffe and Stephenson
(2012), "Forecast Verification: A Practitioner's Guide"; the relative economic
value follows Richardson (2000). All functions are pure: arrays or series in,
numbers or tables out.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf

import numpy as np
import pandas as pd

# z such that Phi(z) = 0.9: the normal-tail sigmas match the q10/q90 spacing.
Z90 = 1.2815515655446004


def _normal_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF; finite for +-inf (0 and 1) and for zero sigma."""
    return 0.5 * (1.0 + np.vectorize(erf, otypes=[float])(x / np.sqrt(2.0)))


def exceedance_probability(q10, q50, q90, threshold) -> np.ndarray:
    """P(stage >= threshold) from a piecewise-linear CDF with normal tails.

    The CDF passes through (q10, 0.1), (q50, 0.5) and (q90, 0.9); beyond q10
    and q90 it continues as a normal tail with sigma (q50-q10)/z90 and
    (q90-q50)/z90 respectively, matched to the linear part at q10 and q90.
    Zero-width segments are handled without NaN.
    """
    q10 = np.asarray(q10, dtype=float)
    q50 = np.asarray(q50, dtype=float)
    q90 = np.asarray(q90, dtype=float)
    t = np.asarray(threshold, dtype=float)
    q10, q50, q90, t = np.broadcast_arrays(q10, q50, q90, t)

    cdf = np.full(t.shape, np.nan)
    at_q50 = t == q50
    cdf[at_q50] = 0.5
    below = t < q10
    above = t > q90
    mid_lower = ~at_q50 & ~below & ~above & (t < q50)
    mid_upper = ~at_q50 & ~below & ~above & (t > q50)

    # Linear segments: t < q50 implies q10 < q50, so the slope is well defined.
    cdf[mid_lower] = 0.1 + 0.4 * (t[mid_lower] - q10[mid_lower]) / (q50[mid_lower] - q10[mid_lower])
    cdf[mid_upper] = 0.5 + 0.4 * (t[mid_upper] - q50[mid_upper]) / (q90[mid_upper] - q50[mid_upper])

    # Normal tails; a zero sigma makes them a step, which is finite everywhere
    # because t is strictly below q10 (or strictly above q90) here.
    with np.errstate(divide="ignore"):
        low_sigma = (q50 - q10) / Z90
        high_sigma = (q90 - q50) / Z90
        cdf[below] = 0.1 * _normal_cdf((t[below] - q10[below]) / low_sigma[below])
        cdf[above] = 0.9 + 0.1 * _normal_cdf((t[above] - q90[above]) / high_sigma[above])
    return 1.0 - cdf


def brier_score(p, event) -> float:
    """Mean squared error of the probability forecast."""
    p = np.asarray(p, dtype=float)
    event = np.asarray(event, dtype=bool)
    return float(np.mean((p - event) ** 2))


def brier_skill_score(p, event, base_rate: float) -> float:
    """Brier skill score against a climatological forecast of ``base_rate``.

    NaN when the reference has no skill to beat (base rate 0 or 1 with any
    error at all): the score is then undefined.
    """
    reference = float(np.mean((base_rate - np.asarray(event, dtype=bool)) ** 2))
    if reference == 0.0:
        return float("nan")
    return 1.0 - brier_score(p, event) / reference


def roc_auc(p, event) -> float:
    """Rank-based ROC area (Mann-Whitney); ties count half. NaN without events or non-events."""
    p = np.asarray(p, dtype=float)
    event = np.asarray(event, dtype=bool)
    n_event = int(event.sum())
    n_none = int(event.size - n_event)
    if not n_event or not n_none:
        return float("nan")
    ranks = pd.Series(p).rank(method="average").to_numpy()
    return float((ranks[event].sum() - n_event * (n_event + 1) / 2) / (n_event * n_none))


def reliability_table(p, event, bins: int = 10) -> pd.DataFrame:
    """Per probability bin: mean forecast probability, observed frequency, count.

    Empty bins are dropped. Probabilities of exactly 1.0 fall in the last bin.
    """
    p = np.asarray(p, dtype=float)
    event = np.asarray(event, dtype=bool)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip((p * bins).astype(int), 0, bins - 1)
    rows = []
    for i in range(bins):
        inside = index == i
        count = int(inside.sum())
        if not count:
            continue
        rows.append(
            {
                "bin_low": edges[i],
                "bin_high": edges[i + 1],
                "mean_p": float(np.mean(p[inside])),
                "frequency": float(np.mean(event[inside])),
                "count": count,
            }
        )
    return pd.DataFrame(rows, columns=["bin_low", "bin_high", "mean_p", "frequency", "count"])


@dataclass(frozen=True)
class Contingency:
    """2x2 contingency table of a binary warning and its scores."""

    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int

    @property
    def events(self) -> int:
        return self.hits + self.misses

    @property
    def warnings(self) -> int:
        return self.hits + self.false_alarms

    @property
    def pod(self) -> float:
        """Probability of detection (hit rate)."""
        return self.hits / self.events if self.events else float("nan")

    @property
    def false_alarm_ratio(self) -> float:
        """Share of warnings that were false alarms."""
        return self.false_alarms / self.warnings if self.warnings else float("nan")

    @property
    def pofd(self) -> float:
        """Probability of false detection (false-alarm rate)."""
        none = self.false_alarms + self.correct_negatives
        return self.false_alarms / none if none else float("nan")

    @property
    def frequency_bias(self) -> float:
        return self.warnings / self.events if self.events else float("nan")

    @property
    def peirce(self) -> float:
        """Peirce (Hanssen-Kuipers) skill score: POD - POFD."""
        pod, pofd = self.pod, self.pofd
        if np.isnan(pod) or np.isnan(pofd):
            return float("nan")
        return pod - pofd


def contingency(warn, event) -> Contingency:
    warn = np.asarray(warn, dtype=bool)
    event = np.asarray(event, dtype=bool)
    return Contingency(
        hits=int(np.sum(warn & event)),
        misses=int(np.sum(~warn & event)),
        false_alarms=int(np.sum(warn & ~event)),
        correct_negatives=int(np.sum(~warn & ~event)),
    )


def relative_economic_value(p, event, cost_loss_ratios, threshold: float) -> np.ndarray:
    """Relative economic value V(alpha) of Richardson (2000).

    A user protects (pays the cost alpha = C/L) when the warning fires
    (``p >= threshold``) and otherwise absorbs the loss of every event.
    V = (E_clim - E_fc) / (E_clim - E_perfect) with E_clim = min(alpha, o),
    E_perfect = alpha * o and o the base rate of ``event``; V = 1 for a perfect
    warning system and V <= 0 worse than climatology. NaN where o is 0 or 1.
    """
    p = np.asarray(p, dtype=float)
    event = np.asarray(event, dtype=bool)
    alphas = np.asarray(cost_loss_ratios, dtype=float)
    o = float(np.mean(event))
    warn = p >= threshold
    hits = float(np.sum(warn & event))
    false_alarms = float(np.sum(warn & ~event))
    misses = float(np.sum(~warn & event))
    n = float(event.size)
    # Expense per unit loss of the warning system: the cost on every warning
    # plus the full loss on every unwarned event.
    e_fc = alphas * (hits + false_alarms) / n + misses / n
    e_clim = np.minimum(alphas, o)
    e_perfect = alphas * o
    denominator = e_clim - e_perfect
    defined = denominator > 0
    return np.where(defined, (e_clim - e_fc) / np.where(defined, denominator, 1.0), np.nan)


def bias_one_threshold(p, event) -> float:
    """Probability threshold whose warning frequency is closest to the event frequency.

    Candidates are the unique forecast probabilities; ties in the distance go
    to the higher threshold. Without events the highest threshold wins (fewest
    warnings); with only events the lowest one (warn on everything).
    """
    p = np.asarray(p, dtype=float)
    event = np.asarray(event, dtype=bool)
    if p.size == 0:
        raise ValueError("bias_one_threshold needs at least one forecast")
    event_frequency = float(np.mean(event))
    candidates = np.unique(p)
    frequencies = np.array([(p >= t).mean() for t in candidates])
    distance = np.abs(frequencies - event_frequency)
    # Distances that are equal only up to float error count as tied, and the
    # tie goes to the higher threshold (fewer warnings).
    tied = np.isclose(distance, distance.min(), rtol=0.0, atol=1e-12)
    return float(candidates[np.where(tied)[0][-1]])


def persistent(warn: pd.Series) -> pd.Series:
    """True only where the warning also held on the previous issue.

    ``warn`` is a boolean series indexed by issue day. The previous issue is
    the previous row of the series (issues may not be daily); a missing
    previous value, or no previous issue at all, gives False.
    """
    warn = warn.sort_index().fillna(False).astype(bool)
    previous = warn.shift(1, fill_value=False)
    return warn & previous
