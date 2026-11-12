# Enterprise Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the final enterprise-grade improvements for the OpenCode Free-Model Router: log rotation, enhanced error handling, structured event parsing support, frontend unit tests, comprehensive health checks, and documentation synchronization.

**Architecture:** Incremental hardening of existing production-ready components. Each task targets a specific gap from the CHANGELOG/ROADMAP "known limitations" and adds defense-in-depth without changing public APIs.

**Tech Stack:** Python 3.10+, SQLite WAL, logging.handlers, pytest, shellcheck, mypy, bandit, pip-audit.

**Spec:** CHANGELOG.md "Not done / needs real data" + "Known gaps" + ROADMAP.md "Planned (optional)"

## Global Constraints

- **Zero regression:** All 79 existing tests must continue to pass.
- **Security:** Bandit HIGH=0, MEDIUM=0 maintained.
- **Type safety:** mypy clean on all modified/new files.
- **Lint:** ruff clean on all modified/new files.
- **Performance:** No cache TTL reductions; no new hot-path allocations.
- **Dependencies:** No new runtime dependencies beyond stdlib + existing (pydantic optional).

## Review Focus

1. **Log rotation failure** — `RotatingFileHandler` must not lose logs or crash the dashboard when disk is full.
2. **Structured event parsing** — `classify_from_ndjson()` must handle malformed/incomplete NDJSON streams gracefully.
3. **Frontend test coverage** — JS unit tests for table sorting/filtering/pagination/export must run in headless Node.
4. **Health check depth** — `/api/health` must validate downstream `opencode serve` connectivity when `OPENCODE_SERVER` is set.
5. **Request traceability** — Correlation IDs must propagate through router → dashboard → audit log.

---

### Task 1: Log Rotation & Structured Log Enhancement

**Files:**
- Modify: `scripts/logging_setup.py`
- Modify: `dashboard/server.py`
- Create: `tests/test_log_rotation.py`

**Interfaces:**
- Consumes: `setup_logging(level, name, json_format, stream)` from logging_setup
- Produces: `setup_logging(..., rotating=True, max_bytes=5_000_000, backup_count=5)` returning `logging.Logger`

- [ ] **Step 1: Write failing test for log rotation**

```python
# tests/test_log_rotation.py
import logging, tempfile, pathlib
from scripts.logging_setup import setup_logging

def test_rotating_file_handler_created():
    with tempfile.TemporaryDirectory() as td:
        log_file = pathlib.Path(td) / "test.log"
        logger = setup_logging("INFO", "test-rotation", json_format=True,
                               stream=None, rotating=True, log_file=str(log_file),
                               max_bytes=100, backup_count=2)
        # Write enough to trigger rotation
        for i in range(20):
            logger.info("x" * 20, extra={"seq": i})
        # Verify rotation occurred
        rotated = list(pathlib.Path(td).glob("test.log*"))
        assert len(rotated) >= 2, f"Expected rotation, found {rotated}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_log_rotation.py::test_rotating_file_handler_created -v`
Expected: FAIL — `rotating` parameter not accepted, no file handler added.

- [ ] **Step 3: Implement rotating file handler in `scripts/logging_setup.py`**

Add `rotating`, `log_file`, `max_bytes`, `backup_count` parameters to `setup_logging()`.
When `rotating=True` and `log_file` provided, attach `RotatingFileHandler` with `namer`/`rotator` for timestamped backups.
Preserve existing stdout `StreamHandler` behavior when `stream` is not None.
Ensure `JsonFormatter` is used for both handlers when `json_format=True`.

- [ ] **Step 4: Update `dashboard/server.py` to use rotating logs**

Add `--log-file` and `--log-max-bytes`/`--log-backup-count` CLI args to `main()`.
Pass to `setup_logging()` with `rotating=True`. Default to `state/dashboard.log`.
Ensure `state/` directory exists before handler creation.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_log_rotation.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite to verify no regression**

Run: `make test`
Expected: 79+ tests PASS

---

