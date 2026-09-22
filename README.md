# jev-or-not

A reproducible evaluation comparing John Siracusa's rulings on the podcast
*Robot or Not?* (The Incomparable, hosted by John Siracusa and Jason Snell)
against yes/no predictions from TypeSafe AI's Jev, a "System One" model that
returns probabilities instead of text. Calibration is an outcome to measure,
not an assumption.

## Ground rules

- Transcripts and audio are copyrighted by The Incomparable. Audio and full
  transcripts stay local and gitignored. Only derived data (verdicts, short
  evidence quotes under 15 words, generated questions, predictions) may be
  committed or published.
- Secrets live in `.env` (see `.env.example`) and are never committed.

## Usage

```sh
uv sync
uv run jev-or-not --help
uv run pytest
```

Lint: `uv run pre-commit run --all-files`
