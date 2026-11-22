"""Capture real CLI output and render a labelled, readable video replay.

This is a terminal-output replay with explanatory charts, not a browser screen
recording. The complete timestamped command output remains in capture JSON.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "LAB1"
WIDTH, HEIGHT, FPS = 1920, 1080, 5
NAVY, MUTED, TEAL = "#153348", "#a3becb", "#7fdfc0"


def font(name, size):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.truetype("arial.ttf" if "Mono" not in name else "consola.ttf", size)


def capture(args: list[str]) -> dict:
    started = time.monotonic()
    events = []
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", PRE_COMMIT_COLOR="never")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    command = [sys.executable if args[0] == "python" else args[0], *args[1:]]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line.rstrip())
        events.append({"t": round(time.monotonic() - started, 4), "text": clean})
    code = process.wait()
    result = {
        "command": " ".join(args),
        "events": events,
        "duration_seconds": round(time.monotonic() - started, 4),
        "exit_code": code,
    }
    if code:
        raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"CAPTURE PASS: {' '.join(args[:4])}", flush=True)
    return result


def record(output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Установите FFmpeg или используйте готовый MP4 из комплекта")
    output.parent.mkdir(parents=True, exist_ok=True)
    scenes = [
        {
            "title": "ЛР1 · Вариант 25 · Калибровка",
            "note": "Работающая командная версия: данные, анализ, контроль изменений и повтор результата.",
            "seconds": 16,
            "run": ["python", "-m", "calibration_lab.cli", "--help"],
        },
        {
            "title": "Проверяем происхождение и целостность данных",
            "note": "Фиксированный SHA-256 сравнивается с исходным CSV. Эталон не пересчитывается из входа.",
            "seconds": 20,
            "run": ["python", "-m", "calibration_lab.cli", "verify"],
        },
        {
            "title": "Запускаем анализ и регистрируем эксперимент",
            "note": "Реальный EDA создаёт таблицы, графики, метрики и MLflow run ID для отчёта.",
            "seconds": 22,
            "run": ["python", "-m", "calibration_lab.cli", "eda"],
        },
        {
            "title": "Исследуем данные до моделирования",
            "note": "В датасете 569 наблюдений и 30 признаков. Классы учитываются при стратификации.",
            "seconds": 16,
            "image": "class_balance.png",
        },
        {
            "title": "Проблема варианта — качество вероятностей",
            "note": "На фиксированном test калибровка ухудшила Brier и ECE. Этот результат сохранён без подбора seed.",
            "seconds": 26,
            "image": "calibration.png",
        },
        {
            "title": "Негативный контроль — подмена данных",
            "note": "Меняем один байт в изолированной копии. Валидатор отклоняет её; исходный файл цел.",
            "seconds": 22,
            "run": ["python", "-m", "calibration_lab.cli", "tamper"],
        },
        {
            "title": "Доказываем защиту коммитов",
            "note": "Реальные git commit блокируются для нарушения стиля, фиктивного секрета и большого файла.",
            "seconds": 27,
            "run": ["python", "scripts/negative_controls.py"],
            "show_log": "precommit_block.log",
        },
        {
            "title": "Повторяем эксперимент с тем же seed",
            "note": "Два запуска сравнивают метрики и десять детерминированных файлов. Различие метрик равно нулю.",
            "seconds": 24,
            "run": ["python", "-m", "calibration_lab.cli", "reproduce"],
        },
        {
            "title": "Проверяем код автоматическими тестами",
            "note": "Проверяются данные, настройки, разбиение, калибровочные интервалы и защитные хуки.",
            "seconds": 18,
            "run": ["python", "-m", "pytest", "-q", "-o", "addopts="],
        },
        {
            "title": "Фиксируем этапы работы в отдельных ветках",
            "note": "Локальная история готова. Удалённые GitHub CI, PR и защита main требуют публикации.",
            "seconds": 19,
            "run": ["git", "log", "--oneline", "--graph", "--all", "-12"],
        },
    ]
    for scene in scenes:
        if "run" in scene:
            scene["capture"] = capture(scene["run"])
        if "show_log" in scene:
            scene["full_log"] = (REPORT / scene["show_log"]).read_text(encoding="utf-8")
    total = sum(scene["seconds"] for scene in scenes)
    result = json.loads((REPORT / "results.json").read_text(encoding="utf-8"))
    manifest = {
        "kind": "captured_cli_output_replay_with_actual_generated_charts",
        "browser_screen_recording": False,
        "duration_seconds": total,
        "fps": FPS,
        "resolution": [WIDTH, HEIGHT],
        "timing_note": "Command output was captured live. Reading pauses are expanded in playback.",
        "run_id": result["run_id"],
        "git_commit": result["git_commit"],
        "scenes": scenes,
    }
    (REPORT / "video_capture.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fonts = {
        "title": font("DejaVuSans-Bold.ttf", 38),
        "body": font("DejaVuSans.ttf", 24),
        "small": font("DejaVuSans.ttf", 19),
        "mono": font("DejaVuSansMono.ttf", 24),
        "label": font("DejaVuSans-Bold.ttf", 20),
    }
    process = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-framerate",
            str(FPS),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        stdin=subprocess.PIPE,
    )
    assert process.stdin is not None
    elapsed = 0.0
    try:
        for index, scene in enumerate(scenes, 1):
            chart_image = None
            if "image" in scene:
                chart_image = Image.open(REPORT / "figures" / scene["image"]).convert("RGB")
                chart_image.thumbnail((1690, 725), Image.Resampling.LANCZOS)
            for frame in range(scene["seconds"] * FPS):
                t = frame / FPS
                canvas = Image.new("RGB", (WIDTH, HEIGHT), "#f2f6f9")
                draw = ImageDraw.Draw(canvas)
                draw.rectangle((0, 0, WIDTH, 160), fill=NAVY)
                draw.text(
                    (76, 24),
                    "CALIBRATION LAB  /  РЕАЛЬНЫЕ CLI ЗАПУСКИ",
                    font=fonts["label"],
                    fill=TEAL,
                )
                draw.text((76, 72), scene["title"], font=fonts["title"], fill="white")
                draw.rounded_rectangle(
                    (72, 192, 1848, 940), radius=18, fill="white" if chart_image else "#102635"
                )
                if chart_image:
                    canvas.paste(
                        chart_image,
                        ((WIDTH - chart_image.width) // 2, 205 + (710 - chart_image.height) // 2),
                    )
                else:
                    cap = scene["capture"]
                    draw.text((100, 211), "$ " + cap["command"], font=fonts["mono"], fill=TEAL)
                    draw.line((100, 259, 1820, 259), fill="#315568", width=2)
                    playback_t = (
                        t * max(cap["duration_seconds"], 1) / max(scene["seconds"] * 0.5, 1)
                    )
                    lines = []
                    for event in cap["events"]:
                        if event["t"] <= playback_t:
                            lines.extend(
                                textwrap.wrap(event["text"], width=116, replace_whitespace=False)
                                or [""]
                            )
                    if t > scene["seconds"] * 0.55:
                        lines.extend(["", f"Код завершения: {cap['exit_code']}"])
                    if "full_log" in scene and t > scene["seconds"] * 0.58:
                        raw = scene["full_log"].splitlines()
                        fraction = (t / scene["seconds"] - 0.58) / 0.42
                        end = min(len(raw), 23 + int(fraction * max(0, len(raw) - 23)))
                        lines = []
                        for line in raw[max(0, end - 23) : end]:
                            lines.extend(
                                textwrap.wrap(line, width=116, replace_whitespace=False) or [""]
                            )
                    for row, line in enumerate(lines[-23:]):
                        color = (
                            TEAL
                            if any(
                                x in line
                                for x in ("PASS", "true", "passed", "совпадает", "RESULT:")
                            )
                            else "#d6e6ed"
                        )
                        draw.text((100, 278 + row * 27), line, font=fonts["mono"], fill=color)
                draw.text((78, 960), scene["note"], font=fonts["body"], fill=NAVY)
                draw.text(
                    (78, 1010),
                    "Запись вывода выполненных команд · чтение результатов дополнено паузами",
                    font=fonts["small"],
                    fill="#697f8d",
                )
                current = elapsed + t
                draw.text(
                    (1570, 1010),
                    f"{int(current) // 60:02d}:{int(current) % 60:02d} / {total // 60:02d}:{total % 60:02d}    {index}/{len(scenes)}",
                    font=fonts["small"],
                    fill="#697f8d",
                )
                draw.rectangle((0, 1069, WIDTH, 1080), fill="#d8e5ea")
                draw.rectangle((0, 1069, int(WIDTH * current / total), 1080), fill="#0a8c7e")
                buffer = io.BytesIO()
                canvas.save(buffer, format="PNG", compress_level=1)
                process.stdin.write(buffer.getvalue())
            elapsed += scene["seconds"]
            print(f"VIDEO: scene {index}/{len(scenes)}", flush=True)
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("FFmpeg не завершил запись")
    print(f"SAVED: {output} ({total} seconds)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "lab1_demo_cli.mp4")
    record(parser.parse_args().output.resolve())
