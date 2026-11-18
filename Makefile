UV ?= uv
IMAGE ?= ai-dataops:local

.DEFAULT_GOAL := help
.PHONY: help setup lock lint format test test-cov check config version demo snapshot dashboard build run clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-14s %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install all dependencies
	$(UV) sync --all-extras

lock: ## Regenerate uv.lock from pyproject.toml
	$(UV) lock

lint: ## Static checks: ruff + mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy src

format: ## Auto-fix lint issues and format code
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Run the test suite (DuckDB only, no network)
	$(UV) run pytest

test-cov: ## Run tests with coverage and the configured threshold
	$(UV) run pytest --cov --cov-report=term-missing

check: lint test ## Run lint and tests

config: ## Print the resolved configuration
	$(UV) run dataops config

version: ## Print the CLI and package version
	$(UV) run dataops version

demo: ## Generate, backfill and backtest a short labelled demo
	$(UV) run dataops demo --days 45 --volume 1500

snapshot: ## Export the dashboard serving snapshot
	$(UV) run dataops export-serving

dashboard: snapshot ## Run the Streamlit dashboard locally
	$(UV) run streamlit run src/ai_dataops/dashboard/app.py

build: ## Build the container image
	docker build -t $(IMAGE) .

run: ## Container smoke test
	docker run --rm $(IMAGE) dataops version

clean: ## Remove caches and local build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
