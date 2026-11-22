"""Reproducible EDA and an explicitly separated calibration demonstration."""

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from calibration_lab.config import LabConfig, load_config
from calibration_lab.data import load_data, sha256_file, verify_data

COLORS = {"uncalibrated": "#4c78a8", "calibrated": "#14a090", "naive": "#b9a58a"}
MODEL_LABELS = {
    "uncalibrated": "Логистическая регрессия",
    "calibrated": "После sigmoid-калибровки",
    "naive": "Постоянная вероятность",
}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _write_csv(frame: pd.DataFrame, path: Path, index: bool = False) -> None:
    frame.to_csv(path, index=index, encoding="utf-8", lineterminator="\n", float_format="%.15g")


def split_indices(y: pd.Series, config: LabConfig) -> dict[str, np.ndarray]:
    """Stratified disjoint train/calibration/test; sorted stable row identifiers."""
    indices = np.arange(len(y))
    holdout = config.calibration_fraction + config.test_fraction
    train, rest = train_test_split(indices, test_size=holdout, stratify=y, random_state=config.seed)
    calibration, test = train_test_split(
        rest,
        test_size=config.test_fraction / holdout,
        stratify=y.iloc[rest],
        random_state=config.seed,
    )
    return {
        name: np.sort(ids)
        for name, ids in (("train", train), ("calibration", calibration), ("test", test))
    }


