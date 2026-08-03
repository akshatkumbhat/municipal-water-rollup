.PHONY: install install-dev test lint format model dashboard operating sourcing smoke clean

install:
	python -m pip install -r requirements.txt

install-dev:
	python -m pip install -r requirements-dev.txt

format:
	ruff format .
	ruff check . --fix

lint:
	ruff check .
	python -m py_compile sourcing_pipeline.py buy_and_build_model.py operations_dashboard.py operations_kpis.py
	mypy sourcing_pipeline.py buy_and_build_model.py operations_dashboard.py sourcing_fixtures.py operations_kpis.py

test:
	pytest

model:
	python buy_and_build_model.py --output-dir outputs/model

dashboard:
	streamlit run operations_dashboard.py

operating:
	python operations_kpis.py --output-dir outputs/operations

sourcing:
	python sourcing_pipeline.py --output outputs/targets.csv --min-targets 50

smoke:
	bash scripts/smoke_test.sh

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__ tests/__pycache__ outputs/model outputs/operations
