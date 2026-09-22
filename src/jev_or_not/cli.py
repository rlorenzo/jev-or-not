import typer

from jev_or_not import pilot as pilot_module
from jev_or_not import scorecard as scorecard_module

app = typer.Typer(no_args_is_help=True)


def _not_implemented(force: bool = typer.Option(False, "--force")) -> None:
    typer.echo("not implemented")
    raise typer.Exit(1)


@app.command("pilot")
def pilot_cmd(force: bool = typer.Option(False, "--force")) -> None:
    """Phase 0 step 5: freeze the pilot episode list (no-op once frozen)."""
    summary = pilot_module.run(force=force)
    n = len(summary["pilot"]["episodes"])
    state = "already frozen" if summary["reused"] else "frozen"
    typer.echo(f"pilot: {n} episodes {state} ({summary['pilot']['total_duration_s']} s)")


@app.command("scorecard")
def scorecard_cmd(force: bool = typer.Option(False, "--force")) -> None:
    summary = scorecard_module.run(force=force)
    if summary["reused_verified"]:
        typer.echo(
            f"scorecard: {summary['rows']} rows written; "
            "verified scorecard unchanged (fingerprint match)"
        )
    else:
        typer.echo(
            f"scorecard: {summary['rows']} rows, {summary['rulings']} rulings written "
            f"(raw HTML: {summary['raw_html_path']})"
        )


for _name in (
    "catalog",
    "download",
    "transcribe",
    "extract",
    "questions",
    "rubrics",
    "predict",
    "evaluate",
    "export",
):
    app.command(_name)(_not_implemented)


if __name__ == "__main__":
    app()