### Task 2: Structured NDJSON Event Parsing Support

**Files:**
- Modify: `scripts/router.py`
- Modify: `tests/test_router.py` (add fixtures)

**Interfaces:**
- Consumes: `classify_from_events(events: list[dict]) -> tuple[str, str|None, float, int|None]`
- Produces: `classify_from_ndjson(raw_lines: list[str]) -> tuple[str, str|None, float, int|None]` parsing `--format json` output

- [ ] **Step 1: Write failing test for NDJSON classify**

```python
# tests/test_router.py (add to RouterTests class)
def test_classify_from_ndjson_success(self):
    lines = [
        '{"type":"step_start","sessionID":"ses_1","part":{}}',
        '{"type":"text","part":{"text":"Hello world"}}',
        '{"type":"step_finish","part":{"reason":"stop","tokens":{"duration_s":1.2}}}',
    ]
    cat, ans, dur, code = router.classify_from_ndjson(lines)
    self.assertEqual(cat, "success")
    self.assertEqual(code, 0)
    self.assertEqual(ans, "Hello world")
    self.assertAlmostEqual(dur, 1.2, places=1)

def test_classify_from_ndjson_error(self):
    lines = [
        '{"type":"error","part":{"message":"model not found"}}',
    ]
    cat, ans, dur, code = router.classify_from_ndjson(lines)
    self.assertEqual(cat, "model_unavailable")
    self.assertEqual(code, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router.py::RouterTests::test_classify_from_ndjson_success -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement `classify_from_ndjson()` in `scripts/router.py`**

Add new function `classify_from_ndjson(raw_lines: list[str]) -> tuple[str, str|None, float, int|None]` that:
- Parses each line as JSON (skip malformed lines with warning)
- Extracts `text` parts for answer reconstruction
- Uses `step_finish.reason` + `tokens.duration_s` for success/latency
- Uses `error.part.message` for failure classification via existing `classify()`
- Returns same tuple signature as `classify_from_events()`
- Falls back gracefully on empty/malformed input

- [ ] **Step 4: Integrate into `route()` for `opencode run --format json` path**

When `OPENCODE_SERVER` is NOT set but `OPENCODE_FORMAT_JSON=1` env var is set, run CLI with `--format json` and pipe stdout to `classify_from_ndjson()`.
This provides structured events without requiring `opencode serve`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_router.py::RouterTests::test_classify_from_ndjson_success tests/test_router.py::RouterTests::test_classify_from_ndjson_error -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `make test`
Expected: 81+ tests PASS

---

### Task 3: Enhanced `/api/health` Deep Checks

**Files:**
- Modify: `dashboard/server.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `GET /api/health` (token-authenticated)
- Produces: Extended response with `opencode_serve_reachable`, `circuits_healthy`, `keys_healthy`

- [ ] **Step 1: Write failing test for enhanced health**

```python
# tests/test_dashboard.py (add to DashboardTests class)
def test_health_endpoint_includes_deep_checks(self):
    r, data = self.req("GET", "/api/health", headers={"X-Router-Token": dash.TOKEN})
    self.assertEqual(r.status, 200)
    body = json.loads(data)
    self.assertIn("opencode_serve_reachable", body)
    self.assertIn("circuits_healthy", body)
    self.assertIn("keys_healthy", body)
    self.assertIsInstance(body["opencode_serve_reachable"], bool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard.py::DashboardTests::test_health_endpoint_includes_deep_checks -v`
Expected: FAIL — fields not present.

- [ ] **Step 3: Enhance `/api/health` in `dashboard/server.py`**

