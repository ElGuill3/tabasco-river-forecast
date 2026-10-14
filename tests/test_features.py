import numpy as np
import pandas as pd
import pytest

from riverforecast import features

DAYS = pd.date_range("2004-01-01", "2004-03-31", freq="D")


def reanalysis(values=None):
    values = np.arange(1, len(DAYS) + 1, dtype=float) * 100 if values is None else values
    return pd.Series(values, index=DAYS)


def summary(issues, lead=3):
    issues = pd.to_datetime(issues)
    return pd.DataFrame(
        {
            "issue": issues,
            "lead_days": lead,
            "valid": issues + pd.Timedelta(days=lead),
            "members": 11,
            "control": 1000.0,
            "mean": 1100.0,
            "q10": 900.0,
            "q50": 1000.0,
            "q90": 1400.0,
        }
    )


def test_one_row_per_issue_targeting_the_valid_day():
    table = features.build(summary(["2004-02-01", "2004-02-05"]), reanalysis(), lead=3)

    assert list(table.index) == list(pd.to_datetime(["2004-02-01", "2004-02-05"]))
    assert list(table["valid"]) == list(pd.to_datetime(["2004-02-04", "2004-02-08"]))
    assert list(table.columns) == ["valid", *features.COLUMNS]


def test_forecast_columns_are_log_discharge_and_spread():
    row = features.build(summary(["2004-02-01"]), reanalysis(), lead=3).iloc[0]

    assert row["fc_q50"] == pytest.approx(np.log(1000))
    assert row["fc_mean"] == pytest.approx(np.log(1100))
    assert row["fc_spread"] == pytest.approx(np.log(1400) - np.log(900))


def test_past_discharge_uses_only_days_up_to_the_issue():
    issue = pd.Timestamp("2004-02-01")
    row = features.build(summary([issue]), reanalysis(), lead=3).iloc[0]
    q = reanalysis()

    assert row["past_q0"] == pytest.approx(np.log(q[issue]))
    assert row["past_q3"] == pytest.approx(np.log(q[issue - pd.Timedelta(days=3)]))
    assert row["past_q7"] == pytest.approx(np.log(q[issue - pd.Timedelta(days=7)]))
    assert row["past_trend3"] == pytest.approx(
        np.log(q[issue]) - np.log(q[issue - pd.Timedelta(days=3)])
    )
    assert row["fc_vs_now"] == pytest.approx(np.log(1000) - np.log(q[issue]))


def test_changing_the_future_does_not_change_the_features():
    issue = "2004-02-01"
    original = features.build(summary([issue]), reanalysis(), lead=3)
    altered = reanalysis()
    altered[altered.index > issue] = 1.0

    assert features.build(summary([issue]), altered, lead=3).equals(original)


def test_season_describes_the_target_day():
    row = features.build(summary(["2004-02-01"]), reanalysis(), lead=3).iloc[0]
    target = pd.Timestamp("2004-02-04")

    assert row["season_sin"] == pytest.approx(np.sin(2 * np.pi * target.dayofyear / 365.25))
    assert row["season_cos"] == pytest.approx(np.cos(2 * np.pi * target.dayofyear / 365.25))


def test_issues_without_past_discharge_are_dropped():
    table = features.build(summary(["2004-01-03", "2004-02-01"]), reanalysis(), lead=3)

    assert list(table.index) == [pd.Timestamp("2004-02-01")]


def test_other_leads_are_ignored():
    mixed = pd.concat([summary(["2004-02-01"], lead=1), summary(["2004-02-01"], lead=3)])

    table = features.build(mixed, reanalysis(), lead=1)

    assert list(table["valid"]) == [pd.Timestamp("2004-02-02")]
