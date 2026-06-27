# =============================================================================
# Makefile — common development commands
# =============================================================================
# Usage: make <target>
# =============================================================================

.PHONY: help up down build test lint migrate seed logs shell clean

# ---- Defaults & config ----------------------------------------------------
PYTHON   ?= python
PIP      ?= pip
DOCKER   ?= docker
COMPOSE  ?= docker compose

# ---- Help ------------------------------------------------------------------
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- Docker ----------------------------------------------------------------
up: ## Start the API container (docker compose up)
	$(COMPOSE) up --build -d

down: ## Stop and remove the API container
	$(COMPOSE) down

build: ## Build the Docker image
	$(COMPOSE) build

logs: ## Tail container logs
	$(COMPOSE) logs -f api

shell: ## Open a shell in the running container
	$(COMPOSE) exec api bash

# ---- Python setup ----------------------------------------------------------
venv: ## Create a local virtualenv and install deps
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -r requirements-dev.txt

install: ## Install deps into the current environment
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

# ---- Database --------------------------------------------------------------
migrate: ## Run Alembic migrations (autogenerate + upgrade)
	$(PYTHON) -m alembic upgrade head

migrate-generate: ## Autogenerate a new Alembic migration
	$(PYTHON) -m alembic revision --autogenerate -m "$(msg)"

seed: ## Seed the admin user (requires SEED_ADMIN_* in .env)
	$(PYTHON) -c "import asyncio; from app.db.session import AsyncSessionLocal; from app.db.seed import seed_admin; asyncio.run(seed_admin(AsyncSessionLocal()))"

# ---- Testing ---------------------------------------------------------------
test: ## Run pytest with coverage
	$(PYTHON) -m pytest tests/ -v --cov=app --cov-report=term --cov-report=xml

test-quick: ## Run pytest (no coverage, faster)
	$(PYTHON) -m pytest tests/ -v

# ---- Linting ---------------------------------------------------------------
lint: ## Run ruff check + black --check
	ruff check app/ tests/
	black --check app/ tests/

format: ## Auto-format with black + ruff fix
	black app/ tests/
	ruff check --fix app/ tests/

typecheck: ## Run mypy (non-blocking)
	mypy app/ || true

# ---- Cleanup ---------------------------------------------------------------
clean: ## Remove build artifacts, caches, and the SQLite DB
	rm -rf .venv/ data/ *.db *.db-journal
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/ coverage.xml
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
