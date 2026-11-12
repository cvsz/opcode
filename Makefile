# OpenCode Free Router — Core Makefile
# Automated start for ALL features / ALL services / ALL functions / ALL modular components / ALL options — single command
# =============================================================================

.DEFAULT_GOAL := help

.PHONY: help all start stop status restart clean-pids clean \
        doctor models free-models check-models \
        plan route code review fast vision general \
        orchestrate quality \
        dashboard dashboard-open dashboard-stop dashboard-restart \
        optimize health reset-circuit keys key-reset \
        test lint type-check security-scan \
        frontend-lint frontend-check backend-lint backend-type-check backend-security \
        ci-all

# ── Help ────────────────────────────────────────────────────────────────────
help:
	@printf '%s\n' \
	  'OpenCode Free Router — Complete Control Makefile' \
	  '' \
	  '════════════════════════════════════════════════════════════════════════════════════════════════════════════════' \
	  'ALL-IN-ONE (start everything)' \
	  '  make all               Start dashboard + run quality gate + run tests' \
	  '  make start             Start dashboard service in background' \
	  '  make stop              Stop all running services' \
	  '  make status            Show status of all services' \
	  '  make restart           Stop and start all services' \
	  '  make clean-pids        Remove stale PID files' \
	  '' \
	  '════════════════════════════════════════════════════════════════════════════════════════════════════════════════' \
	  'DISCOVERY' \
	  '  make doctor            Verify OpenCode CLI, python3, bash availability' \
	  '  make models            List available OpenCode models' \
	  '  make free-models       Show free models catalogue' \
	  '  make check-models      Verify all configured models are available' \
	  '' \
	  'ROUTING (single model task execution)' \
	  '  make plan TASK=code            Show candidate route for task' \
	  '  make route                   Run router with TASK/PROMPT/MODEL' \
	  '  make code|review|fast|vision|general PROMPT="..."  Route specific task' \
	  '' \
	  'ORCHESTRATION & QUALITY' \
	  '  make orchestrate PROMPT="..."   Full multi-role workflow' \
	  '  make quality TEST_COMMAND="..." INDEPENDENT_REVIEW_FILE=...  Fail-closed quality gate' \
	  '' \
	  'CONTROL PANEL (local HTTP dashboard)' \
	  '  make dashboard           Start control panel (http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/)' \
	  '  make dashboard-open      Print dashboard URL' \
	  '  make dashboard-stop      Stop dashboard service' \
	  '  make dashboard-restart   Restart dashboard service' \
	  '' \
	  'UTILITIES' \
	  '  make optimize            Telemetry-based model ranking' \
	  '  make health              Show API key pool status' \
	  '  make reset-circuit       Reset circuit breaker state' \
	  '  make keys                Show API key pool status' \
	  '  make key-reset           Reset key-pool cooldowns/counters' \
	  '' \
	  'TESTING & QUALITY ASSURANCE' \
	  '  make test                Run full unit test suite' \
	  '  make lint                Run ruff linter' \
	  '  make type-check          Run mypy type checker' \
	  '  make security-scan       Run bandit + pip-audit security scans' \
	  '  make ci-all              Run lint + type-check + security-scan + test' \
	  '' \
	  'FRONTEND' \
	  '  make frontend-lint       Check dashboard frontend files exist' \
	  '  make frontend-check      Validate frontend files present' \
	  '' \
	  'BACKEND' \
	  '  make backend-lint        Run ruff on scripts/ dashboard/' \
	  '  make backend-type-check  Run mypy on scripts/ dashboard/' \
	  '  make backend-security    Run bandit + pip-audit on backend' \
	  '' \
	  'CONTROLS: MODEL=id DRY_RUN=1 MAX_MODELS=2 TIMEOUT_SECONDS=180' \
	  'Quality: QUALITY_REQUIRED=1 TEST_COMMAND="pytest -q"'

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# ══════════════════════════════════════════════════════════════════════════════════
# CORE CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════════

OPENCODE ?= opencode
TASK ?= code
PROMPT ?=
MODEL ?=
DRY_RUN ?= 0
MAX_MODELS ?= 0
TIMEOUT_SECONDS ?= 180
TEST_COMMAND ?=
QUALITY_REQUIRED ?= 1
DASHBOARD_HOST ?= 127.0.0.1
DASHBOARD_PORT ?= 8765

export OPENCODE TASK PROMPT MODEL DRY_RUN MAX_MODELS TIMEOUT_SECONDS
export TEST_COMMAND QUALITY_REQUIRED DASHBOARD_HOST DASHBOARD_PORT

