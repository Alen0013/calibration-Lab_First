@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
echo Публикация ЛР1 в Alen0013/calibration-lab1
echo Вход выполняется через официальный GitHub CLI на вашем компьютере.
echo Пароль или токен в этот файл вставлять не нужно.
echo.
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto try_py3
py -3.12 scripts\publish_github.py
goto finished
:try_py3
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 scripts\publish_github.py
goto finished
:try_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
python scripts\publish_github.py
goto finished
:missing_python
echo Не найден Python версии 3.10 или новее. Установите Python 3.12 и повторите.
set "PUBLISH_RESULT=1"
goto paused
:finished
set "PUBLISH_RESULT=%ERRORLEVEL%"
echo.
if "%PUBLISH_RESULT%"=="0" (echo Публикация завершена. Ссылки и подтверждения находятся в artifacts\github_publication.json.) else (echo Публикация остановлена. Причина указана выше и в artifacts\github_publication.log.)
:paused
echo.
pause
exit /b %PUBLISH_RESULT%
