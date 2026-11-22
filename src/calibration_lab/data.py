"""Acquire and verify the immutable scikit-learn copy of the WDBC data."""

import hashlib
import json
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

from calibration_lab.config import LabConfig

SOURCE_URL = (
    "https://raw.githubusercontent.com/scikit-learn/scikit-learn/1.8.0/"
    "sklearn/datasets/data/breast_cancer.csv"
)
DATASET_URL = "https://archive.ics.uci.edu/dataset/17/breast+cancer+wisconsin+diagnostic"
DATASET_DOI = "https://doi.org/10.24432/C5DW2B"
# Verified independently against the official versioned download and package copy.
# Never replace this trust anchor with a digest calculated from the user's input.
EXPECTED_SHA256 = "fed3eb72d0575ef6192293f5093c6e801b1476b577d0386bf4455504522172ed"
EXPECTED_SIZE = 119913
FEATURE_NAMES = tuple(
    f"{prefix}{feature}{suffix}"
    for prefix, suffix in (("mean ", ""), ("", " error"), ("worst ", ""))
    for feature in (
        "radius",
        "texture",
        "perimeter",
        "area",
        "smoothness",
        "compactness",
        "concavity",
        "concave points",
        "symmetry",
        "fractal dimension",
    )
)


class DataIntegrityError(ValueError):
    """Data or the recorded trust anchor has changed."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_bytes(payload: bytes) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != EXPECTED_SHA256 or len(payload) != EXPECTED_SIZE:
        raise DataIntegrityError(
            "Хеш сырых данных не совпадает: "
            f"ожидается {EXPECTED_SHA256}, получен {actual}; "
            f"размер {len(payload)} вместо {EXPECTED_SIZE} байт"
        )


def prepare_data(config: LabConfig, root: Path, download: bool = False) -> dict:
    """Acquire once; an existing damaged file is never silently overwritten."""
    path, manifest_path = root / config.raw_data, root / config.manifest
    if path.exists():
        _check_bytes(path.read_bytes())
        if manifest_path.exists():
            return verify_data(config, root)
        acquisition = "existing_file_verified_against_pinned_sha256"
    else:
        if download:
            with urlopen(SOURCE_URL, timeout=30) as response:
                payload = response.read(EXPECTED_SIZE + 1)
            acquisition = "https_official_scikit_learn_1.8.0"
        else:
            payload = files("sklearn.datasets.data").joinpath("breast_cancer.csv").read_bytes()
            # Windows package wheels can store the CSV with CRLF newlines.
            # Canonicalize package-supplied bytes; the pinned SHA-256 is still verified.
            payload = payload.replace(b"\r\n", b"\n")
            acquisition = "offline_copy_from_installed_scikit_learn_verified_by_sha256"
        _check_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "algorithm": "sha256",
        "dataset": "Breast Cancer Wisconsin (Diagnostic)",
        "source_url": SOURCE_URL,
        "dataset_url": DATASET_URL,
        "doi": DATASET_DOI,
        "license": "CC BY 4.0",
        "acquisition": acquisition,
        "files": [
            {"path": config.raw_data, "sha256": EXPECTED_SHA256, "size_bytes": EXPECTED_SIZE}
        ],
        "format_note": (
            "Official scikit-learn CSV: first line stores sample/feature counts and class names. "
            "Raw class 0=malignant, 1=benign. Original UCI IDs are omitted by scikit-learn."
        ),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return verify_data(config, root)


def verify_data(config: LabConfig, root: Path) -> dict:
    """Verify both the manifest and bytes against the immutable source digest."""
    manifest_path = root / config.manifest
    if not manifest_path.exists():
        raise DataIntegrityError("Нет манифеста. Сначала выполните команду prepare")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files", [])
    expected = {"path": config.raw_data, "sha256": EXPECTED_SHA256, "size_bytes": EXPECTED_SIZE}
    if manifest.get("algorithm") != "sha256" or entries != [expected]:
        raise DataIntegrityError(
            "Манифест не совпадает с закреплённым хешем официального источника"
        )
    path = root / config.raw_data
    if not path.is_file():
        raise DataIntegrityError(f"Нет файла сырых данных: {config.raw_data}")
    _check_bytes(path.read_bytes())
    return {
        "status": "совпадает",
        "path": config.raw_data,
        "sha256": EXPECTED_SHA256,
        "size_bytes": EXPECTED_SIZE,
        "source": SOURCE_URL,
        "dataset_url": DATASET_URL,
        "license": "CC BY 4.0",
        "acquisition": manifest.get("acquisition"),
    }


def load_data(config: LabConfig, root: Path) -> tuple[pd.DataFrame, pd.Series]:
    """Load verified raw values; model y=1 means malignant (not sklearn class 1)."""
    verify_data(config, root)
    frame = pd.read_csv(
        root / config.raw_data, skiprows=1, header=None, names=[*FEATURE_NAMES, "target"]
    )
    if frame.shape != (569, 31) or set(frame["target"]) != {0, 1}:
        raise DataIntegrityError("Неверная структура датасета")
    features = frame.loc[:, list(FEATURE_NAMES)].astype("float64")
    if not np.isfinite(features.to_numpy()).all():
        raise DataIntegrityError("В исходных признаках найдены нечисловые или бесконечные значения")
    target = (frame["target"] == 0).astype("int64").rename("malignant")
    return features, target


def tamper_control(config: LabConfig, root: Path) -> dict:
    """Prove detection on an isolated copy, without touching the real raw data."""
    original = verify_data(config, root)
    with tempfile.TemporaryDirectory(prefix="calibration_tamper_") as folder:
        isolated = Path(folder)
        copied = isolated / config.raw_data
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied_manifest = isolated / config.manifest
        copied_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / config.raw_data, copied)
        shutil.copy2(root / config.manifest, copied_manifest)
        payload = bytearray(copied.read_bytes())
        payload[len(payload) // 2] ^= 1
        copied.write_bytes(payload)
        actual = sha256_file(copied)
        try:
            verify_data(config, isolated)
        except DataIntegrityError as error:
            result = {
                "status": "blocked",
                "rejected": True,
                "control": "single_byte_tamper",
                "expected_sha256": original["sha256"],
                "actual_sha256": actual,
                "error": str(error),
                "original_unchanged": sha256_file(root / config.raw_data) == original["sha256"],
            }
        else:
            raise RuntimeError("Негативный контроль не сработал: изменённый файл был принят")
    output = root / config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "hash_tamper.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "hash_tamper.log").write_text(
        "Негативный контроль: подмена одного байта в изолированной копии\n"
        + f"Ожидаемый SHA-256: {result['expected_sha256']}\n"
        + f"Изменённый SHA-256: {result['actual_sha256']}\n"
        + f"Отказ: {result['error']}\n"
        + f"Оригинальный файл не изменён: {result['original_unchanged']}\n"
        + "PASS: подмена обнаружена, файл отклонён\n",
        encoding="utf-8",
    )
    return result
