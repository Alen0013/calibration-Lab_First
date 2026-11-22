@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo =============================================================
echo  LAB1 - publish to Alen0013/calibration-Lab_First
 echo =============================================================
echo.
where powershell >nul 2>nul
if errorlevel 1 (echo ERROR: PowerShell not found. & pause & exit /b 1)
if not exist "%~dp0publish_new_repo.ps1" (echo ERROR: publish_new_repo.ps1 is missing. & pause & exit /b 1)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_new_repo.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (echo Publication script finished.) else (echo Publication stopped with error %RESULT%.)
pause
exit /b %RESULT%
