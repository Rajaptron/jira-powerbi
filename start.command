#!/bin/bash
# Double-click (macOS) or run ./start.command — sets up on first run, then opens the dashboard.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (one-time)..."
  python3 -m venv .venv || { echo "Setup failed. Install Python 3 from python.org and retry."; read -r; exit 1; }
fi
# Install / update add-ons whenever requirements.txt changes (e.g. after copying in a new version).
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing add-ons..."
  .venv/bin/pip install -q -r requirements.txt && cp requirements.txt .venv/requirements.installed \
    || echo "Couldn't install add-ons (no internet?). The app still runs; PDF upload may not work."
fi
exec .venv/bin/python -m app.server "$@"
