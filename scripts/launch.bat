@echo off
REM Starts the AR Aging & Collections app and opens it in your browser.
REM Close this window to stop the app.

setlocal
cd /d "%~dp0.."
set "PROJECT=%CD%"
set "VENV=%PROJECT%\.venv"

echo AR Aging ^& Collections
echo Project: %PROJECT%
echo.

set "PY="
for %%C in (py python) do (
  if not defined PY (
    %%C -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=%%C"
  )
)

if not defined PY (
  echo Python 3.11 or newer is required but was not found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^) and run this again.
  pause
  exit /b 1
)

if not exist "%VENV%" (
  echo First run: setting up ^(this takes a minute^)...
  %PY% -m venv "%VENV%" || (echo Could not create the environment. & pause & exit /b 1)
)

call "%VENV%\Scripts\activate.bat"

REM Install dependencies on first run, or whenever requirements change.
set "STAMP=%VENV%\.requirements-stamp"
if not exist "%STAMP%" (
  echo Installing dependencies...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)
  echo installed> "%STAMP%"
)

REM Streamlit asks for an email on first run and blocks waiting for it, which
REM would hang a double-clicked shortcut. An empty credentials file skips it.
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

echo.
echo Starting... your browser will open at http://localhost:8501
echo Leave this window open while you use the app. Close it to stop.
echo.
python -m streamlit run app.py --server.port 8501 --server.headless false
pause
