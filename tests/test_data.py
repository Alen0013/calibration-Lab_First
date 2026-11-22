"""Trust anchor must detect changed data even when a manifest is also edited."""

import json

import pytest

from calibration_lab.config import LabConfig
from calibration_lab.data import (
    EXPECTED_SHA256,
    DataIntegrityError,
    load_data,
    prepare_data,
    sha256_file,
    tamper_control,
    verify_data,
)


@pytest.fixture
def prepared(tmp_path):
    config = LabConfig()
    prepare_data(config, tmp_path)
    return config, tmp_path


def test_verified_data_schema_and_positive_class(prepared):
    config, root = prepared
    verification = verify_data(config, root)
    assert verification["sha256"] == EXPECTED_SHA256
    assert verification["status"] == "совпадает"
    features, target = load_data(config, root)
    assert features.shape == (569, 30)
    assert target.sum() == 212
    assert target.iloc[0] == 1  # First raw class is malignant=0 in scikit-learn.


def test_data_tamper_rejected_without_silent_repair(prepared):
    config, root = prepared
    path = root / config.raw_data
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + b"X")
    damaged_sha = sha256_file(path)
    with pytest.raises(DataIntegrityError, match="не совпадает"):
        verify_data(config, root)
    with pytest.raises(DataIntegrityError):
        prepare_data(config, root)
    assert sha256_file(path) == damaged_sha


def test_manifest_cannot_launder_tampered_file(prepared):
    config, root = prepared
    path = root / config.raw_data
    path.write_bytes(path.read_bytes() + b"X")
    manifest_path = root / config.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = sha256_file(path)
    manifest["files"][0]["size_bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="Манифест"):
        verify_data(config, root)


def test_missing_manifest_is_rejected(prepared):
    config, root = prepared
    (root / config.manifest).unlink()
    with pytest.raises(DataIntegrityError, match="Нет манифеста"):
        verify_data(config, root)


def test_tamper_demo_preserves_original(prepared):
    config, root = prepared
    result = tamper_control(config, root)
    assert result["rejected"] is True
    assert result["original_unchanged"] is True
    assert result["actual_sha256"] != EXPECTED_SHA256
    assert verify_data(config, root)["sha256"] == EXPECTED_SHA256