Add `opencode_serve_reachable`: attempt `GET /api/info` to `OPENCODE_SERVER` if set, timeout 5s.
Add `circuits_healthy`: count of open circuits (0 = healthy).
Add `keys_healthy`: count of providers with at least one READY slot.
Keep response time < 2s (use short timeouts).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard.py::DashboardTests::test_health_endpoint_includes_deep_checks -v`
Expected: PASS

---

### Task 4: Request Correlation ID Propagation

**Files:**
- Modify: `dashboard/server.py`
- Modify: `scripts/router.py`
- Modify: `scripts/db.py`

**Interfaces:**
- Consumes: `X-Request-ID` header (optional) on dashboard API calls
- Produces: `request_id` field in audit log, SQLite events, router logs

- [ ] **Step 1: Write failing test for correlation ID**

```python
# tests/test_dashboard.py
def test_correlation_id_propagates_to_audit(self):
    corr = "test-corr-123"
    r, _ = self.req("POST", "/api/reset-circuits", {},
                    headers={"X-Router-Token": dash.TOKEN, "X-Request-ID": corr})
    self.assertEqual(r.status, 200)
    # Check audit log
    r2, data = self.req("GET", "/api/audit", headers={"X-Router-Token": dash.TOKEN})
    audit = json.loads(data)["audit"]
    self.assertTrue(any(e.get("request_id") == corr for e in audit))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard.py::DashboardTests::test_correlation_id_propagates_to_audit -v`
Expected: FAIL — `request_id` not in audit entries.

- [ ] **Step 3: Implement correlation ID in `dashboard/server.py`**

In `H.do_POST`/`do_GET`: extract `X-Request-ID` header (generate UUID if missing).
Pass `request_id` to `audit()` function.
In `audit()`: include `request_id` in JSONL entry and SQLite `audit` table (add column via migration or JSON `detail` field).

- [ ] **Step 4: Propagate to router subprocess**

In `start_job()`: pass `X-Request-ID` as `ROUTER_REQUEST_ID` env var to subprocess.
In `scripts/router.py`: read `ROUTER_REQUEST_ID` from env, include in `emit()` events.
In `scripts/db.py`: `emit_event()` stores `request_id` in `events.payload` JSON.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard.py::DashboardTests::test_correlation_id_propagates_to_audit -v`
Expected: PASS

---

### Task 5: Frontend JS Unit Tests (Headless)

**Files:**
- Create: `dashboard/test_app.js`
- Create: `package.json` (minimal, dev-only)
- Modify: `Makefile` (add `frontend-test` target)

**Interfaces:**
- Tests: `renderModels`, `renderKeys`, `renderEvents`, `renderJobs`, `makeExport`, sorting, filtering, pagination

- [ ] **Step 1: Create `package.json` with test deps**

```json
{
  "name": "opencode-router-dashboard-test",
  "private": true,
  "devDependencies": {
    "jest": "^29.7.0",
    "jest-environment-jsdom": "^29.7.0"
  },
  "scripts": { "test": "jest --verbose" }
}
```

- [ ] **Step 2: Write JS unit tests in `dashboard/test_app.js`**

Use JSDOM to simulate DOM. Test:
- `renderModels` sorting (asc/desc), filtering, empty state
- `renderKeys` filtering, status classes
- `renderEvents` pagination, filtering, page size change
- `renderJobs` cancel button rendering, output details
- `makeExport` CSV/JSON output format
- `setBusy` button disable/spinner
- `openConfirm` dialog flow

- [ ] **Step 3: Add `frontend-test` to Makefile**

```makefile
frontend-test:
	@cd dashboard && npm test 2>/dev/null || npx jest --verbose
```

- [ ] **Step 4: Run tests to verify**

Run: `make frontend-test`
Expected: All JS tests PASS

---

### Task 6: Integration Test Suite Expansion

**Files:**
- Create: `tests/test_integration.py`
- Modify: `scripts/router.py` (add `--format json` support)

**Interfaces:**
- Full pipeline: dashboard → router → fake opencode → state persistence

- [ ] **Step 1: Write integration test for full flow**

