#!/usr/bin/env bash
set -euo pipefail

python -m py_compile sourcing_pipeline.py buy_and_build_model.py operations_dashboard.py
python buy_and_build_model.py --output-dir outputs/model >/tmp/project-copperline-model.log
pytest -q

echo "Smoke test passed."
echo "Model output: outputs/model/five_year_pro_forma.csv"
