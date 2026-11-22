"""Validated, explicit configuration for the first laboratory work."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variant: Literal[25] = 25
    lab: Literal[1] = 1
    author: str = "Автор не указан"
    seed: int = Field(default=42, ge=0, le=4_294_967_295)
    raw_data: str = "data/raw/breast_cancer.csv"
    manifest: str = "hash_manifest.json"
    output_dir: str = "reports/LAB1"
    mlflow_dir: str = "mlruns"
    experiment_name: str = "lab1_variant25_calibration"
    test_fraction: float = Field(default=0.2, ge=0.1, le=0.4)
    calibration_fraction: float = Field(default=0.2, ge=0.1, le=0.4)
    bins: int = Field(default=10, ge=2, le=20)
    logistic_c: float = Field(default=1.0, gt=0, le=1000)
    max_iter: int = Field(default=2000, ge=100, le=10000)
    iqr_multiplier: float = Field(default=1.5, gt=0, le=10)

    @model_validator(mode="after")
    def check_partitions_and_paths(self):
        if self.test_fraction + self.calibration_fraction > 0.7:
            raise ValueError("Для обучения должно оставаться не менее 30% данных")
        for name in ("raw_data", "manifest", "output_dir", "mlflow_dir"):
            path = Path(getattr(self, name))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"{name}: ожидается относительный путь внутри проекта")
        return self


def load_config(config_path: str | Path = "configs/lab1.yaml") -> tuple[LabConfig, Path]:
    """Return validated settings and the project root (parent of configs/)."""
    path = Path(config_path).resolve()
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    config = LabConfig.model_validate(raw)
    return config, path.parent.parent
