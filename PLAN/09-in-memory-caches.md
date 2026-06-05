# Issue 09: In-Memory Caches

**Phase:** 3 - Core API
**Priority:** Medium
**Labels:** `phase-3`, `cache`
**Depends on:** #04 (PG Pool), #05 (Domain Model)

## Summary

Implement in-memory caches for FX rates (TTL-based), products (TTL-based),
and ledger/currency mappings (immutable, loaded once at startup).

## Files to Create

| File | Description |
|------|-------------|
| `src/cbs/cache/__init__.py` | Cache package |
| `src/cbs/cache/fx_cache.py` | FX rate cache with configurable TTL (default 30s) |
| `src/cbs/cache/product_cache.py` | Product cache with configurable TTL (default 300s) |
| `src/cbs/cache/ledger_cache.py` | Immutable currency-to-ledger and code mappings |

## Key Patterns

- **FX Cache:** Fetches rates from PG on cache miss, expires after `cache_ttl_fx` seconds
- **Product Cache:** Fetches product details from PG on cache miss, expires after `cache_ttl_product` seconds
- **Ledger Cache:** Loaded once at startup from `CurrencyInfo` constants, never expires (immutable)
- All caches are in-process memory (no Redis) — consistent with stateless design
- Use `asyncio.Lock` for cache stampede protection on refresh

## Go Source References

| Go File | Purpose |
|---------|---------|
| `../corebanking/internal/cache/fx_cache.go` | FX rate caching |
| `../corebanking/internal/cache/product_cache.go` | Product caching |
| `../corebanking/internal/cache/ledger_cache.go` | Ledger mappings |

## Acceptance Criteria

- [ ] FX cache returns fresh rates, refreshes after TTL expiry
- [ ] Product cache returns cached products, refreshes after TTL
- [ ] Ledger cache provides O(1) currency-to-ledger lookups
- [ ] Cache stampede protection (only one refresh at a time)
- [ ] Unit tests with mock PG repo
