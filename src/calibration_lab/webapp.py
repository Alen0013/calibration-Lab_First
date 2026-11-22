"""Local dashboard for the real CLI and its persisted evidence."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

WEB = Path(__file__).parent / "web"
ROOT = Path.cwd()
LOCK = threading.Lock()
JOB: dict = {"running": False, "action": None, "log": "", "exit_code": None}
ACTIONS = {
    "eda": ["-m", "calibration_lab.cli", "eda", "--config", "configs/lab1.yaml"],
    "verify": ["-m", "calibration_lab.cli", "verify", "--config", "configs/lab1.yaml"],
    "reproduce": ["-m", "calibration_lab.cli", "reproduce", "--config", "configs/lab1.yaml"],
    "tamper": ["-m", "calibration_lab.cli", "tamper", "--config", "configs/lab1.yaml"],
    "controls": ["scripts/negative_controls.py"],
    "tests": ["-m", "pytest", "-q"],
}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def git_info() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=3
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {
        "branch": run("branch", "--show-current"),
        "commit": run("rev-parse", "--short", "HEAD"),
        "branches": run("branch", "--format=%(refname:short)").splitlines(),
        "graph": run("log", "--oneline", "--graph", "--all", "-16"),
    }


def execute(action: str) -> None:
    global JOB
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    command = [sys.executable, "-u", *ACTIONS[action]]
    display = "python " + " ".join(ACTIONS[action])
    with LOCK:
        JOB["log"] = f"$ {display}\n\n"
    try:
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
            with LOCK:
                JOB["log"] = (JOB["log"] + line)[-120000:]
        code = process.wait()
    except OSError as error:
        code = 1
        with LOCK:
            JOB["log"] += f"Ошибка запуска: {error}\n"
    with LOCK:
        elapsed = time.monotonic() - started
        JOB["log"] += f"\nКод завершения: {code}. Время: {elapsed:.1f} с.\n"
        JOB.update(running=False, exit_code=code, elapsed_seconds=round(elapsed, 2))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, message: str, *args: object) -> None:
        if "/api/status" not in message % args:
            super().log_message(message, *args)

    def send_json(self, value: dict, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/status":
            with LOCK:
                job = dict(JOB)
            report = ROOT / "reports" / "LAB1"
            files = sorted(p.relative_to(ROOT).as_posix() for p in report.glob("*") if p.is_file())
            self.send_json(
                {
                    "results": read_json(report / "results.json"),
                    "job": job,
                    "git": git_info(),
                    "files": files,
                    "checks": read_json(report / "verification_summary.json"),
                }
            )
            return
        if path.startswith("/reports/") or path.startswith("/docs/"):
            target = (ROOT / path.lstrip("/")).resolve()
            bases = [(ROOT / "reports").resolve(), (ROOT / "docs").resolve()]
        else:
            target = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            bases = [WEB.resolve()]
        if not any(target.is_relative_to(base) for base in bases) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".md", ".log", ".txt", ".yaml", ".csv"}:
            mime = "text/plain"
        if mime.startswith("text/") or mime in {"application/json", "application/javascript"}:
            mime += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_json({"error": "Неизвестный адрес"}, 404)
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }:
            self.send_json({"error": "Разрешён только локальный интерфейс"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Неверный размер запроса")
            action = json.loads(self.rfile.read(size)).get("action")
            if action not in ACTIONS:
                raise ValueError("Неизвестное действие")
        except (ValueError, AttributeError, TypeError):
            self.send_json({"error": "Неверное действие или формат запроса"}, 400)
            return
        with LOCK:
            if JOB["running"]:
                self.send_json({"error": "Дождитесь завершения текущей проверки"}, 409)
                return
            JOB.update(running=True, action=action, log="Запуск…\n", exit_code=None)
        threading.Thread(target=execute, args=(action,), daemon=True).start()
        self.send_json({"started": action}, 202)


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser(description="Локальное приложение ЛР1 Калибровка")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    ROOT = args.root.resolve()
    if not (ROOT / "configs" / "lab1.yaml").exists():
        parser.error("Запустите команду из корня проекта или укажите --root")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"ЛР1 Калибровка · {url}\nДля остановки нажмите Ctrl+C.", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
