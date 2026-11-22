"""Record the actual local dashboard on the user's computer with Playwright.

No prerecorded results are substituted. Every demonstration action is a real
button click, acknowledged by the server and checked for a zero exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VIEWPORT = {"width": 1600, "height": 1000}


class RecordingError(RuntimeError):
    """A demonstration failed; its video must not be labelled successful."""


def read_status(url: str, timeout: float = 5) -> dict:
    try:
        with urlopen(url.rstrip("/") + "/api/status", timeout=timeout) as response:
            state = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RecordingError(f"Сервер ответил HTTP {error.code} на /api/status") from error
    except (ValueError, UnicodeDecodeError) as error:
        raise RecordingError(
            "Сервер вернул некорректный JSON вместо состояния приложения"
        ) from error
    if not isinstance(state, dict) or not isinstance(state.get("job"), dict):
        raise RecordingError("По этому адресу работает другой сервер: нет ожидаемого /api/status")
    result = state.get("results") or {}
    if result and (result.get("variant"), result.get("lab")) != (25, 1):
        raise RecordingError("Открыт результат другого варианта или лабораторной работы")
    return state


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("Ожидается локальный адрес вида http://127.0.0.1:8765")
    try:
        port = parsed.port or 80
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Неверный номер порта")
    return value.rstrip("/")


def check_dependencies() -> None:
    missing = [
        name
        for name in ("playwright", "pre_commit", "pytest", "calibration_lab")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise RecordingError(
            f"Не установлены модули: {', '.join(missing)}. "
            'В активном окружении выполните: python -m pip install -e ".[dev,video]"'
        )
    if shutil.which("git") is None:
        raise RecordingError(
            "Для записи трёх блокировок коммита нужен Git в PATH. Установите Git и откройте новый терминал"
        )


def ensure_server(url: str, output: Path):
    """Reuse a running dashboard; otherwise start our own local server."""
    try:
        state = read_status(url)
    except (URLError, TimeoutError, ConnectionError, OSError):
        state = None
    if state is not None:
        if state["job"].get("running"):
            raise RecordingError("В приложении уже выполняется команда. Дождитесь её завершения")
        return None, None
    log_path = output.with_name(output.stem + "_server.log")
    log = log_path.open("w", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "calibration_lab.webapp",
            "--root",
            str(ROOT),
            "--port",
            str(urlparse(url).port or 80),
            "--no-browser",
        ],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log.close()
            raise RecordingError(f"Сервер завершился при запуске; журнал: {log_path}")
        try:
            read_status(url, timeout=1)
            return process, log
        except (URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.3)
    process.terminate()
    process.wait(timeout=10)
    log.close()
    raise RecordingError(f"Сервер не запустился за 30 секунд; журнал: {log_path}")


def stop_own_server(process, log) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if log is not None:
        log.close()


def exact_video_duration(path: Path) -> float | None:
    probe = shutil.which("ffprobe")
    if probe is None:
        return None
    try:
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return float(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class Demo:
    def __init__(self, page, url: str, seconds: int, timeout: int, started: float):
        self.page = page
        self.url = url
        self.seconds = seconds
        self.timeout = timeout
        self.started = started
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def event(self, kind: str, **fields) -> None:
        record = {"at_seconds": round(time.monotonic() - self.started, 3), "kind": kind, **fields}
        self.events.append(record)
        print(
            f"[{record['at_seconds']:6.1f} с] {fields.get('title') or fields.get('action') or kind}",
            flush=True,
        )

    def caption(self, step: str, title: str, body: str) -> None:
        self.page.evaluate("args => window.demoCaption(...args)", [step, title, body])
        self.event("caption", step=step, title=title, body=body)

    def pause_until(self, nominal_seconds: float) -> None:
        deadline = self.started + nominal_seconds / 210 * self.seconds
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(min(500, (deadline - time.monotonic()) * 1000))
        if self.errors:
            raise RecordingError("Ошибка JavaScript в приложении: " + "; ".join(self.errors))
        if time.monotonic() - self.started > 299:
            raise RecordingError(
                "Проверки заняли больше 5 минут. Прогрейте pre-commit перед записью и повторите"
            )

    def view(self, name: str) -> None:
        self.page.locator(f".nav[data-view='{name}']").click()
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('img.chart')].every(i => i.complete && i.naturalWidth > 0)",
            timeout=15000,
        )
        self.event("view", title=name)

    def scroll_to(self, selector: str) -> None:
        self.page.locator(selector).first.scroll_into_view_if_needed()
        self.page.wait_for_timeout(350)

    def action(self, name: str) -> dict:
        self.view("checks")
        button = self.page.locator(f"button[data-action='{name}']")
        button.scroll_into_view_if_needed()
        button.evaluate(
            "el => el.animate([{boxShadow:'0 0 0 0 #4eb49e'},"
            "{boxShadow:'0 0 0 7px #4eb49e88'},{boxShadow:'0 0 0 0 #4eb49e'}],"
            "{duration:900})"
        )
        self.page.wait_for_timeout(450)
        self.event("action_start", action=name)
        with self.page.expect_response(
            lambda response: response.url == self.url + "/api/run"
            and response.request.method == "POST",
            timeout=15000,
        ) as acknowledgment:
            button.click()
        response = acknowledgment.value
        body = response.json()
        if response.status != 202 or body.get("started") != name:
            raise RecordingError(
                f"Приложение не приняло действие {name}: HTTP {response.status}, {body}"
            )
        self.scroll_to("#console")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = read_status(self.url)
            job = state["job"]
            if job.get("action") == name and not job.get("running"):
                log = job.get("log", "")
                self.event("action_end", action=name, exit_code=job.get("exit_code"), log=log)
                if job.get("exit_code") != 0:
                    raise RecordingError(f"Действие {name} завершилось с ошибкой:\n{log[-6000:]}")
                # The dashboard refreshes once a second; wait until the real log is shown.
                self.page.wait_for_function(
                    "() => document.querySelector('#console-status')?.textContent.includes('КОД 0')",
                    timeout=5000,
                )
                self.scroll_to("#console")
                return state
            self.page.wait_for_timeout(400)
        raise RecordingError(f"Действие {name} не завершилось за {self.timeout} секунд")

    def run(self) -> dict:
        self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_function("() => typeof window.demoCaption === 'function'", timeout=10000)
        self.page.locator(".nav[data-view='overview']").wait_for()
        self.caption(
            "01",
            "ЛР1 · Вариант 25 · Калибровка",
            "Записывается работа локального приложения. Все проверки запускаются настоящими кнопками.",
        )
        self.pause_until(8)
        self.caption(
            "02",
            "Запускаем анализ",
            "Приложение читает проверенные данные, строит графики и сохраняет новый MLflow run.",
        )
        measured = self.action("eda")
        self.view("overview")
        self.pause_until(25)

        result = measured["results"]
        summary = result["summary"]
        self.caption(
            "03",
            "Паспорт и качество данных",
            f"Наблюдений: {summary['rows']}. Признаков: {summary['features']}. Пропуски: {summary['missing_values']}. Дубликаты: {summary['duplicate_rows']}.",
        )
        self.view("data")
        self.pause_until(39)
        self.scroll_to(".data-grid")
        self.caption(
            "04",
            "Связи и распределения",
            f"Вне границ IQR: {summary['outlier_cells']} ячеек в {summary['outlier_rows']} строках. Эти наблюдения не удаляются.",
        )
        self.pause_until(54)

        self.view("calibration")
        self.caption(
            "05",
            "Диаграмма калибровки",
            "По горизонтали — вероятность модели, по вертикали — доля события. Диагональ означает согласованность вероятностей.",
        )
        self.pause_until(73)
        self.scroll_to(".grid")
        before = result["metrics"]["uncalibrated"]["brier"]
        after = result["metrics"]["calibrated"]["brier"]
        change = "снизился" if after < before else "не снизился"
        self.caption(
            "06",
            "Измеренное качество",
            f"Brier {change}: {before:.4f} → {after:.4f}. На одном test калибровка не обязана улучшать результат. Test не используется для обучения.",
        )
        self.pause_until(93)

        self.caption(
            "07",
            "Проверяем исходный SHA-256",
            "Проверка использует закреплённый хеш официального файла, а не переписывает манифест.",
        )
        self.action("verify")
        self.pause_until(108)
        self.caption(
            "08",
            "Негативный контроль данных",
            "В изолированной копии меняется один байт. Проверка обязана обнаружить несовпадение; оригинал остаётся целым.",
        )
        self.action("tamper")
        self.pause_until(125)

        self.caption(
            "09",
            "Три блокировки коммитов",
            "Настоящие попытки коммита: неверный стиль, фиктивный секрет и большой бинарный файл. Успех проверки означает три отказа.",
        )
        self.action("controls")
        self.pause_until(153)
        self.caption(
            "10",
            "Повторяем эксперимент",
            "Два новых прогона с одним seed. Сравниваются CSV, разбиения и метрики; MLflow записывает оба запуска.",
        )
        self.action("reproduce")
        self.pause_until(179)
        self.caption(
            "11",
            "Тесты программы",
            "Проверяются вычисления, схема настроек, целостность данных и защитные сценарии. Результат берётся из настоящего pytest.",
        )
        self.action("tests")
        self.pause_until(197)

        self.view("files")
        self.caption(
            "12",
            "Артефакты для сдачи",
            "Отчёт, таблицы, графики, хеши и журналы доступны в проекте. Состояние GitHub подтверждается отдельно после публикации.",
        )
        # Retain a readable final frame even if slower checks consumed their planned pause.
        end = max(self.started + self.seconds, time.monotonic() + 8)
        if end - self.started > 299:
            raise RecordingError(
                "Не удалось вместить все проверки в 5 минут. Повторите после прогрева окружения"
            )
        while time.monotonic() < end:
            self.page.wait_for_timeout(400)
        final_state = read_status(self.url)
        if self.errors:
            raise RecordingError(
                "В приложении возникла ошибка JavaScript: " + "; ".join(self.errors)
            )
        return {
            "run_id": final_state.get("results", {}).get("run_id"),
            "git_commit": final_state.get("results", {}).get("git_commit"),
            "data_sha256": final_state.get("results", {}).get("data", {}).get("sha256"),
        }


def record(args) -> Path:
    check_dependencies()
    # Import only on actual recording, so --help works without the optional package.
    from playwright.sync_api import sync_playwright

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    process, server_log = ensure_server(args.url, output)
    manifest = {
        "status": "recording",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "viewport": VIEWPORT,
        "target_seconds": args.seconds,
        "format": "WebM",
        "audio": False,
        "headless": args.headless,
        "action_source": "real dashboard button clicks; server acknowledgment and exit code checked",
    }
    final_path = output
    error = None
    demo = None
    wrote_video = False
    try:
        with tempfile.TemporaryDirectory(prefix="lab1_video_") as temporary:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=args.headless)
                context = browser.new_context(
                    viewport=VIEWPORT,
                    record_video_dir=temporary,
                    record_video_size=VIEWPORT,
                    locale="ru-RU",
                    device_scale_factor=1,
                )
                started = time.monotonic()
                page = context.new_page()
                video = page.video
                demo = Demo(page, args.url, args.seconds, args.step_timeout, started)
                try:
                    manifest.update(demo.run())
                    manifest["status"] = "success"
                except Exception as exception:
                    error = exception
                    manifest.update(status="failed", error=str(exception))
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    final_path = output.with_name(f"{output.stem}.failed_{stamp}.webm")
                finally:
                    manifest["wall_clock_seconds"] = round(time.monotonic() - started, 3)
                    manifest["events"] = demo.events
                    # Closing the context is required for Playwright to finalize WebM.
                    context.close()
                try:
                    if video is None:
                        raise RecordingError("Playwright не создал объект видеозаписи")
                    captured = Path(video.path())
                    if not captured.is_file() or captured.stat().st_size == 0:
                        raise RecordingError("Видеофайл отсутствует или пуст")
                    duration = exact_video_duration(captured)
                    manifest["duration_seconds"] = duration
                    manifest["duration_measurement"] = (
                        "ffprobe" if duration is not None else "wall_clock_estimate_only"
                    )
                    if duration is not None and error is None and not 180 <= duration <= 300:
                        error = RecordingError(
                            f"Измеренная длительность {duration:.1f} с не соответствует 3–5 минутам"
                        )
                        manifest.update(status="failed", error=str(error))
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        final_path = output.with_name(f"{output.stem}.failed_{stamp}.webm")
                    provisional = output.with_name(output.name + ".part")
                    shutil.copy2(captured, provisional)
                    os.replace(provisional, final_path)
                    wrote_video = True
                finally:
                    browser.close()
    except Exception as exception:
        error = exception
        manifest.update(status="failed", error=str(exception))
    finally:
        stop_own_server(process, server_log)
    if wrote_video and error is not None and final_path == output:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_path = output.with_name(f"{output.stem}.failed_{stamp}.webm")
        output.replace(failed_path)
        final_path = failed_path
    if wrote_video and final_path.exists():
        manifest["file"] = str(final_path)
        manifest["size_bytes"] = final_path.stat().st_size
        manifest["sha256"] = hashlib.sha256(final_path.read_bytes()).hexdigest()
    manifest_path = output.with_name(output.stem + "_recording.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if error is not None:
        raise RecordingError(
            f"Запись не завершена успешно: {error}\n"
            f"Диагностика: {manifest_path}\n"
            "При отсутствии браузера выполните: python -m playwright install chromium"
        ) from error
    return final_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Записать настоящую работу приложения ЛР1 с титрами на 3–5 минут"
    )
    parser.add_argument("--url", type=validate_url, default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "lab1_demo.webm")
    parser.add_argument(
        "--seconds",
        type=int,
        default=210,
        help="Целевая длительность 180–290 секунд; по умолчанию 210",
    )
    parser.add_argument(
        "--step-timeout",
        type=int,
        default=120,
        help="Максимальное ожидание одного действия, секунд",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Записывать Chromium без видимого окна"
    )
    args = parser.parse_args(argv)
    if not 180 <= args.seconds <= 290:
        parser.error("--seconds должен быть от 180 до 290; оставлен запас на закрытие видео")
    if args.output.suffix.lower() != ".webm":
        parser.error("Playwright записывает WebM; укажите --output с расширением .webm")
    if not 15 <= args.step_timeout <= 180:
        parser.error("--step-timeout должен быть от 15 до 180")
    try:
        output = record(args)
    except (RecordingError, KeyboardInterrupt) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return 1
    print(f"Готово: {output}\nПросмотрите запись перед сдачей; звук не записывается.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
