# OpenCode Router Enterprise Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the OpenCode Router into a Production-Grade, Enterprise-ready system with robust security, observability, and a standardized release workflow.

**Architecture:** Implements structured logging, hardened Pydantic schemas, comprehensive health monitoring, and a multi-account GitHub audit workflow.

**Tech Stack:** Python 3.10+, Pydantic, SQLite, GitHub CLI (gh).

**Spec:** [Full Independent Implementation Request]

## Global Constraints
- **Reliability:** Maintain 100% test pass rate (77+ tests).
- **Security:** Zero High/Medium Bandit issues.
- **Type Safety:** 100% Mypy coverage on all new/modified modules.
- **Workflow:** All production merges must be GPG-signed and approved by `policedbc`.

## Review Focus
- **Input Fuzzing:** Ensure `RunRequest` rejects malformed or oversized prompts before reaching the shell.
- **State Corruption:** Verify the router survives a zero-byte or malformed `circuits.json` without crashing.
- **Concurrency:** Ensure SQLite writes are atomic and WAL-mode enabled for dashboard/router parallel access.
- **Auth Bypass:** Confirm that manual token manipulation in headers is rejected by `hmac.compare_digest`.
- **Resource Exhaustion:** Confirm that the log rotator prevents `dashboard.log` from filling the disk.

---

### Task 1: Structured Logging & Robustness

**Files:**
- Modify: `scripts/logging_setup.py`
- Modify: `dashboard/server.py`
- Modify: `scripts/router.py`

**Interfaces:**
- Produces: `setup_logging(..., json_format=True)`

- [ ] **Step 1: Implement JSON structured logging in `logging_setup.py`**
- [ ] **Step 2: Update Dashboard and Router to use structured logs when `LOG_FORMAT=json` is set.**
- [ ] **Step 3: Enable SQLite WAL mode in `scripts/db.py` for concurrent access.**
- [ ] **Step 4: Verify with a test that logs are readable and SQLite handles simultaneous writes.**

### Task 2: API Hardening & Health Monitoring

**Files:**
- Modify: `dashboard/server.py`
- Create: `scripts/security_fuzzer.py`

**Interfaces:**
- Produces: `GET /api/health` returning `{status: "ok", circuits: [...], keys: [...]}`

- [ ] **Step 1: Tighten Pydantic models in `server.py` with `StringConstraints` (max length for prompts).**
- [ ] **Step 2: Implement `/api/health` endpoint with deep-checks for key pool availability.**
- [ ] **Step 3: Write a security fuzzer that sends oversized/invalid payloads to `/api/run`.**
- [ ] **Step 4: Run `make test` and verify zero crashes.**

### Task 3: Final Documentation & Final Report

**Files:**
- Create: `Final-Report.md`
- Modify: `README.md`, `SECURITY.md`, `AGENTS.md`

- [ ] **Step 1: Audit all `.md` files for consistency with the new Makefile and Chat interface.**
- [ ] **Step 2: Generate a comprehensive `Final-Report.md` covering architecture, security, and usage.**
- [ ] **Step 3: Update `SECURITY.md` with findings from the fuzzer and Bandit scans.**

### Task 4: Production Release Workflow (The "Enterprise" Merge)

- [ ] **Step 1: `gh auth switch -u cvsz`**
- [ ] **Step 2: Create a final release branch, GPG commit all changes, and open a PR.**
- [ ] **Step 3: `gh auth switch -u policedbc`**
- [ ] **Step 4: Perform the final Audit, Review, and Approve the PR.**
- [ ] **Step 5: `gh auth switch -u cvsz`**
- [ ] **Step 6: Merge to `main` and tag the release.**
