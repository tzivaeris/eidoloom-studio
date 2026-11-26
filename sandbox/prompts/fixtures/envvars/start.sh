#!/bin/bash
# Starts the app with a number of workers.
WORKERS="${WORKERS:-4}"
export PYTHONUNBUFFERED=1
echo "starting with $WORKERS workers"
python3 app.py
