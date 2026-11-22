@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Создание окружения Python 3.12...
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Установите Python 3.12 и повторите запуск.
    pause
    exit /b 1
  )
)
if not exist ".venv\lab1_installed.ok" (
  .venv\Scripts\python.exe -m pip install -e ".[dev]"
  if errorlevel 1 (
    echo Установка не завершена. Проверьте интернет и повторите запуск файла.
    pause
    exit /b 1
  )
  type nul > ".venv\lab1_installed.ok"
)
set "PATH=%CD%\.venv\Scripts;%PATH%"
.venv\Scripts\python.exe -m calibration_lab.webapp
pause

