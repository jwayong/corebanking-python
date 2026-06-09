"""Application configuration — YAML file, env vars, and CLI flag cascade.

Mirrors corebanking/internal/config/config.go with a 3-layer loading strategy:
defaults → YAML file → env vars → CLI flags.
"""

from __future__ import annotations

import os
from typing import Any

import yaml  # pyright: ignore[reportMissingImports]
from pydantic_settings import BaseSettings, SettingsConfigDict


class CBSConfig(BaseSettings):
    """Core banking system configuration.

    Loading priority (highest wins): CLI flags > env vars > config file > defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="CBS_",
        env_nested_delimiter="_",
    )

    tb_addresses: str = ""
    pg_dsn: str = ""
    port: int = 8080
    log_level: str = "info"
    pg_pool_max: int = 10
    cache_ttl_fx: int = 30
    cache_ttl_product: int = 300
    cors_allowed_origins: str = "*"

    def validate(self) -> None:
        """Require TB addresses and PG DSN for commands that need connectivity."""
        if not self.tb_addresses:
            raise ValueError("TigerBeetle addresses required (--tb-address or CBS_TB_ADDRESSES)")
        if not self.pg_dsn:
            raise ValueError("PostgreSQL DSN required (--pg-dsn or CBS_PG_DSN)")


def load_from_file(path: str) -> CBSConfig:
    """Load configuration from a YAML file, then overlay env vars.

    Mirrors Go's LoadFromFile: read YAML as base, let pydantic-settings
    apply env var overrides on top.

    Strategy: load defaults from env first, then overlay file values only
    where env vars are NOT set (matching Go's cascade: defaults < file < env).
    """
    # First, get values from env vars only.
    env_cfg = CBSConfig()

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Build final config: start with env values, overlay file values
    # only where the corresponding env var is NOT set (using os.environ).
    kwargs: dict[str, Any] = {
        "tb_addresses": env_cfg.tb_addresses,
        "pg_dsn": env_cfg.pg_dsn,
        "port": env_cfg.port,
        "log_level": env_cfg.log_level,
        "pg_pool_max": env_cfg.pg_pool_max,
        "cache_ttl_fx": env_cfg.cache_ttl_fx,
        "cache_ttl_product": env_cfg.cache_ttl_product,
        "cors_allowed_origins": env_cfg.cors_allowed_origins,
    }

    # Overlay file values where env var is not set (checked via os.environ).
    if "port" in data and "CBS_PORT" not in os.environ:
        kwargs["port"] = int(data["port"])
    if "tb_addresses" in data and "CBS_TB_ADDRESSES" not in os.environ:
        raw = data["tb_addresses"]
        if isinstance(raw, list):
            kwargs["tb_addresses"] = ",".join(str(x) for x in raw)
        else:
            kwargs["tb_addresses"] = str(raw)
    if "pg_dsn" in data and "CBS_PG_DSN" not in os.environ:
        kwargs["pg_dsn"] = str(data["pg_dsn"])
    if "log_level" in data and "CBS_LOG_LEVEL" not in os.environ:
        kwargs["log_level"] = str(data["log_level"])
    if "pg_pool_max" in data and "CBS_PG_POOL_MAX" not in os.environ:
        kwargs["pg_pool_max"] = int(data["pg_pool_max"])
    if "cache_ttl_fx" in data and "CBS_CACHE_TTL_FX" not in os.environ:
        kwargs["cache_ttl_fx"] = _parse_duration(data["cache_ttl_fx"])
    if "cache_ttl_product" in data and "CBS_CACHE_TTL_PRODUCT" not in os.environ:
        kwargs["cache_ttl_product"] = _parse_duration(data["cache_ttl_product"])
    if "cors_allowed_origins" in data and "CBS_CORS_ALLOWED_ORIGINS" not in os.environ:
        kwargs["cors_allowed_origins"] = str(data["cors_allowed_origins"])

    return CBSConfig(**kwargs)


def load_defaults() -> CBSConfig:
    """Load from env vars only (pydantic-settings default behaviour)."""
    return CBSConfig()


def apply_flags(cfg: CBSConfig, port: int | None, tb_addresses: str | None,
                 pg_dsn: str | None, log_level: str | None) -> CBSConfig:
    """Overlay CLI flag values onto config (non-empty / non-zero wins).

    Mirrors Go's ApplyFlags — zero/empty values are treated as "not set".
    """
    if port and port > 0:
        cfg.port = port
    if tb_addresses:
        cfg.tb_addresses = tb_addresses
    if pg_dsn:
        cfg.pg_dsn = pg_dsn
    if log_level:
        cfg.log_level = log_level
    return cfg


def _parse_duration(value: Any) -> int:
    """Parse a duration value (int seconds or string like '30s', '5m') to seconds."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.endswith("ms"):
        return int(float(s[:-2]) / 1000)
    if s.endswith("s"):
        return int(float(s[:-1]))
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    try:
        return int(s)
    except ValueError as e:
        raise ValueError(f"invalid duration {s!r}") from e


# Module-level convenience for env-only loading
def get_config() -> CBSConfig:
    """Load config from environment variables."""
    return load_defaults()
