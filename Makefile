.PHONY: dev down reset logs setup status migrate test test-unit test-integration lint typecheck build db-only
dev:              ## Start full stack (TB + PG + API)
	docker compose up -d --build
down:             ## Stop containers, keep data
	docker compose down
reset:            ## Stop and DELETE all data
	docker compose down -v
logs:             ## Follow API logs
	docker compose logs -f cbs-api
setup:            ## Bootstrap bank
	docker compose run --rm cbs-api cbs setup init --currency USD --currency EUR --product-file /app/products.example.yaml
status:           ## Check setup status
	docker compose run --rm cbs-api cbs setup status
migrate:          ## Run pending migrations
	docker compose run --rm cbs-api cbs migrate up
test:             ## Run all tests
	pytest
test-unit:        ## Unit tests
	pytest tests/unit
test-integration: ## Integration tests
	pytest tests/integration
lint:             ## Run linter
	ruff check src/ tests/
	ruff format --check src/ tests/
typecheck:        ## Run type checker
	mypy src/
build:            ## Build wheel
	python -m build
db-only:          ## Start only databases
	docker compose up -d tigerbeetle postgres
