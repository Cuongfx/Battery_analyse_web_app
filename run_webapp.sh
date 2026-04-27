#!/usr/bin/env sh
# Run the BatteryML PKL web viewer from the project root:
#   ./run_webapp.sh
# Then open http://127.0.0.1:8765
cd "$(dirname "$0")" || exit 1
export PYTHONPATH=".:${PYTHONPATH:-}"
exec python3 -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
