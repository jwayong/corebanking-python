# Issue 19: CI/CD Pipeline (GitHub Actions)

**Phase:** Cross-Cutting
**Priority:** Medium
**Labels:** `ci-cd`
**Depends on:** #01 (Project Bootstrap)

## Summary

Set up the GitHub Actions CI pipeline for linting, type checking, and
testing with TigerBeetle and PostgreSQL service containers.

## Files to Create

| File | Description |
|------|-------------|
| `.github/workflows/ci.yml` | CI pipeline (lint + test) |

## Detailed Spec

```yaml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install ruff mypy
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: mypy src/

  test:
    runs-on: ubuntu-latest
    services:
      tigerbeetle:
        image: ghcr.io/tigerbeetle/tigerbeetle:latest
        ports: ["3001:3001"]
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: corebanking
          POSTGRES_USER: cbs
          POSTGRES_PASSWORD: cbs_dev
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install -e ".[dev]"
      - run: cbs migrate up
      - run: pytest --cov=cbs --cov-report=xml
```

## CI Checks

| Check | Tool | Command |
|-------|------|---------|
| Lint | ruff | `ruff check src/ tests/` |
| Format | ruff | `ruff format --check src/ tests/` |
| Type check | mypy | `mypy src/` |
| Test | pytest | `pytest --cov=cbs --cov-report=xml` |

## Acceptance Criteria

- [ ] CI runs on every push and pull request
- [ ] Lint job passes when code is properly formatted
- [ ] Test job runs with TB and PG service containers
- [ ] Migrations run before tests
- [ ] Coverage report is generated
- [ ] CI blocks merging on failure (branch protection)
