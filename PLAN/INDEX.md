# Implementation Issues Index

This directory contains detailed specifications for each GitHub Issue.
Issues are organised by implementation phase and should be worked on in dependency order.

**Source repo:** Clone `https://github.com/jwayong/corebanking` to `../corebanking` before starting.

## Phase 1: Foundation

| # | Issue | File | Depends On |
|---|-------|------|------------|
| 1 | [Project bootstrap, config, Docker, Makefile](01-project-bootstrap.md) | `01-project-bootstrap.md` | -- |
| 2 | [Alembic migrations (10 scripts)](02-alembic-migrations.md) | `02-alembic-migrations.md` | #1 |
| 3 | [TigerBeetle client wrapper](03-tigerbeetle-client.md) | `03-tigerbeetle-client.md` | #1 |
| 4 | [PostgreSQL connection pool and database module](04-postgres-pool.md) | `04-postgres-pool.md` | #1 |

## Phase 2: Domain Model + CLI

| # | Issue | File | Depends On |
|---|-------|------|------------|
| 5 | [Domain constants, types, and error classes](05-domain-model.md) | `05-domain-model.md` | #1 |
| 6 | [Utility modules (UUIDv7, amount, TB types)](06-utility-modules.md) | `06-utility-modules.md` | #1 |
| 7 | [PostgreSQL store repositories (all repos)](07-postgres-repos.md) | `07-postgres-repos.md` | #4, #5 |
| 8 | [CLI commands and product loader](08-cli-commands.md) | `08-cli-commands.md` | #2, #3, #4, #5 |

## Phase 3: Core API

| # | Issue | File | Depends On |
|---|-------|------|------------|
| 9 | [In-memory caches (FX, product, ledger)](09-in-memory-caches.md) | `09-in-memory-caches.md` | #4, #5 |
| 10 | [TigerBeetle store repositories](10-tigerbeetle-repos.md) | `10-tigerbeetle-repos.md` | #3, #5, #6 |
| 11 | [Service layer: accounts, customers, balances](11-service-layer-core.md) | `11-service-layer-core.md` | #7, #9, #10 |
| 12 | [Service layer: transfers, FX, holds, loans, fees, settlements](12-service-layer-transfers.md) | `12-service-layer-transfers.md` | #7, #9, #10 |
| 13 | [API middleware (idempotency, request ID, logging, errors)](13-api-middleware.md) | `13-api-middleware.md` | #4 |
| 14 | [API route handlers (all endpoints)](14-api-routes.md) | `14-api-routes.md` | #11, #12, #13 |
| 15 | [Litestar app factory and wiring](15-app-factory.md) | `15-app-factory.md` | #14 |

## Phase 4: Batch Operations

| # | Issue | File | Depends On |
|---|-------|------|------------|
| 16 | [Batch runner and interest accrual job](16-batch-runner.md) | `16-batch-runner.md` | #7, #10 |
| 17 | [Remaining batch jobs (capitalise, fees, arrears)](17-batch-jobs.md) | `17-batch-jobs.md` | #16 |

## Cross-Cutting

| # | Issue | File | Depends On |
|---|-------|------|------------|
| 18 | [Test suite (unit, integration, e2e)](18-test-suite.md) | `18-test-suite.md` | #15, #17 |
| 19 | [CI/CD pipeline (GitHub Actions)](19-cicd-pipeline.md) | `19-cicd-pipeline.md` | #1 |
