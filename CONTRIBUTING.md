# CONTRIBUTING.md

# Contributing

## Setup

```bash
git clone <repo>
cd opencode-router
python -m unittest discover -s tests -v
make lint type-check security-scan
```

## Workflow

1. Create a branch from `main`
2. Make changes
3. Run tests: `python -m unittest discover -s tests -v`
4. Run compile check: `python -m py_compile scripts/*.py dashboard/server.py`
5. Run lint: `make lint`
6. Run type check: `make type-check`
7. Run security scan: `make security-scan`
8. Run full CI: `make ci-all`
9. Update `CHANGELOG.md` for user-visible changes
10. Open a pull request

## Code style

- Python: type hints (`from __future__ import annotations`), `logging` module (not `print`)
- Frontend: modular HTML/CSS/JS split; ARIA attributes on interactive elements
- Commits: conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)

## Testing

77 tests must pass. New features require tests. Security regressions require regression tests.

## Security

Never log or print secrets. Dashboard token uses `hmac.compare_digest`. All security findings must be addressed — never suppress a security failure to make CI green.
