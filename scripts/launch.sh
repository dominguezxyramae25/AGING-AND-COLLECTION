#!/usr/bin/env bash
# Starts the AR Aging & Collections app and opens it in your browser.
# Close this window (or press Ctrl-C) to stop the app.

set -u
cd "$(dirname "$0")/.." || exit 1
PROJECT="$(pwd)"
VENV="$PROJECT/.venv"

echo "AR Aging & Collections"
echo "Project: $PROJECT"
echo

PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is required but was not found."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Return to close..." _ ; exit 1
fi

# First run sets up an isolated environment so nothing else on the machine changes.
if [ ! -d "$VENV" ]; then
  echo "First run: setting up (this takes a minute)..."
  "$PY" -m venv "$VENV" || { echo "Could not create the environment."; read -r -p "Press Return..." _; exit 1; }
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Install or update dependencies only when requirements have changed.
STAMP="$VENV/.requirements-stamp"
CURRENT="$(cksum requirements.txt 2>/dev/null | awk '{print $1}')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$CURRENT" ]; then
  echo "Installing dependencies..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt || {
    echo "Dependency install failed."; read -r -p "Press Return..." _; exit 1; }
  echo "$CURRENT" > "$STAMP"
fi

# Streamlit asks for an email on first run and blocks waiting for it, which would
# hang a double-clicked shortcut. An empty credentials file skips the prompt.
CRED_DIR="${HOME}/.streamlit"
if [ ! -f "$CRED_DIR/credentials.toml" ]; then
  mkdir -p "$CRED_DIR"
  printf '[general]\nemail = ""\n' > "$CRED_DIR/credentials.toml"
fi

echo
echo "Starting… your browser will open at http://localhost:8501"
echo "Leave this window open while you use the app. Close it to stop."
echo
exec python -m streamlit run app.py --server.port 8501 --server.headless false
