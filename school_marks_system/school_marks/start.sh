#!/bin/sh
cd "$(dirname "$0")" || exit 1
echo "Checking required packages..."
python3 -m pip install -q -r requirements.txt || python3 -m pip install -q --user -r requirements.txt || {
  echo "Could not install the packages. Check that Python 3 is installed and the internet works."; exit 1; }
exec python3 app.py
