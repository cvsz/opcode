# CONTRIBUTING.md

# Contributing

## Setup

```bash
git clone <repo>
cd opencode-router
python -m unittest discover -s tests -v
```

## Workflow

1. Create a branch from `main`
2. Make changes
3. Run tests: `python -m unittest discover -s tests -v`
4. Run compile check: `python -m py_compile scripts/*.py dashboard/server.py`
5. Run shellcheck: `shellcheck scripts/load-keys.sh`
6. Update `CHANGELOG.md` for user-visible changes
7. Open a pull request

## Code style

- Python: type hints (`from __future__ import annotations`), `logging` module (not `print`)
- Frontend: single HTML file unless modularizing; ARIA attributes on interactive elements
- Commits: conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)

## Testing

64 tests must pass. New features require tests.

## Security

Never log or print secrets. Dashboard token uses `hmac.compare_digest`.
