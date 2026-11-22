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
  .venv\Scripts\python.exe -m pip install -e ".[dev]"
  if errorlevel 1 (
    echo Установка не завершена. Проверьте подключение к интернету.
    pause
    exit /b 1
  )
)
set "PATH=%CD%\.venv\Scripts;%PATH%"
.venv\Scripts\python.exe -m calibration_lab.webapp
pause

