import numpy as np
import pandas as pd
import pytest

from riverforecast import evaluation as ev


def test_point_errors():
    observed = np.array([10.0, 12.0, 14.0])
    predicted = np.array([11.0, 12.0, 11.0])

    assert ev.mae(observed, predicted) == pytest.approx(4 / 3)
    assert ev.rmse(observed, predicted) == pytest.approx(np.sqrt(10 / 3))


def test_pinball_loss_penalizes_each_side_by_the_quantile():
    observed = np.array([10.0, 10.0])
    # Under-prediction by 2 costs q * 2; over-prediction by 2 costs (1 - q) * 2.
    assert ev.pinball(observed, np.array([8.0, 12.0]), 0.9) == pytest.approx((1.8 + 0.2) / 2)
    assert ev.pinball(observed, np.array([8.0, 12.0]), 0.1) == pytest.approx((0.2 + 1.8) / 2)


def test_interval_coverage_and_width():
    observed = np.array([1.0, 5.0, 9.0, 4.0])
    low = np.array([0.0, 6.0, 8.0, 4.0])
    high = np.array([2.0, 8.0, 10.0, 6.0])

    assert ev.coverage(observed, low, high) == pytest.approx(3 / 4)  # bounds count as inside
    assert ev.width(low, high) == pytest.approx(2.0)


def test_exceedance_scores_count_hits_misses_and_false_alarms():
    observed = np.array([19.0, 19.5, 17.0, 18.0, 16.0])
    predicted = np.array([18.6, 17.0, 18.7, 18.0, 15.0])

    scores = ev.exceedance_scores(observed, predicted, threshold=18.5)

    assert scores == ev.Exceedance(hits=1, misses=1, false_alarms=1, correct_negatives=2)
    assert scores.probability_of_detection == pytest.approx(0.5)
    assert scores.false_alarm_ratio == pytest.approx(0.5)
    assert scores.critical_success_index == pytest.approx(1 / 3)


def test_exceedance_ratios_are_nan_without_events():
    scores = ev.exceedance_scores(np.array([1.0]), np.array([1.0]), threshold=5.0)

    assert np.isnan(scores.probability_of_detection)
    assert np.isnan(scores.false_alarm_ratio)
    assert np.isnan(scores.critical_success_index)


def test_development_and_test_years_never_overlap():
    days = pd.date_range("1998-01-01", "2025-12-31", freq="D")

    development, test = ev.split_days(days)

    assert development.min() == pd.Timestamp("1998-01-01")
    assert development.max() == pd.Timestamp("2016-12-31")
    assert test.min() == pd.Timestamp("2017-01-01")
    assert test.max() == pd.Timestamp("2025-12-31")
    assert development.intersection(test).empty


def test_year_folds_hold_out_whole_years_with_a_buffer():
    days = pd.date_range("1998-01-01", "2016-12-31", freq="D")

    folds = list(ev.year_folds(days, years_per_fold=4, buffer_days=7))

    assert [sorted(set(held.year)) for _, held in folds][:2] == [
        [1998, 1999, 2000, 2001],
        [2002, 2003, 2004, 2005],
    ]
    for train, held in folds:
        assert train.intersection(held).empty
        gap = min(abs((train - day).days).min() for day in (held.min(), held.max()))
        assert gap > 7


def test_summary_reports_every_metric_for_a_quantile_forecast():
    observed = np.array([10.0, 12.0, 19.0, 20.0])
    q10 = np.array([9.0, 11.0, 17.0, 17.0])
    q50 = np.array([10.0, 13.0, 18.0, 19.0])
    q90 = np.array([11.0, 14.0, 19.5, 19.5])

    row = ev.summarize(observed, q10, q50, q90, high_water=18.5)

    assert row == {
        "n": 4,
        "mae": pytest.approx(0.75),
        "rmse": pytest.approx(np.sqrt(3 / 4)),
        "pinball_q10": pytest.approx(ev.pinball(observed, q10, 0.1)),
        "pinball_q50": pytest.approx(ev.pinball(observed, q50, 0.5)),
        "pinball_q90": pytest.approx(ev.pinball(observed, q90, 0.9)),
        "coverage_80": pytest.approx(0.75),
        "width_80": pytest.approx(2.5),
        "high_water_n": 2,
        "high_water_mae": pytest.approx(1.0),
        "detection": pytest.approx(0.5),
        "false_alarm_ratio": pytest.approx(0.0),
    }
