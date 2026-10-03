#!/usr/bin/env sh
# Starts mcIRC.  Needs Python 3 with Tk (Linux: sudo apt install python3-tk) and: pip install meshcore-cli pyserial
cd "$(dirname "$0")" || exit 1
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
exec "$PY" mcIRC.py "$@"
