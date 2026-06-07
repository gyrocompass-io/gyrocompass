.PHONY: install dev test lint format check run-api run-web run-mcp clean

# ── Setup ──────────────────────────────────────────────────────────────────────

install:
	uv sync --all-extras

dev:
	uv sync --all-extras --dev

# ── Testing ────────────────────────────────────────────────────────────────────

test:
	uv run pytest tests/ -v

test-fast:
	uv run pytest tests/ -v -x --ignore=tests/integration

# ── Code Quality ───────────────────────────────────────────────────────────────

lint:
	uv run ruff check gyrocompass/ api/

format:
	uv run ruff format gyrocompass/ api/

check: lint
	uv run ruff check --no-fix gyrocompass/ api/

# ── Running Services ───────────────────────────────────────────────────────────

run-api:
	uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 7700

run-web:
	cd web && npm run dev

run-mcp:
	uv run gyro mcp start

# ── Docker ─────────────────────────────────────────────────────────────────────

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-build:
	docker-compose build

# ── Development Shortcuts ──────────────────────────────────────────────────────

analyze:
	uv run gyro analyze --repo . --save

drift:
	uv run gyro drift --repo .

status:
	uv run gyro status

# ── Clean ──────────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	rm -f gyrocompass.db
