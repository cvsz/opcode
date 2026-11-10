# OpenCode Free Router — provider intelligence, multi-key failover, orchestration, quality gates, control panel
.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

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
export TEST_COMMAND QUALITY_REQUIRED

.PHONY: help doctor models free-models check-models plan route \
        code review fast vision general orchestrate quality \
        dashboard dashboard-open optimize health reset-circuit keys key-reset test

help:
	@printf '%s\n' \
	  'OpenCode Free Router' \
	  '' \
	  'Discovery:     make doctor | models | free-models | check-models' \
	  'Routing:       make plan TASK=code | make code PROMPT="..."' \
	  'Orchestration: make orchestrate PROMPT="..."' \
	  'Quality gate:  make quality TEST_COMMAND="pytest -q" INDEPENDENT_REVIEW_FILE=docs/review.md' \
	  'Control panel: make dashboard (http://127.0.0.1:8765)' \
	  'Optimizer:     make optimize' \
	  'Circuits:      make health | make reset-circuit' \
	  'API keys:      make keys | make key-reset' \
	  'Tests:         make test' \
	  '' \
	  'Controls: MODEL=id DRY_RUN=1 MAX_MODELS=2 TIMEOUT_SECONDS=180' \
	  'Quality: QUALITY_REQUIRED=1 TEST_COMMAND="pytest -q"'

doctor:
	@command -v "$(OPENCODE)" >/dev/null 2>&1 || { echo "ERROR: OpenCode not found"; exit 127; }
	@command -v python3 >/dev/null
	@command -v bash >/dev/null
	@$(OPENCODE) --version
	@echo "OpenCode Router prerequisites: OK"

models: doctor
	@$(OPENCODE) models

free-models:
	@cat config/free-models.txt

check-models: doctor
	@python3 scripts/router.py check

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

orchestrate: doctor
	@python3 scripts/orchestrator.py

quality: doctor
	@python3 scripts/quality_gate.py

dashboard:
	@python3 dashboard/server.py --host "$(DASHBOARD_HOST)" --port "$(DASHBOARD_PORT)"

dashboard-open:
	@echo "Open http://$(DASHBOARD_HOST):$(DASHBOARD_PORT)/"

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

test:
	@python3 -m unittest discover -s tests -v
