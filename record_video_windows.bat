@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Установите Python 3.12 и повторите запуск.
    pause
    exit /b 1
  )
)
set "PATH=%CD%\.venv\Scripts;%PATH%"
if not exist ".venv\lab1_video_installed.ok" (
  .venv\Scripts\python.exe -m pip install -e ".[dev,video]"
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 (
    echo Не удалось установить браузер для записи. Проверьте интернет.
    pause
    exit /b 1
  )
  type nul > ".venv\lab1_installed.ok"
  type nul > ".venv\lab1_video_installed.ok"
)
.venv\Scripts\python.exe scripts\negative_controls.py
if errorlevel 1 (
  echo Предварительная проверка не прошла. Проверьте, установлен ли Git.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\record_demo.py
if errorlevel 1 (
  echo Запись не завершена. Причина указана выше и в журнале artifacts.
) else (
  echo Видео сохранено: artifacts\lab1_demo.webm
)
pause