def calibration_table(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Equal-width bins; last interval includes p=1, all others are left-closed."""
    y, probability = np.asarray(y), np.asarray(probability, dtype=float)
    if y.ndim != 1 or probability.ndim != 1 or len(y) != len(probability) or not len(y):
        raise ValueError("Метки и вероятности должны быть непустыми векторами одной длины")
    if not np.isfinite(probability).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("Вероятности должны быть конечными числами от 0 до 1")
    if not set(np.unique(y)).issubset({0, 1}):
        raise ValueError("Ожидаются двоичные метки")
    if not isinstance(bins, int) or bins < 2:
        raise ValueError("Нужно не менее двух корзин")
    edges = np.linspace(0, 1, bins + 1)
    bin_ids = np.minimum(np.searchsorted(edges, probability, side="right") - 1, bins - 1)
    rows = []
    for index in range(bins):
        mask = bin_ids == index
        count = int(mask.sum())
        mean_p = float(probability[mask].mean()) if count else np.nan
        frequency = float(y[mask].mean()) if count else np.nan
        rows.append(
            {
                "bin": index,
                "left": float(edges[index]),
                "right": float(edges[index + 1]),
                "count": count,
                "positive_count": int(y[mask].sum()),
                "mean_probability": mean_p,
                "observed_fraction": frequency,
                "absolute_gap": abs(mean_p - frequency) if count else np.nan,
                "ece_contribution": count / len(y) * abs(mean_p - frequency) if count else 0.0,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> float:
    return float(calibration_table(y, probability, bins)["ece_contribution"].sum())


def summarize_data(features: pd.DataFrame, y: pd.Series, config: LabConfig):
    """Produce descriptive statistics, not rules for fitting or deleting test rows."""
    q1, q3 = features.quantile(0.25), features.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - config.iqr_multiplier * iqr, q3 + config.iqr_multiplier * iqr
    outliers = features.lt(lower) | features.gt(upper)
    feature_stats = (
        pd.DataFrame(
            {
                "dtype": features.dtypes.astype(str),
                "count": features.count(),
                "missing": features.isna().sum(),
                "negative": features.lt(0).sum(),
                "zero": features.eq(0).sum(),
                "mean": features.mean(),
                "std": features.std(ddof=1),
                "min": features.min(),
                "q1": q1,
                "median": features.median(),
                "q3": q3,
                "max": features.max(),
                "iqr_lower": lower,
                "iqr_upper": upper,
                "iqr_outliers": outliers.sum(),
            }
        )
        .rename_axis("feature")
        .reset_index()
    )
    correlations = features.corr(method="pearson")
    upper_triangle = correlations.where(np.triu(np.ones(correlations.shape), k=1).astype(bool))
    strongest_pair = upper_triangle.abs().stack().idxmax()
    counts = {"benign": int((y == 0).sum()), "malignant": int((y == 1).sum())}
    shares = {name: count / len(y) for name, count in counts.items()}
    summary = {
        "rows": int(len(features)),
        "features": int(features.shape[1]),
        "missing_values": int(features.isna().sum().sum()),
        "duplicate_rows": int(features.assign(malignant=y).duplicated().sum()),
        "duplicate_feature_rows": int(features.duplicated().sum()),
        "negative_values": int(features.lt(0).sum().sum()),
        "zero_values": int(features.eq(0).sum().sum()),
        "nonfinite_values": int((~np.isfinite(features.to_numpy())).sum()),
        "outlier_cells": int(outliers.sum().sum()),
        "outlier_rows": int(outliers.any(axis=1).sum()),
        "class_counts": counts,
        "class_shares": shares,
        "maximum_abs_correlation": float(abs(correlations.loc[strongest_pair])),
        "correlation_pair": list(strongest_pair),
        "correlation_pair_value": float(correlations.loc[strongest_pair]),
    }
    class_balance = pd.DataFrame(
        [
            {
                "class": name,
                "model_target": int(name == "malignant"),
                "count": counts[name],
                "share": shares[name],
            }
            for name in ("benign", "malignant")
        ]
    )
    return summary, feature_stats, class_balance, correlations


def _metric_values(y, probability, bins):
    return {
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "ece": expected_calibration_error(y, probability, bins),
        "accuracy": float(accuracy_score(y, probability >= 0.5)),
        "roc_auc": float(roc_auc_score(y, probability)),
    }


def calibration_experiment(features: pd.DataFrame, y: pd.Series, config: LabConfig):
    ids = split_indices(y, config)
    train, calibration, test = (ids[key] for key in ("train", "calibration", "test"))
    base = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=config.logistic_c, max_iter=config.max_iter, random_state=config.seed),
    )
    # Restrict BLAS threads to make the same-environment repeat byte-for-byte stable.
    with threadpool_limits(limits=1):
        base.fit(features.iloc[train], y.iloc[train])
        calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
        calibrated.fit(features.iloc[calibration], y.iloc[calibration])
        predictions = {
            "uncalibrated": base.predict_proba(features.iloc[test])[:, 1],
            "calibrated": calibrated.predict_proba(features.iloc[test])[:, 1],
            "naive": np.full(len(test), y.iloc[train].mean()),
        }
    y_test = y.iloc[test].to_numpy()
    metrics = {name: _metric_values(y_test, p, config.bins) for name, p in predictions.items()}
    tables = [
        calibration_table(y_test, p, config.bins).assign(model=name)
        for name, p in predictions.items()
    ]
    bins_table = pd.concat(tables, ignore_index=True)
    test_table = pd.DataFrame({"row_id": test, "malignant": y_test, **predictions})
    splits = {name: int(len(values)) for name, values in ids.items()}
    splits["class_counts"] = {
        name: {
            "benign": int((y.iloc[values] == 0).sum()),
            "malignant": int((y.iloc[values] == 1).sum()),
        }
        for name, values in ids.items()
    }
    splits["train_malignant_prevalence"] = float(y.iloc[train].mean())
    return metrics, bins_table, test_table, ids, splits


def _save_figure(fig, path):
    fig.savefig(
        path,
        dpi=160,
        bbox_inches="tight",
        facecolor="white",
        metadata={"Software": "calibration-lab1"},
    )
    plt.close(fig)


def make_plots(
    features, y, summary, feature_stats, correlations, metrics, bins_table, config, output
):
    sns.set_theme(style="whitegrid", font="DejaVu Sans", font_scale=1.0)
    output.mkdir(parents=True, exist_ok=True)
    plots = {}
    fig, ax = plt.subplots(figsize=(8.5, 4.7))
    names = ["Доброкачественный", "Злокачественный"]
    values = [summary["class_counts"][name] for name in ("benign", "malignant")]
    bars = ax.bar(names, values, color=["#14a090", "#d88169"], width=0.55)
    for bar, value in zip(bars, values, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 8,
            f"{value} · {value / len(y):.2%}",
            ha="center",
        )
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_ylabel("Число наблюдений")
    ax.set_title(f"Распределение классов · всего {len(y)} наблюдений", pad=16, fontweight="bold")
    fig.tight_layout()
    _save_figure(fig, output / "class_balance.png")
    plots["class_balance"] = output / "class_balance.png"

    chosen = ["mean radius", "mean texture", "mean area", "mean concavity"]
    frame = features.assign(Класс=y.map({0: "Доброкачественный", 1: "Злокачественный"}))
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.2))
    for ax, name in zip(axes.flat, chosen, strict=True):
        sns.histplot(
            data=frame,
            x=name,
            hue="Класс",
            bins=24,
            palette=["#d88169", "#14a090"],
            alpha=0.55,
            ax=ax,
        )
        ax.set_title(f"{name} · среднее {features[name].mean():.3f}", fontsize=11)
        ax.set_xlabel("Значение признака")
        ax.set_ylabel("Число наблюдений")
    fig.suptitle(f"Распределения ключевых признаков · N = {len(y)}", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    _save_figure(fig, output / "distributions.png")
    plots["distributions"] = output / "distributions.png"

    means = [name for name in features.columns if name.startswith("mean ")]
    corr = correlations.loc[means, means].copy()
    corr.index = corr.columns = [name.removeprefix("mean ") for name in means]
    fig, ax = plt.subplots(figsize=(11, 8))
    sns.heatmap(
        corr,
        annot=True,
        fmt=".2f",
        vmin=-1,
        vmax=1,
        center=0,
        cmap="vlag",
        square=True,
        ax=ax,
        annot_kws={"fontsize": 9},
    )
    ax.set_title(
        f"Корреляции средних признаков · max |r| = {summary['maximum_abs_correlation']:.4f}",
        pad=18,
        fontweight="bold",
    )
    fig.tight_layout()
    _save_figure(fig, output / "correlations.png")
    plots["correlations"] = output / "correlations.png"

    top_outliers = (
        feature_stats.sort_values(["iqr_outliers", "feature"], ascending=[False, True])
        .head(10)
        .iloc[::-1]
    )
    fig, ax = plt.subplots(figsize=(10.5, 6.1))
    bars = ax.barh(top_outliers["feature"], top_outliers["iqr_outliers"], color="#647b9e")
    for bar, count in zip(bars, top_outliers["iqr_outliers"], strict=True):
        ax.text(count + 1.4, bar.get_y() + bar.get_height() / 2, str(int(count)), va="center")
    ax.set_xlim(0, int(top_outliers["iqr_outliers"].max()) * 1.15)
    ax.set_xlabel("Значения вне границ IQR; наблюдения не удаляются")
    ax.set_title(
        f"Потенциальные выбросы · {summary['outlier_cells']} ячеек, {summary['outlier_rows']} строк",
        pad=18,
        fontweight="bold",
    )
    fig.tight_layout()
    _save_figure(fig, output / "outliers.png")
    plots["outliers"] = output / "outliers.png"

    fig, (ax, hist) = plt.subplots(
        2, 1, figsize=(10.5, 8.6), gridspec_kw={"height_ratios": [3.2, 1.3]}, sharex=True
    )
    ax.plot([0, 1], [0, 1], linestyle="--", color="#8290a1", label="Идеальная калибровка")
    for model in ("uncalibrated", "calibrated"):
        subset = bins_table.loc[(bins_table["model"] == model) & (bins_table["count"] > 0)]
        score = metrics[model]
        label = f"{MODEL_LABELS[model]} · Brier {score['brier']:.4f} · ECE {score['ece']:.4f}"
        ax.plot(
            subset["mean_probability"],
            subset["observed_fraction"],
            "o-",
            color=COLORS[model],
            label=label,
            linewidth=2,
        )
        for _, row in subset.iterrows():
            ax.annotate(
                str(int(row["count"])),
                (row["mean_probability"], row["observed_fraction"]),
                textcoords="offset points",
                xytext=(5, 7 if model == "uncalibrated" else -14),
                fontsize=8,
                color=COLORS[model],
            )
        all_bins = bins_table.loc[bins_table["model"] == model]
        centers = (all_bins["left"] + all_bins["right"]) / 2
        offset = -0.018 if model == "uncalibrated" else 0.018
        hist.bar(centers + offset, all_bins["count"], width=0.034, color=COLORS[model], alpha=0.9)
    test_count = int(bins_table.loc[bins_table["model"] == "uncalibrated", "count"].sum())
    ax.set_title(
        f"Проблема варианта 25 · диаграмма калибровки\nТест: {test_count} наблюдений · {config.bins} равных интервалов",
        pad=16,
        fontweight="bold",
    )
    ax.set_ylabel("Наблюдаемая доля злокачественных случаев")
    ax.set_ylim(-0.06, 1.09)
    ax.set_xlim(-0.03, 1.03)
    ax.legend(loc="center left", bbox_to_anchor=(0, 0.55), fontsize=9)
    hist.set_xlabel("Предсказанная вероятность злокачественного класса")
    hist.set_ylabel("Число\nнаблюдений")
    hist.set_xticks(np.linspace(0, 1, config.bins + 1))
    fig.text(
        0.5,
        0.015,
        "Числа у точек — размер корзины. Пустые корзины не рисуются. Малые корзины дают нестабильные оценки.",
        ha="center",
        fontsize=9,
        color="#43516a",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    _save_figure(fig, output / "calibration.png")
    plots["calibration"] = output / "calibration.png"
    return plots


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unversioned"


def _code_provenance(root: Path) -> dict:
    selected = sorted(
        [*root.glob("src/**/*.py"), *root.glob("configs/*.yaml")]
        + [
            path
            for name in ("pyproject.toml", "requirements.txt")
            if (path := root / name).exists()
        ]
    )
    digest = hashlib.sha256()
    for path in selected:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    try:
        git = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                "src",
                "configs",
                "pyproject.toml",
                "requirements.txt",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        dirty, status = None, "git_unavailable"
    else:
        dirty = bool(git.stdout.strip()) if git.returncode == 0 else None
        status = ("modified" if dirty else "clean") if git.returncode == 0 else "unversioned"
    return {
        "git_code_dirty": dirty,
        "source_control_status": status,
        "source_sha256": digest.hexdigest(),
        "source_fingerprint_paths": [path.relative_to(root).as_posix() for path in selected],
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _environment() -> dict:
    names = (
        "numpy",
        "pandas",
        "scikit-learn",
        "matplotlib",
        "seaborn",
        "pydantic",
        "mlflow-skinny",
        "PyYAML",
    )
    return {
        "python": platform.python_version(),
        **{name: importlib.metadata.version(name) for name in names},
    }


def run_analysis(config_path="configs/lab1.yaml", output_dir=None, run_name="lab1_eda") -> dict:
    config, root = load_config(config_path)
    output = Path(output_dir).resolve() if output_dir is not None else root / config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    verified = verify_data(config, root)
    features, y = load_data(config, root)
    mlflow.set_tracking_uri((root / config.mlflow_dir).as_uri())
    mlflow.set_experiment(config.experiment_name)
    started = time.perf_counter()
    with mlflow.start_run(run_name=run_name) as run:
        run_id = run.info.run_id
        mlflow.log_params({key: value for key, value in config.model_dump().items()})
        mlflow.set_tags(
            {
                "variant": "25",
                "lab": "1",
                "data_sha256": verified["sha256"],
                "positive_class": "malignant",
                "git_commit": _git_commit(root),
            }
        )
        summary, feature_stats, classes, correlations = summarize_data(features, y, config)
        metrics, bins_table, predictions, ids, splits = calibration_experiment(features, y, config)
        filenames = {
            "feature_summary": "feature_summary.csv",
            "class_balance": "class_balance.csv",
            "correlations": "correlations.csv",
            "calibration_bins": "calibration_bins.csv",
            "calibration_metrics": "calibration_metrics.csv",
            "test_predictions": "test_predictions.csv",
            "split_indices": "split_indices.json",
            "eda_stats": "eda_stats.csv",
            "hash_manifest": "hash_manifest.json",
            "config_snapshot": "config_snapshot.json",
        }
        _write_csv(feature_stats, output / filenames["feature_summary"])
        _write_csv(classes, output / filenames["class_balance"])
        _write_csv(correlations, output / filenames["correlations"], index=True)
        _write_csv(bins_table, output / filenames["calibration_bins"])
        _write_csv(
            pd.DataFrame([{"model": name, **values} for name, values in metrics.items()]),
            output / filenames["calibration_metrics"],
        )
        _write_csv(predictions, output / filenames["test_predictions"])
        _write_json(
            output / filenames["split_indices"],
            {name: values.tolist() for name, values in ids.items()},
        )
        _write_json(output / filenames["config_snapshot"], config.model_dump())
        shutil.copy2(root / config.manifest, output / filenames["hash_manifest"])
        rows = []
        for name, value in summary.items():
            if isinstance(value, (int, float)):
                rows.append({"section": "dataset", "name": "all", "metric": name, "value": value})
        for name in classes["class"]:
            for metric in ("count", "share"):
                value = classes.loc[classes["class"] == name, metric].iloc[0]
                rows.append(
                    {"section": "classes", "name": name, "metric": metric, "value": float(value)}
                )
        for _, row in feature_stats.iterrows():
            for metric in feature_stats.columns:
                if metric not in ("feature", "dtype"):
                    rows.append(
                        {
                            "section": "feature",
                            "name": row["feature"],
                            "metric": metric,
                            "value": float(row[metric]),
                        }
                    )
        for model, values in metrics.items():
            for metric, value in values.items():
                rows.append(
                    {"section": "calibration", "name": model, "metric": metric, "value": value}
                )
        for name in ("train", "calibration", "test"):
            rows.append(
                {"section": "split", "name": name, "metric": "count", "value": splits[name]}
            )
        _write_csv(pd.DataFrame(rows), output / filenames["eda_stats"])
        plots = make_plots(
            features,
            y,
            summary,
            feature_stats,
            correlations,
            metrics,
            bins_table,
            config,
            output / "figures",
        )
        duration = time.perf_counter() - started
        result = {
            "schema_version": 1,
            "variant": 25,
            "lab": 1,
            "author": config.author,
            "seed": config.seed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "git_commit": _git_commit(root),
            **_code_provenance(root),
            "data": verified,
            "summary": summary,
            "splits": splits,
            "metrics": metrics,
            "plots": {name: _relative(path, root) for name, path in plots.items()},
            "files": {
                name: _relative(output / filename, root) for name, filename in filenames.items()
            },
            "method": {
                "positive_class": "malignant",
                "positive_class_raw_sklearn_target": 0,
                "model_target_mapping": {"0": "benign", "1": "malignant"},
                "base_model": "StandardScaler + LogisticRegression",
                "calibration": "sigmoid with FrozenEstimator",
                "split": "stratified train/calibration/test, disjoint row IDs",
                "bins": config.bins,
                "binning": "uniform, [left,right), final bin includes 1",
                "ece_formula": "sum(n_bin/N * abs(mean_probability - observed_fraction))",
                "iqr_multiplier": config.iqr_multiplier,
                "outliers_removed": False,
                "scope": "LAB1 diagnostic illustration; no hyperparameter search or claim of clinical validity",
                "limitations": "One small fixed test split; ECE depends on binning. Brier/log-loss mix calibration and discrimination. Improvement after calibration is not guaranteed.",
            },
            "environment": _environment(),
            "duration_seconds": duration,
            "deterministic_files": {
                filename: sha256_file(output / filename) for filename in filenames.values()
            },
        }
        _write_json(output / "results.json", result)
        mlflow.log_metrics(
            {
                f"{model}_{metric}": value
                for model, values in metrics.items()
                for metric, value in values.items()
            }
        )
        mlflow.log_metrics(
            {key: float(value) for key, value in summary.items() if isinstance(value, (int, float))}
        )
        mlflow.log_metric("duration_seconds", duration)
        for filename in [*filenames.values(), "results.json"]:
            mlflow.log_artifact(str(output / filename), artifact_path="reports")
        for path in plots.values():
            mlflow.log_artifact(str(path), artifact_path="figures")
    return result


def reproduce(config_path="configs/lab1.yaml") -> dict:
    """Repeat twice, compare semantic metrics and all deterministic data artifacts."""
    config, root = load_config(config_path)
    output = root / config.output_dir
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="calibration_repeat_") as folder:
        first = run_analysis(config_path, Path(folder) / "first", "lab1_repeat_a")
        second = run_analysis(config_path, Path(folder) / "second", "lab1_repeat_b")
    checks = {
        name: first["deterministic_files"][name] == value
        for name, value in second["deterministic_files"].items()
    }
    metric_differences = {
        f"{model}.{name}": abs(value - second["metrics"][model][name])
        for model, metrics in first["metrics"].items()
        for name, value in metrics.items()
    }
    result = {
        "status": "совпадает"
        if all(checks.values()) and max(metric_differences.values()) == 0
        else "не совпадает",
        "mode": "exact within the recorded environment, single BLAS thread",
        "first_run_id": first["run_id"],
        "second_run_id": second["run_id"],
        "seed": config.seed,
        "data_sha256": first["data"]["sha256"],
        "files_equal": checks,
        "first_file_hashes": first["deterministic_files"],
        "second_file_hashes": second["deterministic_files"],
        "metric_absolute_differences": metric_differences,
        "max_absolute_difference": max(metric_differences.values()),
        "exclusions": [
            "run_id",
            "generated_at",
            "duration_seconds",
            "output paths",
            "PNG metadata",
        ],
    }
    _write_json(output / "reproducibility.json", result)
    log = [
        "Повторный прогон EDA",
        f"Seed: {config.seed}",
        f"MLflow A: {first['run_id']}",
        f"MLflow B: {second['run_id']}",
    ]
    log.extend(f"{name}: {'совпадает' if equal else 'ОШИБКА'}" for name, equal in checks.items())
    log.extend(
        [
            f"Максимальная разница метрик: {result['max_absolute_difference']}",
            f"Итог: {result['status']}",
        ]
    )
    (output / "reproducibility.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    if result["status"] != "совпадает":
        raise RuntimeError("Повторный прогон не совпал; см. reproducibility.json")
    return result