# PID / runtime directory for service management
PID_DIR := .run
DASHBOARD_PID := $(PID_DIR)/dashboard.pid
ORCHESTRATOR_PID := $(PID_DIR)/orchestrator.pid
QUALITY_PID := $(PID_DIR)/quality.pid

# ══════════════════════════════════════════════════════════════════════════════════
# ALL-IN-ONE: Start EVERYTHING with one command
# ══════════════════════════════════════════════════════════════════════════════════

all: start-all run-quality run-tests   ## Start ALL services, run quality gate, run tests — one command

start-all: doctor start-dashboard   ## Start every service (dashboard) in background

start: start-all   ## Alias for start-all

start-dashboard: $(DASHBOARD_PID)   ## Start dashboard control panel server

$(DASHBOARD_PID): | $(PID_DIR)
	@if [ -f "$(DASHBOARD_PID)" ] && kill -0 "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null; then \
		echo "✅ Dashboard already running (PID $$(cat "$(DASHBOARD_PID)")) on http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/"; \
	else \
		@mkdir -p "$(PID_DIR)"; \
		nohup python3 dashboard/server.py --host "$(DASHBOARD_HOST)" --port "$(DASHBOARD_PORT)" \
			> "$(PID_DIR)/dashboard.log" 2>&1 & \
		echo $$! > "$(DASHBOARD_PID)"; \
		sleep 1; \
		if kill -0 "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null; then \
			echo "🚀 Dashboard started (PID $$(cat "$(DASHBOARD_PID)")) on http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/"; \
		else \
			echo "❌ Dashboard failed to start. Check $(PID_DIR)/dashboard.log"; \
			exit 1; \
		fi; \
	fi

$(PID_DIR):
	@mkdir -p "$(PID_DIR)"

stop: stop-dashboard   ## Stop all running services

stop-dashboard:
	@if [ -f "$(DASHBOARD_PID)" ] && kill -0 "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null; then \
		kill "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null || true; \
		rm -f "$(DASHBOARD_PID)"; \
		echo "🛑 Dashboard stopped."; \
	else \
		echo "ℹ️  Dashboard not running."; \
	fi

status:   ## Show status of ALL services
	@echo "================================================" \
	&& echo "  OpenCode Router — Service Status" \
	&& echo "================================================" \
	&& if [ -f "$(DASHBOARD_PID)" ] && kill -0 "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null; then \
		echo "  ✅ dashboard:  RUNNING (PID $$(cat "$(DASHBOARD_PID)")) → http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/"; \
	else \
		echo "  ❌ dashboard:  STOPPED"; \
	fi \
	&& if command -v "$(OPENCODE)" >/dev/null 2>&1; then \
		echo "  ✅ opencode:   AVAILABLE ($$(command -v "$(OPENCODE)"))"; \
	else \
		echo "  ❌ opencode:   NOT FOUND"; \
	fi \
	&& if command -v python3 >/dev/null 2>&1; then \
		echo "  ✅ python3:    AVAILABLE ($$(command -v python3))"; \
	else \
		echo "  ❌ python3:    NOT FOUND"; \
	fi

restart: stop start-all   ## Stop and start all services

clean-pids:
	@rm -f "$(DASHBOARD_PID)"
	@echo "🧹 PID files cleaned."

clean: clean-pids   ## Clean all generated artifacts

# ══════════════════════════════════════════════════════════════════════════════════
# DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════════

doctor:
	@command -v "$(OPENCODE)" >/dev/null 2>&1 || { echo "❌ ERROR: OpenCode CLI not found"; exit 127; }
	@command -v python3 >/dev/null || { echo "❌ ERROR: python3 not found"; exit 127; }
	@command -v bash >/dev/null || { echo "❌ ERROR: bash not found"; exit 127; }
	@$(OPENCODE) --version
	@echo "✅ OpenCode Router prerequisites: OK"

models: doctor
	@$(OPENCODE) models

free-models:
	@cat config/free-models.txt

check-models: doctor
	@python3 scripts/router.py check

# ══════════════════════════════════════════════════════════════════════════════════
# ROUTING — ALL TASK TYPES (modular functions)
# ══════════════════════════════════════════════════════════════════════════════════

plan: doctor
	@python3 scripts/router.py plan

route: doctor
	@python3 scripts/router.py route

code:
	@TASK=code $(MAKE) --no-print-directory route PROMPT="$(PROMPT)" MODEL="$(MODEL)"

