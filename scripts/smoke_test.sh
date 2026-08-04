#!/usr/bin/env bash
set -euo pipefail

python -m py_compile sourcing_pipeline.py buy_and_build_model.py operations_dashboard.py operations_kpis.py candidate_package.py
python buy_and_build_model.py --output-dir outputs/model >/tmp/project-copperline-model.log
python operations_kpis.py --output-dir outputs/operations >/tmp/project-copperline-operations.log
python candidate_package.py --output-dir outputs/candidate_package >/tmp/project-copperline-package.log
python candidate_package.py --verify outputs/candidate_package/MANIFEST.json
pytest -q

echo "Smoke test passed."
echo "Model output: outputs/model/base/five_year_pro_forma.csv"
echo "Scenario comparison: outputs/model/scenario_comparison.csv"
echo "Operating KPIs: outputs/operations/kpi_summary.csv"
echo "Candidate package: outputs/candidate_package/IC_SUMMARY.md"
