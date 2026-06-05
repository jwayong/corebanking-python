import typer

cli_app = typer.Typer()

@cli_app.command("serve")
def serve():
    """Start the API server (stub)."""
    print("Server started (stub)")

@cli_app.command("migrate")
def migrate(up: bool = True):
    """Run migrations (stub)."""
    print(f"Running migrations (up={up}) (stub)")

@cli_app.command("setup")
def setup():
    """Bootstrap the bank (stub)."""
    print("Setting up bank (stub)")

if __name__ == "__main__":
    cli_app()