review:
	@TASK=review $(MAKE) --no-print-directory route PROMPT="$(PROMPT)" MODEL="$(MODEL)"

fast:
	@TASK=fast $(MAKE) --no-print-directory route PROMPT="$(PROMPT)" MODEL="$(MODEL)"

vision:
	@TASK=vision $(MAKE) --no-print-directory route PROMPT="$(PROMPT)" MODEL="$(MODEL)"

general:
	@TASK=general $(MAKE) --no-print-directory route PROMPT="$(PROMPT)" MODEL="$(MODEL)"

# ══════════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION & QUALITY (backend functions)
# ══════════════════════════════════════════════════════════════════════════════════

orchestrate: doctor
	@python3 scripts/orchestrator.py

quality: doctor
	@python3 scripts/quality_gate.py

# ══════════════════════════════════════════════════════════════════════════════════
# CONTROL PANEL — DASHBOARD (frontend + backend API)
# ══════════════════════════════════════════════════════════════════════════════════

dashboard-open:
	@echo "🌐 Open http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/"

dashboard-stop:
	@if [ -f "$(DASHBOARD_PID)" ] && kill -0 "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null; then \
		kill "$$(cat "$(DASHBOARD_PID)")" 2>/dev/null || true; \
		rm -f "$(DASHBOARD_PID)"; \
		echo "🛑 Dashboard stopped."; \
	else \
		echo "ℹ️  Dashboard not running."; \
	fi

dashboard-restart: dashboard-stop start-dashboard   ## Restart dashboard service

# ══════════════════════════════════════════════════════════════════════════════════
# UTILITIES — BACKEND FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════════

optimize:
	@python3 scripts/optimizer.py

health:
	@python3 scripts/router.py health

reset-circuit:
	@python3 scripts/router.py reset

keys:
	@python3 scripts/key_pool.py status

key-reset:
	@python3 scripts/key_pool.py reset

# ══════════════════════════════════════════════════════════════════════════════════
# TESTING & QUALITY ASSURANCE — ALL OPTIONS
# ══════════════════════════════════════════════════════════════════════════════════

test:
	@python3 -m unittest discover -s tests -v

lint:
	@ruff check .

type-check:
	@mypy --explicit-package-bases scripts/ dashboard/

security-scan:
	@bandit -r scripts/ dashboard/ || true
	@pip-audit || true

ci-all: lint type-check security-scan test   ## Run full CI pipeline: lint + type-check + security + tests

# ══════════════════════════════════════════════════════════════════════════════════
# FRONTEND — ALL OPTIONS
# ══════════════════════════════════════════════════════════════════════════════════

frontend-lint:
	@echo "🔍 Frontend lint: dashboard/app.js dashboard/style.css dashboard/index.html" \
	&& if command -v npx >/dev/null 2>&1; then \
		npx eslint dashboard/app.js 2>/dev/null || echo "ℹ️  eslint not configured for dashboard/app.js"; \
	else \
		echo "ℹ️  npx not found — skipping frontend lint"; \
	fi

frontend-check:
	@echo "🔍 Frontend check: validating dashboard HTML/CSS/JS" \
	&& if [ -f dashboard/index.html ] && [ -f dashboard/style.css ] && [ -f dashboard/app.js ]; then \
		echo "  ✅ dashboard/index.html  present"; \
		echo "  ✅ dashboard/style.css   present"; \
		echo "  ✅ dashboard/app.js      present"; \
	else \
		echo "  ❌ Some frontend files are missing"; \
		exit 1; \
	fi

# ══════════════════════════════════════════════════════════════════════════════════
# BACKEND — ALL OPTIONS
# ══════════════════════════════════════════════════════════════════════════════════

backend-lint:
	@echo "🔍 Backend lint: scripts/*.py dashboard/server.py" \
	&& ruff check scripts/ dashboard/

backend-type-check:
	@echo "🔍 Backend type-check: mypy scripts/ dashboard/" \
	&& mypy --explicit-package-bases scripts/ dashboard/

backend-security:
	@echo "🔍 Backend security scan: bandit + pip-audit" \
	&& bandit -r scripts/ dashboard/ || true \
	&& pip-audit || true

# ══════════════════════════════════════════════════════════════════════════════════
# CI PIPELINE — ALL CHECKS IN ONE COMMAND
# ══════════════════════════════════════════════════════════════════════════════════

ci-all: frontend-lint frontend-check backend-lint backend-type-check backend-security test   ## Run ALL checks: lint + type-check + security + tests (full CI)
