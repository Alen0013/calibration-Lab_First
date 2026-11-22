"""Numerical conventions and split isolation used by the calibration report."""

import numpy as np
import pandas as pd
import pytest

from calibration_lab.analysis import (
    _code_provenance,
    _git_commit,
    calibration_experiment,
    calibration_table,
    expected_calibration_error,
    split_indices,
    summarize_data,
)
from calibration_lab.config import LabConfig
from calibration_lab.data import load_data, prepare_data


def test_ece_hand_calculated_and_endpoints():
    y = np.array([0, 1, 0, 1])
    probability = np.array([0.0, 0.2, 0.8, 1.0])
    table = calibration_table(y, probability, bins=2)
    assert table["count"].tolist() == [2, 2]
    assert table["positive_count"].tolist() == [1, 1]
    assert expected_calibration_error(y, probability, bins=2) == pytest.approx(0.4)


def test_empty_bins_do_not_change_ece_or_drop_observations():
    table = calibration_table(np.array([0, 1]), np.array([0.0, 1.0]), bins=10)
    assert len(table) == 10
    assert table["count"].sum() == 2
    assert (table["count"] == 0).sum() == 8
    assert table["ece_contribution"].sum() == 0


@pytest.mark.parametrize("probability", [[1.01], [-0.1], [np.nan], [np.inf]])
def test_invalid_probabilities_rejected(probability):
    with pytest.raises(ValueError, match="Вероятности"):
        calibration_table(np.array([1]), np.asarray(probability))


def test_partition_disjoint_exhaustive_reproducible():
    y = pd.Series([0] * 357 + [1] * 212)
    first = split_indices(y, LabConfig())
    second = split_indices(y, LabConfig())
    assert [len(first[name]) for name in ("train", "calibration", "test")] == [341, 114, 114]
    combined = np.concatenate(list(first.values()))
    assert len(np.unique(combined)) == len(y)
    assert set(combined) == set(range(len(y)))
    for name in first:
        np.testing.assert_array_equal(first[name], second[name])
        assert set(y.iloc[first[name]]) == {0, 1}


def test_eda_counts_and_test_predictions(tmp_path):
    config = LabConfig()
    prepare_data(config, tmp_path)
    features, y = load_data(config, tmp_path)
    summary, feature_stats, classes, correlations = summarize_data(features, y, config)
    assert summary["rows"] == 569
    assert summary["missing_values"] == 0
    assert summary["duplicate_rows"] == 0
    assert int(feature_stats["iqr_outliers"].sum()) == summary["outlier_cells"]
    assert classes["share"].sum() == pytest.approx(1.0)
    assert correlations.shape == (30, 30)
    metrics, bins, predictions, ids, splits = calibration_experiment(features, y, config)
    np.testing.assert_array_equal(predictions["row_id"], ids["test"])
    np.testing.assert_array_equal(predictions["malignant"], y.iloc[ids["test"]])
    assert (predictions["naive"] == y.iloc[ids["train"]].mean()).all()
    assert len(predictions) == splits["test"]
    for model in metrics:
        assert bins.loc[bins["model"] == model, "count"].sum() == splits["test"]
        assert all(np.isfinite(value) for value in metrics[model].values())
        assert predictions[model].between(0, 1).all()


def test_source_provenance_without_git_executable(tmp_path, monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("calibration_lab.analysis.subprocess.run", missing_git)
    assert _git_commit(tmp_path) == "unavailable"
    provenance = _code_provenance(tmp_path)
    assert provenance["source_control_status"] == "git_unavailable"
    assert provenance["git_code_dirty"] is None
    assert len(provenance["source_sha256"]) == 64
