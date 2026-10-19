"""Frozen publishing contract; all serving tests work without training files."""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from riverforecast import features, publish


class ConstantModel:
    def predict(self, x):
        return tuple(np.full(len(x), value) for value in (10.0, 12.0, 14.0))


class Provider:
    def inputs(self, issue):
        past = pd.Series(100.0, index=pd.date_range(issue - pd.Timedelta(days=10), issue))
        summary = pd.DataFrame(
            [
                dict(
                    issue=issue,
                    lead_days=h,
                    valid=issue + pd.Timedelta(days=h),
                    control=100.0,
                    mean=100.0,
                    q10=90.0,
                    q50=100.0,
                    q90=110.0,
                )
                for h in (1, 3, 7)
            ]
        )
        return summary, past


@pytest.fixture
def artifact(tmp_path):
    models = {mode: {h: ConstantModel() for h in (1, 3, 7)} for mode in ("live", "glofas_only")}
    joblib.dump(models, tmp_path / "models.joblib")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"model_version": "test", "sklearn_version": publish.sklearn.__version__})
    )
    return tmp_path


@pytest.mark.parametrize("age,mode", [(0, "live"), (2, "live"), (7, "live"), (8, "glofas_only")])
def test_publish_schema_and_age(artifact, age, mode):
    issue = pd.Timestamp("2026-10-13")
    observations = pd.DataFrame(
        {
            "date": [issue - pd.Timedelta(days=age)],
            "stage_m": [13.2],
            "table_last_modified": ["2026-10-13T16:00:00Z"],
        }
    )
    result = publish.forecast(artifact, issue, observations, Provider())
    assert result == publish.forecast(artifact, issue, observations, Provider())
    assert result["schema"] == "bdctb-forecast/v2"
    assert result["mode"] == mode
    assert result["issue_time"] == "2026-10-13T11:00:00-06:00"
    assert result["generated_at"] == result["issue_time"]
    assert result["station"] == {"code": "BDCTB", "name": "Boca del Cerro"}
    assert result["disclaimer"] == publish.DISCLAIMER
    assert result["live_input"] == (
        None
        if mode == "glofas_only"
        else {
            "date": f"{issue - pd.Timedelta(days=age):%Y-%m-%d}",
            "stage_m": 13.2,
            "age_days": age,
        }
    )
    for h in (1, 3, 7):
        row = result["forecasts"][f"h{h}"]
        assert row == {
            "target_date": f"{issue + pd.Timedelta(days=h):%Y-%m-%d}",
            "q10": 10.0,
            "q50": 12.0,
            "q90": 14.0,
            "distance_to_namo_m": 12.0 - 19.21,
            "namo_m": 19.21,
            "data_kind": "modeled_not_observed",
        }


def test_upload_cutoff_and_future_values(artifact):
    obs = pd.DataFrame(
        {
            "date": ["2026-10-13", "2026-10-14"],
            "stage_m": [13.0, 14.0],
            "table_last_modified": ["2026-10-13T17:01:00Z"] * 2,
        }
    )
    assert publish.forecast(artifact, "2026-10-13", obs, Provider())["mode"] == "glofas_only"
    obs.loc[0, "table_last_modified"] = "2026-10-13T17:00:00Z"
    assert publish.forecast(artifact, "2026-10-13", obs, Provider())["live_input"]["age_days"] == 0


def test_missing_forecast_rejected(artifact):
    class Missing(Provider):
        def inputs(self, issue):
            summary, past = super().inputs(issue)
            return summary[summary.lead_days != 7], past

    with pytest.raises(ValueError, match="missing GloFAS inputs for h7"):
        publish.forecast(artifact, "2026-10-13", publish.empty_observations(), Missing())


def test_open_meteo_is_explicit_stub():
    with pytest.raises(NotImplementedError, match="T19"):
        publish.OpenMeteoProvider().inputs(pd.Timestamp("2026-10-13"))


def test_historical_parity():
    """Compare stored artifact with experiment 13's independent operational path."""
    import importlib.util

    from riverforecast import data, live

    if not Path("models/bdctb/models.joblib").exists() or not live.READER_OUTPUT.exists():
        pytest.skip("requires frozen artifact and local research archive")
    spec = importlib.util.spec_from_file_location("postaudit", "experiments/13_postaudit.py")
    experiment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experiment)
    boca = live.boca_rows()
    visibility = live.boca_table_visibility(boca, experiment.manifest_last_modified())
    summary, past = publish.CemsArchiveProvider().inputs(pd.Timestamp("2026-10-13"))
    common = pd.date_range("2023-08-08", "2024-01-15")
    for h in (1, 3, 7):
        common = common.intersection(features.build(summary, past, h).index)
    real = live.causal_revealed_values(boca, visibility, common)
    selected = [
        real.index[real.obs_age.eq(0)][0],
        real.index[real.obs_age.gt(0)][0],
        real.index[real.obs_last.isna()][0],
    ]
    # Independent experiment-13 refit: verifies the artifact's training recipe,
    # not merely its serialization or the serving feature path.
    stage = data.stage("BDCTB")
    training_past = data.glofas_training_past("BDCTB")
    census = live.levels_census().loc[experiment.CENSUS_START : experiment.CENSUS_END]
    availability = live.tile_availability(
        census, pd.date_range(experiment.TILE_START, experiment.M1_END)
    )
    simulated = live.revealed_values(stage.reindex(availability.index), availability)
    reforecast = data.glofas_reforecast_summary("BDCTB")
    training_days = stage.index[(stage.index >= "1998-01-01") & (stage.index <= experiment.M1_END)]
    models = {"live": {}, "glofas_only": {}}
    for h in (1, 3, 7):
        train = features.build(reforecast, training_past, h).drop_duplicates("valid")
        train = train[train.valid.isin(training_days)]
        models["live"][h] = experiment.fit_l1(train, stage, simulated, training_past)
        models["glofas_only"][h] = experiment.ConformalQuantileModel.fit(
            train[features.COLUMNS], stage.loc[train.valid], pd.DatetimeIndex(train.valid)
        )
    real["obs_day_q"] = live.obs_day_discharge(real, past)
    for day in selected:
        # Export only the causally visible value, carrying its effective availability.
        value = real.loc[day]
        obs = (
            publish.empty_observations()
            if pd.isna(value.obs_last)
            else pd.DataFrame(
                {
                    "date": [value.obs_day],
                    "stage_m": [value.obs_last],
                    "table_last_modified": [f"{day:%Y-%m-%d}T16:00:00Z"],
                }
            )
        )
        result = publish.forecast("models/bdctb", day, obs, publish.CemsArchiveProvider())
        mode = "glofas_only" if pd.isna(value.obs_last) else "live"
        errors = []
        for h in (1, 3, 7):
            table = features.build(summary, past, h).loc[[day]]
            if mode == "live":
                table = table.join(real)
            columns = experiment.L1_COLUMNS if mode == "live" else features.COLUMNS
            expected = models[mode][h].predict(table[columns])
            actual = result["forecasts"][f"h{h}"]
            np.testing.assert_allclose(
                [actual[k] for k in ("q10", "q50", "q90")],
                np.asarray(expected).ravel(),
                rtol=0,
                atol=1e-6,
            )
            errors.append(
                max(abs(actual[k] - expected[i][0]) for i, k in enumerate(("q10", "q50", "q90")))
            )
        print(f"PARITY {day:%Y-%m-%d} age={value.obs_age} errors={errors}")
