"""Tests for CLI configuration loading."""

import os
from pathlib import Path

import pytest

from cbs.config import (
    CBSConfig,
    apply_flags,
    load_defaults,
    load_from_file,
    _parse_duration,
)


class TestCBSConfig:
    """Test CBSConfig defaults and validation."""

    def test_defaults(self):
        cfg = CBSConfig()
        assert cfg.port == 8080
        assert cfg.log_level == "info"
        assert cfg.pg_pool_max == 10
        assert cfg.tb_addresses == ""
        assert cfg.pg_dsn == ""

    def test_validate_requires_tb_and_pg(self):
        cfg = CBSConfig()
        with pytest.raises(ValueError, match="TigerBeetle"):
            cfg.validate()

    def test_validate_passes_with_connections(self):
        cfg = CBSConfig(tb_addresses="localhost:3001", pg_dsn="postgres://localhost/db")
        cfg.validate()  # Should not raise

    def test_env_var_loading(self, monkeypatch):
        monkeypatch.setenv("CBS_TB_ADDRESSES", "tb1:3001,tb2:3001")
        monkeypatch.setenv("CBS_PG_DSN", "postgres://user:pass@localhost/db")
        monkeypatch.setenv("CBS_PORT", "9000")
        monkeypatch.setenv("CBS_LOG_LEVEL", "debug")

        cfg = load_defaults()
        assert cfg.tb_addresses == "tb1:3001,tb2:3001"
        assert cfg.pg_dsn == "postgres://user:pass@localhost/db"
        assert cfg.port == 9000
        assert cfg.log_level == "debug"


class TestApplyFlags:
    """Test CLI flag overlay on config."""

    def test_flags_override_defaults(self):
        cfg = CBSConfig()
        cfg = apply_flags(cfg, port=9090, tb_addresses="localhost:3001", pg_dsn="postgres://x", log_level="debug")
        assert cfg.port == 9090
        assert cfg.tb_addresses == "localhost:3001"
        assert cfg.pg_dsn == "postgres://x"
        assert cfg.log_level == "debug"

    def test_zero_port_ignored(self):
        cfg = CBSConfig(port=8080)
        cfg = apply_flags(cfg, port=0, tb_addresses=None, pg_dsn=None, log_level=None)
        assert cfg.port == 8080  # Unchanged

    def test_empty_string_ignored(self):
        cfg = CBSConfig(tb_addresses="original:3001")
        cfg = apply_flags(cfg, port=None, tb_addresses="", pg_dsn="", log_level="")
        assert cfg.tb_addresses == "original:3001"  # Unchanged


class TestLoadFromFile:
    """Test YAML config file loading."""

    def test_load_from_yaml_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "port: 9090\n"
            "tb_addresses: tb1:3001,tb2:3001\n"
            "pg_dsn: postgres://user:pass@localhost/db\n"
            "log_level: debug\n"
            "pg_pool_max: 20\n"
        )

        cfg = load_from_file(str(config_file))
        assert cfg.port == 9090
        assert cfg.tb_addresses == "tb1:3001,tb2:3001"
        assert cfg.pg_dsn == "postgres://user:pass@localhost/db"
        assert cfg.log_level == "debug"
        assert cfg.pg_pool_max == 20

    def test_load_from_yaml_with_list_addresses(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "port: 8080\n"
            "tb_addresses:\n"
            "  - tb1:3001\n"
            "  - tb2:3001\n"
            "pg_dsn: postgres://localhost/db\n"
        )

        cfg = load_from_file(str(config_file))
        assert cfg.tb_addresses == "tb1:3001,tb2:3001"

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "port: 8080\n"
            "tb_addresses: file:3001\n"
            "pg_dsn: postgres://file/db\n"
        )

        monkeypatch.setenv("CBS_TB_ADDRESSES", "env:3001")

        cfg = load_from_file(str(config_file))
        # Env vars should override file values.
        assert cfg.tb_addresses == "env:3001"
        # File values should be used where no env var is set.
        assert cfg.pg_dsn == "postgres://file/db"


class TestParseDuration:
    """Test duration string parsing."""

    def test_parse_int(self):
        assert _parse_duration(30) == 30

    def test_parse_float(self):
        assert _parse_duration(30.5) == 30

    def test_parse_seconds(self):
        assert _parse_duration("30s") == 30

    def test_parse_minutes(self):
        assert _parse_duration("5m") == 300

    def test_parse_hours(self):
        assert _parse_duration("1h") == 3600

    def test_parse_milliseconds(self):
        assert _parse_duration("500ms") == 0

    def test_parse_plain_int_string(self):
        assert _parse_duration("60") == 60

    def test_parse_invalid(self):
        with pytest.raises(ValueError, match="invalid duration"):
            _parse_duration("abc")
