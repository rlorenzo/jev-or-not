import typer

from jev_or_not import catalog as catalog_module
from jev_or_not import download as download_module
from jev_or_not import pilot as pilot_module
from jev_or_not import scorecard as scorecard_module
from jev_or_not import transcribe as transcribe_module

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


@app.command("catalog")
def catalog_cmd(force: bool = typer.Option(False, "--force")) -> None:
    """Phase 1 steps 1-4: fetch feed + archive, build episodes.jsonl, cross-reference scorecard."""
    summary = catalog_module.run(force=force)
    state = "reused (fingerprint match)" if summary["reused"] else "rebuilt"
    typer.echo(
        f"catalog: {summary['episodes']} episodes {state}; "
        f"{summary['new_adjudication_entries']} adjudication entries appended "
        f"(raw fetch: {summary['local_dir']})"
    )


@app.command("download")
def download_cmd(
    pilot_only: bool = typer.Option(False, "--pilot-only"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Phase 1 step 5: download episode audio to local/audio/ (gitignored)."""
    summary = download_module.run(pilot_only=pilot_only, force=force)
    typer.echo(
        f"download: {summary['downloaded']} downloaded, {summary['skipped']} skipped, "
        f"{summary['failed']} failed of {summary['candidates']} candidates "
        f"({summary['bytes_downloaded']} bytes); manifest: {summary['manifest_path']} "
        f"({summary['manifest_rows']} rows, {len(summary['mismatches'])} duration mismatches)"
    )
    for r in summary["failures"]:
        typer.echo(f"  FAILED {r['episode_label']} ({r['reason']}): {r['error']}")


def _transcribe_progress(r: dict) -> None:
    """One line per episode as it finishes; the run can take hours."""
    who = f"{r['episode_label']} ({r['candidate']})"
    if r["status"] == "success":
        rtf = f"{r['rtf']:.3f}" if r["rtf"] is not None else "n/a"
        typer.echo(f"{who}: {r['wall_s']:.1f}s wall, rtf={rtf}")
    elif r["status"] == "failed":
        typer.echo(f"{who}: FAILED ({r['reason']})")
    else:
        typer.echo(f"{who}: skipped ({r['reason']})")


@app.command("transcribe")
def transcribe_cmd(
    candidate: str = typer.Option(..., "--candidate", help="A or B"),
    pilot_only: bool = typer.Option(True, "--pilot-only/--no-pilot-only"),
    all_episodes: bool = typer.Option(False, "--all"),
    episodes: str | None = typer.Option(None, "--episodes", help="comma-separated episode numbers"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Phase 2: drive the transcription worker per episode with fingerprint reuse."""
    summary = transcribe_module.run(
        candidate=candidate,
        pilot_only=pilot_only,
        all_=all_episodes,
        episodes_arg=episodes,
        force=force,
        on_result=_transcribe_progress,
    )
    rtf_str = f"{summary['overall_rtf']:.3f}" if summary["overall_rtf"] is not None else "n/a"
    projected_str = (
        f"{summary['projected_full_s']:.1f}s" if summary["projected_full_s"] is not None else "n/a"
    )
    typer.echo(
        f"transcribe: {summary['success']} succeeded, {summary['skipped']} skipped, "
        f"{summary['failed']} failed of {summary['episodes']} episodes; "
        f"wall={summary['total_wall_s']:.1f}s audio={summary['total_audio_s']:.1f}s rtf={rtf_str}; "
        f"projected full catalog ({summary['catalog_total_s']}s): {projected_str}; "
        f"index: {summary['index_path']} ({summary['index_rows']} rows)"
    )
    for r in summary["failures"]:
        typer.echo(f"  FAILED {r['episode_label']} ({r['candidate']}): {r['reason']}")


for _name in (
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
