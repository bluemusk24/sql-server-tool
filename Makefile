# SQL Server Diagnostic Assistant — developer commands
#
# Run `make` (or `make help`) to list every command with a description.
# The headline entry point is `make run`, which starts the whole stack.

.DEFAULT_GOAL := help

.PHONY: help run backend frontend setup install test \
        backend-install backend-test backend-lint backend-format \
        frontend-install frontend-test frontend-typecheck frontend-build \
        token clean

# ── Configuration ────────────────────────────────────────────────────
BACKEND_DIR   := backend
FRONTEND_DIR  := frontend
BACKEND_PORT  := 8000
FRONTEND_PORT := 5173
BACKEND_URL   := http://127.0.0.1:$(BACKEND_PORT)
FRONTEND_URL  := http://localhost:$(FRONTEND_PORT)
BACKEND_LOG   := /tmp/sql-server-tool-backend.log

# ── Help (default) ───────────────────────────────────────────────────
help: ## Show this help (default target)
	@echo "SQL Server Diagnostic Assistant — make commands"
	@echo
	@grep -E '^[a-zA-Z][a-zA-Z0-9_-]*:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo "Backend:  $(BACKEND_URL)   Frontend: $(FRONTEND_URL)"
	@echo

# ── Run ──────────────────────────────────────────────────────────────
run: ## Start the whole stack: backend (:8000) + frontend (:5173)
	@BACKEND_PID=""; \
	cleanup() { [ -n "$$BACKEND_PID" ] && kill "$$BACKEND_PID" 2>/dev/null || true; rm -f "$(BACKEND_LOG).pid"; }; \
	trap cleanup EXIT INT TERM; \
	echo "Starting backend on $(BACKEND_URL) (logs: $(BACKEND_LOG)) ..."; \
	nohup sh -c 'cd "$(BACKEND_DIR)" && exec uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)' >"$(BACKEND_LOG)" 2>&1 & \
	BACKEND_PID=$$!; echo "$$BACKEND_PID" >"$(BACKEND_LOG).pid"; \
	echo -n "Waiting for backend"; \
	for i in $$(seq 1 60); do \
	  if curl -sf $(BACKEND_URL)/health >/dev/null 2>&1; then echo " ready"; break; fi; \
	  echo -n "."; sleep 0.5; \
	done; \
	if ! curl -sf $(BACKEND_URL)/health >/dev/null 2>&1; then echo " backend did not become healthy — check $(BACKEND_LOG)"; fi; \
	echo "Starting frontend on $(FRONTEND_URL) — press Ctrl+C to stop both."; \
	cd "$(FRONTEND_DIR)" && npm run dev

backend: ## Run only the backend API on :8000
	cd "$(BACKEND_DIR)" && uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend: ## Run only the frontend dev server on :5173
	cd "$(FRONTEND_DIR)" && npm run dev

# ── Install ──────────────────────────────────────────────────────────
setup: backend-install frontend-install ## Install backend and frontend dependencies
install: setup ## Alias for setup

backend-install: ## Install backend dependencies (uv sync)
	cd "$(BACKEND_DIR)" && uv sync

frontend-install: ## Install frontend dependencies (npm install)
	cd "$(FRONTEND_DIR)" && npm install

# ── Test / lint / build ──────────────────────────────────────────────
test: backend-test frontend-test ## Run backend and frontend test suites

backend-test: ## Run backend tests (pytest)
	cd "$(BACKEND_DIR)" && uv run pytest

backend-lint: ## Lint backend (ruff check)
	cd "$(BACKEND_DIR)" && uv run ruff check .

backend-format: ## Format backend code (ruff format)
	cd "$(BACKEND_DIR)" && uv run ruff format .

frontend-test: ## Run frontend tests (vitest)
	cd "$(FRONTEND_DIR)" && npm test

frontend-typecheck: ## Type-check frontend (tsc)
	cd "$(FRONTEND_DIR)" && npm run typecheck

frontend-build: ## Type-check and build frontend for production
	cd "$(FRONTEND_DIR)" && npm run build

# ── Utilities ────────────────────────────────────────────────────────
token: ## Print a bearer token (seeded dba user; backend must be running)
	@curl -s -X POST $(BACKEND_URL)/auth/login \
	  -H 'Content-Type: application/json' \
	  -d '{"username":"dba","password":"dba-password"}' \
	  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'

clean: ## Remove caches and the frontend build output
	rm -rf "$(FRONTEND_DIR)/dist"
	rm -rf "$(BACKEND_DIR)/.pytest_cache" "$(BACKEND_DIR)/.ruff_cache"
	rm -rf .pytest_cache .ruff_cache
	find "$(BACKEND_DIR)" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
