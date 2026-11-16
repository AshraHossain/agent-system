.PHONY: check check-all check-subprojects lint format test test-unit test-integration eval demo build-data clean

check: lint test eval

check-all: check check-subprojects

check-subprojects:
	cd platewatch && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
	cd traffic-flow-analyzer && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
	cd ledger-hound && uv run ruff check . && uv run ruff format --check . && uv run --extra dspy pytest -q
	cd api-tests && uv run ruff check . && uv run ruff format --check . && uv run pytest -m smoke -x -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	OPSPILOT_MODEL_PROVIDER=mock uv run pytest --cov=opspilot --cov-report=term-missing --cov-fail-under=80

test-unit:
	OPSPILOT_MODEL_PROVIDER=mock uv run pytest -m unit

test-integration:
	OPSPILOT_MODEL_PROVIDER=mock uv run pytest -m integration

eval:
	OPSPILOT_MODEL_PROVIDER=mock uv run opspilot eval --guardrails --out var/eval

demo:
	OPSPILOT_MODEL_PROVIDER=mock uv run opspilot demo --case C03 --no-persist

build-data:
	uv run opspilot build-data

clean:
	rm -rf .pytest_cache .coverage var/eval var/htmlcov coverage.xml
