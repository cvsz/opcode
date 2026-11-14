# AUTOPILOT.md — Global Master Prompt (exec loop)

**Cross-platform Repository-Aware Engineering Orchestrator.** This file is the authoritative system instruction for any autonomous agent running the OPCODE execution loop ("AUTOPILOT"). It governs the full delivery pipeline:

**Discover → Understand → Plan → Engineer → Full Independent Implementation → Optimize (best performance) → Test → Validate → Repair → Update Documents (create final-report.md) → Merge**

Read it in full before starting any exec loop. It overrides agent-specific files (`AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `GEMINI.md`) when they conflict; otherwise they are complementary.

## Pipeline stages (exec loop)

Execute the stages in order. Do not skip a stage. Exit criteria for each stage must be met with observable evidence before advancing.

### Stage 1 — Discover

- Inventory the repository root: `Makefile`, `AGENTS.md`, `README.md`, `CLAUDE.md`, `CODEX.md`, `GEMINI.md`, `CHANGELOG.md`, `pyproject.toml`, `requirements*.txt`, `package.json`, `Cargo.toml`, `*.sln`, `pom.xml`, `build.gradle`, `docker-compose.yml`, `Dockerfile`, `.github/workflows/*.yml`, `.gitignore`.
- Detect language/runtime: Python (`scripts/*.py`, `dashboard/server.py`), Bash (`opcode`, `*.sh`), JavaScript (`dashboard/app.js`), HTML/CSS.
- Locate tests (`tests/*.py`), config (`config/*`), state (`state/`), docs (`docs/`).
- Record git status: branch, uncommitted changes, remotes.
- **Exit evidence:** `git status --short`, `ls` of key dirs, list of test files and CI targets.

### Stage 2 — Understand

- Read `AGENTS.md` and any agent instruction files for structure, commands, env vars, and design points.
- Read the Makefile targets (`test`, `lint`, `type-check`, `security-scan`, `ci-all`, `start`, `status`).
- Read the files you intend to modify in full before editing.
- Identify the risk surface: safety-critical paths (auth, keys, routing, free-only gating), test files, config files, CI.
- **Exit evidence:** notes on how tests run, how CI is invoked, which files are safety-critical.

### Stage 3 — Plan

- Define the objective and success criteria (specific command + expected output).
- Define exactly what this cycle will change.
- Identify cross-platform constraints (path separators, shell quoting, line endings, command availability: `date`, `stat`, `jq`, `flock`, `curl`, `python3` vs `python`).
- **Exit evidence:** written plan + success criteria + risk list.

### Stage 4 — Engineer

- Implement the change. Smallest logical change per cycle; reviewable and rollback-safe.
- Keep secrets out of source control: env vars or protected config files (chmod 600), never commit credentials.
- Preserve file encoding and line endings (write `LF`; preserve existing when editing).
- **Exit evidence:** the diff of changed files.

### Stage 5 — Full Independent Implementation

- Complete the implementation end-to-end without external intervention: all wiring, CLI commands, config, docs, and tests for the feature.
- Do not fabricate test data that pretends to be real provider evidence; use realistic fixtures clearly marked as fixtures.
- **Exit evidence:** feature fully wired, CLI commands functional, docs updated.

### Stage 6 — Optimize (best performance)

- Optimize for best performance within safety constraints: caching, rate-limit budgets, retry budgets, cooldowns, connection reuse, TTL caches, avoiding redundant network calls.
- Never optimize by weakening safety (no silent paid fallback, no unknown→verified upgrades).
- Measure before/after where measurable (command timing, cache hit rates, request counts).
- **Exit evidence:** performance notes or timing measurements; safety constraints unchanged.

### Stage 7 — Test

Run the full verification sequence and capture output:

**Python repo (OPCODE):**
```bash
# Compile-check all Python
python -m py_compile scripts/*.py dashboard/server.py

# Full test suite
python -m unittest discover -s tests -v

# Full CI: lint + type-check + security + tests
make ci-all
```

**Security-critical changes additionally require:**
```bash
make security-scan   # bandit + pip-audit
make lint            # ruff check
make type-check      # mypy
```

**Cross-platform / shell:**
```bash
shellcheck *.sh 2>/dev/null || true
bash -n scripts/*.sh 2>/dev/null || true
```

- **Exit evidence:** captured output of every command above; all green.

### Stage 8 — Validate

- Confirm acceptance criteria are met: objective complete, no regressions in existing provider integrations, safety rules honored.
- Confirm no secrets logged, no API keys in output, no fabricated evidence.
- Confirm cross-platform compatibility of commands.
- **Exit evidence:** validation checklist with pass/fail per criterion.

### Stage 9 — Repair

- If any stage 7/8 check failed, fix the root cause (not just the symptom), then re-run stage 7 (Test) and stage 8 (Validate).
- If the same failure repeats 3 cycles despite different attempts, stop and report the blocker with exact commands and output.
- **Exit evidence:** repaired code, re-run test output showing green.

### Stage 10 — Update Documents (create final-report.md)

- Update `CHANGELOG.md` for user-visible or behavior changes.
- Update `AGENTS.md` if structure, env vars, or commands change.
- Update `README.md` if usage changes.
- Update relevant `docs/*` and `config/*`.
- **Create `Final-Report.md`** at the repository root summarizing: objective met, changed files, test results, `make ci-all` result, remaining limitations, and exact verification commands run (this is the termination report).
- Do not edit `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, or `GEMINI.md` unless the change genuinely alters repo structure, commands, or design points.
- **Exit evidence:** docs diff; `Final-Report.md` created and consistent with the code changes.

### Stage 11 — Merge

- Only when stages 1–10 are all green: commit with a descriptive message.
- Produce the termination report (see below) if `Final-Report.md` was not already created.
- **Exit evidence:** commit hash, `Final-Report.md`, termination report.

## Cross-platform execution rules

| Concern | Rule |
|---|---|
| Shell | Prefer POSIX `sh`/`bash`; avoid bashisms unless confirmed available. On Windows use Git Bash / WSL / PowerShell with equivalent semantics. |
| Paths | Use `/`-joined paths via `pathlib`/`os.path.join`; never hardcode `\` or `/`. |
| Line endings | Write files with `LF` (`\n`); preserve existing file line endings when editing. |
| Permissions | `chmod 600` for secret/config files; `0755` for executables; `umask 077` where sensitive. |
| Temp dirs | Use `mktemp -d` / `tempfile.mkdtemp()`; clean up in `trap EXIT` / `finally`. |
| Locking | `flock` on Linux; equivalent file lock on macOS/Windows; do not assume `flock` exists. |
| Date/time | `date -u +'%Y-%m-%dT%H:%M:%SZ'` (GNU) vs `date -u +"%Y-%m-%dT%H:%M:%SZ"` (BSD/macOS); prefer Python `datetime.now(timezone.utc).isoformat()`. |
| JSON | Use `jq` where available; fall back to Python `json`. Never guess JSON structure; validate schema. |
| Network | `curl` with `--fail --show-error --connect-timeout --max-time`; never follow redirects carrying bearer tokens; validate URL schemes (http/https only — SSRF guard). |
| Secrets | Never commit keys/tokens. Use env vars or protected config files (600). Redact secrets in any captured output. |

## Core principles

1. **Evidence before assertion.** Never claim complete, fixed, or passing without running the verification commands and capturing their output.
2. **Fail-closed safety.** Never silently fall back to paid/partner models; never upgrade unknown models to verified-free; never log secrets; never fabricate verification evidence.
3. **Small, reversible steps.** One logical change per cycle; each change reviewable and rollback-safe.
4. **Regression guard.** Existing tests and provider integrations must remain backward-compatible; run the full suite, not just the touched area.
5. **No infinite loops.** If the same failure repeats 3 cycles despite different attempts, stop and report the blocker.

## Canonical verification commands

| Purpose | Command |
|---|---|
| Compile-check Python | `python -m py_compile scripts/*.py dashboard/server.py` |
| Run test suite | `python -m unittest discover -s tests -v` |
| Full CI | `make ci-all` |
| Lint only | `make lint` |
| Type-check only | `make type-check` |
| Security scan | `make security-scan` |
| Dashboard start | `make start` |
| Dashboard status | `make status` |
| NVIDIA router CLI | `python scripts/router.py route --task code --prompt "..."` |
| Shell syntax check | `bash -n *.sh` / `shellcheck *.sh` |

## Safety rules (non-negotiable)

- **Free-only mode:** `OPCODE_FREE_ONLY=true`; only models with verified-free evidence enter NVIDIA free routing.
- **No paid fallback:** never silently fall back to paid or Partner Endpoint models.
- **No unknown→verified upgrades:** catalog sync must never silently mark unknown models as verified-free.
- **No secret logging:** never log API keys or `Authorization` headers; redact secrets in captured output.
- **No fabricated evidence:** do not invent provider pricing, endpoint availability, or verification timestamps.
- **Backward compatibility:** existing `openrouter`, `kilo`, `opencode` behavior must not regress.
- **Endpoint classification:** keep `Downloadable`, `Partner Endpoint`, and `Free Endpoint` distinct; never infer free eligibility from `/v1/models`, model names, `:free` suffixes, or preview status.

## Termination report

When the loop terminates, produce a report containing:

1. Objective and whether it was met.
2. List of changed files (add / modify / delete).
3. Test results: number passed/failed, exact failing test names if any.
4. `make ci-all` result (pass/fail) plus lint/type/security summary.
5. Remaining limitations or known gaps.
6. Exact verification commands run and their output (or pointers to logs).
