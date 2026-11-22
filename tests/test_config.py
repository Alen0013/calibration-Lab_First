"""Invalid settings must stop before any experiment or file mutation."""

import pytest
from pydantic import ValidationError

from calibration_lab.config import LabConfig, load_config


@pytest.mark.parametrize(
    "override",
    [
        {"seed": -1},
        {"bins": 0},
        {"variant": 24},
        {"logistic_c": 0},
        {"test_fraction": 0.4, "calibration_fraction": 0.4},
        {"raw_data": "../external.csv"},
        {"output_dir": "/outside"},
        {"unknown_option": True},
    ],
)
def test_invalid_config_rejected(override):
    with pytest.raises(ValidationError):
        LabConfig(**override)


def test_load_relative_to_config_directory(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    path = configs / "lab1.yaml"
    path.write_text("variant: 25\nseed: 42\n", encoding="utf-8")
    config, root = load_config(path)
    assert root == tmp_path
    assert config.seed == 42
