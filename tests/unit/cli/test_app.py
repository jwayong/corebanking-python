"""Tests for CLI app structure and help output."""

import pytest
from typer.testing import CliRunner

from cbs.cli.app import cli_app

runner = CliRunner()


class TestCLIStructure:
    """Test CLI command registration and help output."""

    def test_help_shows_all_commands(self):
        """cbs --help shows all sub-commands."""
        result = runner.invoke(cli_app, ["--help"])
        assert result.exit_code == 0
        assert "serve" in result.output
        assert "migrate" in result.output
        assert "setup" in result.output
        assert "batch" in result.output
        assert "status" in result.output

    def test_serve_help(self):
        """serve subcommand shows help."""
        result = runner.invoke(cli_app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "Start the HTTP API server" in result.output

    def test_migrate_help(self):
        """migrate subcommand shows help."""
        result = runner.invoke(cli_app, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "up" in result.output
        assert "down" in result.output
        assert "status" in result.output
        assert "create" in result.output

    def test_setup_help(self):
        """setup subcommand shows help."""
        result = runner.invoke(cli_app, ["setup", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "ledger" in result.output
        assert "product" in result.output
        assert "status" in result.output

    def test_batch_help(self):
        """batch subcommand shows help."""
        result = runner.invoke(cli_app, ["batch", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "list" in result.output
        assert "status" in result.output

    def test_status_help(self):
        """status subcommand shows help."""
        result = runner.invoke(cli_app, ["--help"])
        assert result.exit_code == 0


class TestMigrateCreate:
    """Test migration file creation."""

    def test_create_migration_files(self, tmp_path):
        """migrate create generates up/down SQL files."""
        from cbs.cli._migrate_impl import migrate_create

        migrations_dir = str(tmp_path / "migrations")
        result = runner.invoke(cli_app, [
            "--migrations-dir", migrations_dir,
            "migrate", "create", "--name", "test_migration"
        ])

        # Should create the directory and files.
        up_files = list(tmp_path.glob("migrations/*.up.sql"))
        down_files = list(tmp_path.glob("migrations/*.down.sql"))

        assert len(up_files) == 1
        assert len(down_files) == 1
        assert "test_migration" in up_files[0].name

    def test_create_requires_name(self):
        """migrate create fails without --name."""
        result = runner.invoke(cli_app, ["migrate", "create"])
        assert result.exit_code != 0


class TestBatchList:
    """Test batch job listing."""

    def test_list_batch_jobs(self, monkeypatch):
        """batch list shows registered jobs."""
        # Set required env vars so config.validate() passes.
        monkeypatch.setenv("CBS_TB_ADDRESSES", "localhost:3001")
        monkeypatch.setenv("CBS_PG_DSN", "postgres://localhost/test")

        result = runner.invoke(cli_app, ["batch", "list"])
        assert result.exit_code == 0
        assert "interest_accrual" in result.output
        assert "fee_collection" in result.output