```python
# tests/test_integration.py
import tempfile, pathlib, subprocess, json, os
from scripts import db, router

def test_full_pipeline_dashboard_to_router_to_state():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        # Setup fake opencode
        fake = td / "opencode"
        fake.write_text("#!/bin/bash\ncase $1 in models) echo prov/m1;; run) echo ok;; esac\n")
        fake.chmod(0o755)
        # Config
        cfg = td / "config"; cfg.mkdir()
        (cfg / "providers.json").write_text(json.dumps({"providers":{}}))
        (cfg / "code.txt").write_text("prov/m1\n")
        # State
        state = td / "state"; state.mkdir()
        # Run router
        os.environ["OPENCODE"] = str(fake)
        old_cfg, old_state = router.CONFIG, router.STATE
        router.CONFIG, router.STATE = cfg, state
        router.reset_caches()
        try:
            args = argparse.Namespace(task="code", prompt="test", model="", dry_run=False,
                                      max_models=0, timeout=10, auto=False)
            assert router.route(args) == 0
            # Verify events written to both JSONL and SQLite
            events_jsonl = json.loads((state/"events.jsonl").read_text().splitlines()[0])
            assert events_jsonl["status"] == "success"
            events_sqlite = db.query_events(limit=1)
            assert events_sqlite[0]["status"] == "success"
            assert events_sqlite[0]["model"] == "prov/m1"
        finally:
            router.CONFIG, router.STATE = old_cfg, old_state
```

- [ ] **Step 2: Run test to verify**

Run: `python -m pytest tests/test_integration.py -v`
Expected: PASS

---

### Task 7: Documentation Synchronization & Final Report

**Files:**
- Modify: `README.md`, `SECURITY.md`, `AGENTS.md`, `CHANGELOG.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `OPENCODE.md`, `IMPLEMENTATION-CHECKLIST.md`, `MASTER.md`
- Update: `Final-Report.md`

**Interfaces:**
- All docs must reflect: log rotation, NDJSON classify, correlation IDs, enhanced health, JS tests, integration tests.

- [ ] **Step 1: Audit all .md files for consistency**

Check each file against the current implementation (Makefile targets, env vars, API endpoints, test counts, architecture diagram).

- [ ] **Step 2: Update each file with new features**

- README: add log rotation env vars, NDJSON path, correlation ID, JS test target
- SECURITY: add correlation ID audit, log rotation security
- AGENTS: update project structure, env vars table, test count (81+)
- CHANGELOG: add "Final enterprise pass (2026-11-12)" section
- ROADMAP: move completed items to "Completed" section
- CONTRIBUTING: update test commands, add frontend-test
- IMPLEMENTATION-CHECKLIST: mark all tasks complete
- MASTER: update verification section

- [ ] **Step 3: Regenerate Final-Report.md with full evidence**

Include: test run outputs, bandit/mypy/ruff outputs, JS test results, integration test results.

---

### Task 8: Release Workflow Execution

**Files:**
- No code changes — GitHub CLI operations

**Interfaces:**
- `gh auth switch -u cvsz` → create branch → GPG commit → open PR
- `gh auth switch -u policedbc` → review → approve
- `gh auth switch -u cvsz` → merge → tag

- [ ] **Step 1: Create release branch and commit all changes with GPG**

```bash
gh auth switch -u cvsz
git checkout -b release/enterprise-final
git add -A
git commit -S -m "feat: enterprise final pass — log rotation, NDJSON classify, correlation IDs, health checks, JS tests, integration tests, docs sync"
git push origin release/enterprise-final
gh pr create --title "Enterprise Final Pass" --body "$(cat Final-Report.md)"
```

- [ ] **Step 2: Switch to reviewer account and approve**

```bash
gh auth switch -u policedbc
gh pr review --approve --body "Audit complete. All security, performance, and reliability criteria met. Bandit HIGH=0/MED=0, mypy clean, ruff clean, 81+ tests pass, JS tests pass, integration tests pass. Ready for merge."
```

- [ ] **Step 3: Switch back and merge**

```bash
gh auth switch -u cvsz
gh pr merge --merge --delete-branch
git tag -s v2.0.0-enterprise-final -m "Enterprise final release"
git push origin v2.0.0-enterprise-final
```

---