import typer

cli_app = typer.Typer()
migrate_app = typer.Typer()

@migrate_app.command("up")
def migrate_up():
    """Run migrations up (stub)."""
    print("Running migrations up (stub)")

@migrate_app.command("down")
def migrate_down():
    """Run migrations down (stub)."""
    print("Running migrations down (stub)")

cli_app.add_typer(migrate_app, name="migrate")

@cli_app.command("serve")
def serve():
    """Start the API server (stub)."""
    print("Server started (stub)")

@cli_app.command("setup")
def setup():
    """Bootstrap the bank (stub)."""
    print("Setting up bank (stub)")

if __name__ == "__main__":
    cli_app()
