"""Command-line interface; failures have explanatory messages and nonzero exit codes."""

import argparse
import json
import sys

from calibration_lab.config import load_config
from calibration_lab.data import prepare_data, tamper_control, verify_data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ЛР1 · вариант 25 · калибровка вероятностей")
    parser.add_argument("--config", default="configs/lab1.yaml", help="Путь к YAML-конфигурации")
    sub = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("prepare", "Получить официальные данные и проверить закреплённый хеш"),
        ("verify", "Проверить целостность данных без перезаписи манифеста"),
        ("eda", "Выполнить EDA, построить графики и записать MLflow-прогон"),
        ("run", "То же, что eda"),
        ("reproduce", "Повторить EDA дважды и сравнить результаты"),
        ("tamper", "Проверить обнаружение подмены байта на изолированной копии"),
    ):
        item = sub.add_parser(command, help=help_text)
        item.add_argument("--config", default=argparse.SUPPRESS)
        if command == "prepare":
            item.add_argument(
                "--download",
                action="store_true",
                help="Скачать CSV по HTTPS вместо копии из scikit-learn",
            )
    args = parser.parse_args(argv)
    try:
        config, root = load_config(args.config)
        if args.command == "prepare":
            result = prepare_data(config, root, download=args.download)
        elif args.command == "verify":
            result = verify_data(config, root)
        elif args.command == "tamper":
            result = tamper_control(config, root)
        else:
            from calibration_lab.analysis import reproduce, run_analysis

            result = (
                reproduce(args.config) if args.command == "reproduce" else run_analysis(args.config)
            )
        if args.command in ("eda", "run"):
            summary = result["summary"]
            print(
                f"PASS: EDA завершён. Наблюдений: {summary['rows']}; признаков: {summary['features']}"
            )
            print(f"Пропуски: {summary['missing_values']}; дубликаты: {summary['duplicate_rows']}")
            print(f"MLflow run_id: {result['run_id']}")
            print(f"SHA-256: {result['data']['sha256']}")
            for name, metrics in result["metrics"].items():
                print(
                    f"{name}: Brier={metrics['brier']:.8f}; ECE={metrics['ece']:.8f}; log_loss={metrics['log_loss']:.8f}"
                )
            print(f"Артефакты: {config.output_dir}/results.json")
        elif args.command == "reproduce":
            print(
                f"PASS: повторные прогоны {result['status']}; max |delta| = {result['max_absolute_difference']}"
            )
            print(f"MLflow A: {result['first_run_id']}; MLflow B: {result['second_run_id']}")
            for name, equal in result["files_equal"].items():
                print(f"{'PASS' if equal else 'FAIL'}: {name}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
