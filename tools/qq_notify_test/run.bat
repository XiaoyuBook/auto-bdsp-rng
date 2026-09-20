@echo off
setlocal
cd /d "%~dp0"
set "QQ_TEST_PYTHON=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%QQ_TEST_PYTHON%" set "QQ_TEST_PYTHON=python"
"%QQ_TEST_PYTHON%" app.py
set "QQ_TEST_EXIT=%ERRORLEVEL%"
if not "%QQ_TEST_EXIT%" == "0" (
    echo.
    echo Launch failed. Install dependencies with: python -m pip install -r requirements.txt
    pause
)
exit /b %QQ_TEST_EXIT%
