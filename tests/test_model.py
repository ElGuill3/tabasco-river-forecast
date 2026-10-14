import numpy as np
import pandas as pd
import pytest

from riverforecast.model import QuantileModel


def synthetic(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    x = pd.DataFrame({"a": rng.uniform(0, 10, n), "b": rng.uniform(0, 1, n)})
    noise_scale = 0.2 + 0.1 * x["a"]  # spread grows with a
    y = 10 + 0.8 * x["a"] + rng.normal(0, 1, n) * noise_scale
    return x, pd.Series(y)


def test_quantiles_cover_their_share_of_new_data():
    x, y = synthetic()
    x_new, y_new = synthetic(seed=1)

    model = QuantileModel.fit(x, y)
    q10, q50, q90 = model.predict(x_new)

    assert np.mean(y_new <= q10) == pytest.approx(0.1, abs=0.04)
    assert np.mean(y_new <= q50) == pytest.approx(0.5, abs=0.05)
    assert np.mean(y_new <= q90) == pytest.approx(0.9, abs=0.04)
    # The interval follows the growing noise.
    wide = x_new["a"] > 8
    narrow = x_new["a"] < 2
    assert np.mean((q90 - q10)[wide]) > 2 * np.mean((q90 - q10)[narrow])


def test_quantiles_never_cross():
    x, y = synthetic(n=200)
    model = QuantileModel.fit(x, y)

    q10, q50, q90 = model.predict(synthetic(n=2000, seed=3)[0])

    assert np.all(q10 <= q50) and np.all(q50 <= q90)


def test_fitting_is_deterministic():
    x, y = synthetic(n=500)

    first = QuantileModel.fit(x, y).predict(x)
    second = QuantileModel.fit(x, y).predict(x)

    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a, b)


def test_predict_requires_the_training_columns_in_order():
    x, y = synthetic(n=200)
    model = QuantileModel.fit(x, y)

    with pytest.raises(ValueError, match=r"expected columns \['a', 'b'\], got \['b', 'a'\]"):
        model.predict(x[["b", "a"]])


def overfit_case(n, seed):
    """Few noisy rows, so the raw quantile regressors produce too-narrow intervals."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2000-01-01", periods=n, freq="5D")
    x = pd.DataFrame({"a": rng.uniform(0, 10, n), "b": rng.uniform(0, 1, n)})
    y = pd.Series(10 + 0.8 * x["a"] + rng.normal(0, 1, n))
    return x, y, days


def test_conformal_widening_restores_the_nominal_coverage():
    from riverforecast.model import ConformalQuantileModel

    x, y, days = overfit_case(600, seed=4)
    x_new, y_new, _ = overfit_case(4000, seed=5)

    raw = QuantileModel.fit(x, y).predict(x_new)
    calibrated = ConformalQuantileModel.fit(x, y, days).predict(x_new)

    def cover(q):
        return np.mean((y_new >= q[0]) & (y_new <= q[2]))

    assert cover(raw) < 0.72
    assert cover(calibrated) == pytest.approx(0.8, abs=0.05)
    # The median is not moved, only the interval.
    assert np.array_equal(calibrated[1], raw[1])


def test_conformal_margin_is_never_negative():
    from riverforecast.model import ConformalQuantileModel

    rng = np.random.default_rng(6)
    days = pd.date_range("2000-01-01", periods=600, freq="5D")
    x = pd.DataFrame({"a": rng.uniform(0, 10, 600)})
    y = pd.Series(2 * x["a"])  # no noise: the raw interval is already wide enough

    model = ConformalQuantileModel.fit(x, y, days)

    assert model.margin >= 0
