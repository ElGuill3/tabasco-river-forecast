"""Tests of the probabilistic warning verification module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from riverforecast import warning

# --- exceedance_probability -------------------------------------------------


def test_exceedance_at_the_quantiles():
    q10, q50, q90 = 10.0, 20.0, 30.0
    assert warning.exceedance_probability(q10, q50, q90, q50) == pytest.approx(0.5)
    assert warning.exceedance_probability(q10, q50, q90, q90) == pytest.approx(0.1)
    assert warning.exceedance_probability(q10, q50, q90, q10) == pytest.approx(0.9)


def test_exceedance_is_monotone_decreasing():
    thresholds = np.linspace(5.0, 35.0, 21)
    p = warning.exceedance_probability(10.0, 20.0, 30.0, thresholds)
    assert np.all(np.diff(p) <= 0)
    assert p[0] > 0.9 and p[-1] < 0.1  # tails continue below q10 and above q90


def test_exceedance_zero_width_quantiles_without_nan():
    p = warning.exceedance_probability(20.0, 20.0, 20.0, np.array([19.0, 20.0, 21.0]))
    assert np.all(np.isfinite(p))
    assert p[0] == pytest.approx(1.0)
    assert p[1] == pytest.approx(0.5)
    assert p[2] == pytest.approx(0.0)


def test_exceedance_partly_degenerate_quantiles():
    # A collapsed segment acts like an atom: the probability at the collapsed
    # point is 0.5 (the q50 rule wins), and it stays monotone around it.
    assert warning.exceedance_probability(10.0, 10.0, 30.0, 10.0) == pytest.approx(0.5)
    assert warning.exceedance_probability(10.0, 10.0, 30.0, 20.0) == pytest.approx(0.3)
    assert warning.exceedance_probability(10.0, 10.0, 30.0, 9.0) > 0.9
    assert warning.exceedance_probability(10.0, 30.0, 30.0, 20.0) == pytest.approx(0.7)
    assert warning.exceedance_probability(10.0, 30.0, 30.0, 30.0) == pytest.approx(0.5)
    assert warning.exceedance_probability(10.0, 30.0, 30.0, 31.0) < 0.1


def test_exceedance_is_vectorized():
    p = warning.exceedance_probability(
        np.array([10.0, 10.0]),
        np.array([20.0, 20.0]),
        np.array([30.0, 30.0]),
        np.array([20.0, 30.0]),
    )
    np.testing.assert_allclose(p, [0.5, 0.1])


# --- brier_score / brier_skill_score ----------------------------------------


def test_brier_score():
    p = np.array([0.0, 1.0, 0.5])
    event = np.array([0, 1, 0], dtype=bool)
    assert warning.brier_score(p, event) == pytest.approx(0.25 / 3)


def test_brier_skill_score_perfect_and_climatology():
    event = np.array([0, 1, 0, 0], dtype=bool)
    perfect = event.astype(float)
    assert warning.brier_skill_score(perfect, event, 0.25) == pytest.approx(1.0)
    climatology = np.full(4, 0.25)
    assert warning.brier_skill_score(climatology, event, 0.25) == pytest.approx(0.0)


def test_brier_skill_score_without_events_is_nan():
    event = np.zeros(4, dtype=bool)
    value = warning.brier_skill_score(np.full(4, 0.1), event, 0.0)
    assert np.isnan(value)


# --- roc_auc -----------------------------------------------------------------


def test_roc_auc_with_ties():
    p = np.array([0.1, 0.4, 0.35, 0.8])
    event = np.array([0, 0, 1, 1], dtype=bool)
    assert warning.roc_auc(p, event) == pytest.approx(0.75)
    # Ties count half: one tied pair out of one positive-negative pair.
    assert warning.roc_auc(np.array([0.5, 0.5]), np.array([0, 1], dtype=bool)) == pytest.approx(0.5)


def test_roc_auc_degenerate_is_nan():
    assert np.isnan(warning.roc_auc(np.array([0.2, 0.4]), np.zeros(2, dtype=bool)))
    assert np.isnan(warning.roc_auc(np.array([0.2, 0.4]), np.ones(2, dtype=bool)))


# --- reliability_table -------------------------------------------------------


def test_reliability_table_bins_and_counts():
    p = np.array([0.05, 0.15, 0.95, 0.97])
    event = np.array([0, 1, 1, 1], dtype=bool)
    table = warning.reliability_table(p, event, bins=10)
    assert list(table.columns) == ["bin_low", "bin_high", "mean_p", "frequency", "count"]
    assert len(table) == 3  # empty bins are dropped
    first = table.iloc[0]
    assert first["bin_low"] == 0.0 and first["bin_high"] == 0.1
    assert first["mean_p"] == pytest.approx(0.05)
    assert first["frequency"] == pytest.approx(0.0)
    assert first["count"] == 1
    last = table.iloc[-1]
    assert last["bin_low"] == 0.9 and last["bin_high"] == 1.0
    assert last["mean_p"] == pytest.approx(0.96)
    assert last["frequency"] == pytest.approx(1.0)
    assert last["count"] == 2


def test_reliability_table_empty_input():
    table = warning.reliability_table(np.array([]), np.array([], dtype=bool))
    assert len(table) == 0


# --- contingency -------------------------------------------------------------


def test_contingency_hand_computed():
    warn = np.array([1, 1, 0, 0, 1, 0], dtype=bool)
    event = np.array([1, 0, 0, 0, 0, 0], dtype=bool)
    c = warning.contingency(warn, event)
    assert (c.hits, c.misses, c.false_alarms, c.correct_negatives) == (1, 0, 2, 3)
    assert c.pod == pytest.approx(1.0)
    assert c.false_alarm_ratio == pytest.approx(2 / 3)
    assert c.pofd == pytest.approx(2 / 5)
    assert c.frequency_bias == pytest.approx(3.0)
    assert c.peirce == pytest.approx(0.6)


def test_contingency_no_warnings():
    c = warning.contingency(np.zeros(3, dtype=bool), np.array([1, 0, 0], dtype=bool))
    assert (c.hits, c.false_alarms) == (0, 0)
    assert c.pod == pytest.approx(0.0)
    assert np.isnan(c.false_alarm_ratio)
    # A forecast that never warns is the equivalence forecast: Peirce 0.
    assert c.peirce == pytest.approx(0.0)


def test_contingency_no_events():
    c = warning.contingency(np.array([0, 1], dtype=bool), np.zeros(2, dtype=bool))
    assert c.misses == 0
    assert np.isnan(c.pod)
    assert c.pofd == pytest.approx(0.5)
    assert np.isnan(c.frequency_bias)


# --- relative_economic_value -------------------------------------------------


def test_relative_economic_value_perfect_forecast():
    event = np.array([0, 0, 1, 0, 0], dtype=bool)
    p = event.astype(float)  # perfect
    alphas = np.array([0.1, 0.5])
    v = warning.relative_economic_value(p, event, alphas, threshold=0.5)
    # A perfect warning system is worth one unit of the climatological expense
    # at every cost/loss ratio.
    np.testing.assert_allclose(v, [1.0, 1.0])


def test_relative_economic_value_useless_forecasts():
    event = np.array([0, 0, 1, 0, 0], dtype=bool)
    o = 0.2
    # Always warn: expense = alpha, so V = 0 exactly at alpha = o and below.
    v_always = warning.relative_economic_value(np.ones(5), event, np.array([0.1, 0.5]), 0.5)
    assert v_always[0] == pytest.approx(0.0)
    assert v_always[1] == pytest.approx((o - 0.5) / (o - 0.5 * o))
    # Never warn: expense = o, so V = 0 exactly at alpha >= o.
    v_never = warning.relative_economic_value(np.zeros(5), event, np.array([0.1, 0.5]), 1.1)
    assert v_never[0] == pytest.approx((0.1 - o) / (0.1 - 0.1 * o))
    assert v_never[1] == pytest.approx(0.0)


def test_relative_economic_value_without_events_is_nan():
    event = np.zeros(3, dtype=bool)
    v = warning.relative_economic_value(np.full(3, 0.1), event, np.array([0.5]), 0.5)
    assert np.isnan(v[0])


# --- bias_one_threshold ------------------------------------------------------


def test_bias_one_threshold_matches_event_frequency():
    p = np.array([0.1, 0.2, 0.3, 0.4])
    event = np.array([0, 0, 1, 1], dtype=bool)  # frequency 0.5
    assert warning.bias_one_threshold(p, event) == pytest.approx(0.3)


def test_bias_one_threshold_tie_prefers_higher_threshold():
    p = np.array([0.2, 0.2, 0.6, 0.6, 1.0, 1.0])
    event = np.array([0, 0, 1, 1, 1, 0], dtype=bool)  # frequency 0.5
    # Warning frequencies are 1, 2/3 and 1/3: the two closest (2/3 and 1/3)
    # are equally distant from 0.5, and the higher threshold wins.
    assert warning.bias_one_threshold(p, event) == pytest.approx(1.0)


def test_bias_one_threshold_degenerate_cases():
    # No events: as few warnings as possible, i.e. the largest threshold.
    p = np.array([0.2, 0.6, 0.8])
    assert warning.bias_one_threshold(p, np.zeros(3, dtype=bool)) == pytest.approx(0.8)
    # All events: warn on every case, i.e. the smallest threshold.
    assert warning.bias_one_threshold(p, np.ones(3, dtype=bool)) == pytest.approx(0.2)


# --- persistent --------------------------------------------------------------


def test_persistent_needs_the_previous_issue():
    days = pd.date_range("2020-01-01", periods=4, freq="D")
    warn = pd.Series([True, True, False, True], index=days)
    expected = pd.Series([False, True, False, False], index=days)
    pd.testing.assert_series_equal(warning.persistent(warn), expected)


def test_persistent_missing_previous_day_is_false():
    warn = pd.Series([True], index=pd.DatetimeIndex(["2020-01-01"]))
    assert not warning.persistent(warn).iloc[0]
    # A previous issue that is not available (NaN) also breaks persistence.
    warn = pd.Series([np.nan, True], index=pd.DatetimeIndex(["2020-01-01", "2020-01-02"]))
    assert not warning.persistent(warn).iloc[1]


def test_persistent_unsorted_input_and_empty():
    warn = pd.Series(
        [True, True, True],
        index=pd.DatetimeIndex(["2020-01-03", "2020-01-01", "2020-01-02"]),
    )
    result = warning.persistent(warn)
    assert not result.loc["2020-01-01"]
    assert result.loc["2020-01-02"] and result.loc["2020-01-03"]
    empty = warning.persistent(pd.Series(dtype=bool))
    assert len(empty) == 0
