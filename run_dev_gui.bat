@echo off
setlocal

cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Project virtual environment was not found:
    echo %PYTHON%
    echo.
    echo Run the environment setup first, then double-click this script again.
    pause
    exit /b 1
)

title auto-bdsp-rng development GUI
"%PYTHON%" -m auto_bdsp_rng gui
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%" == "0" (
    echo.
    echo The development GUI exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
