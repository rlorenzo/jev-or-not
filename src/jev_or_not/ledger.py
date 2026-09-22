"""SQLite task ledger for resumable, fingerprint-based phase execution.

See PLAN.md "Reproducibility and recovery": one shared `tasks` table for all
phases. The ledger is a rebuildable cache, not a published artifact.
"""

import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

DEFAULT_PATH = "local/ledger.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    item_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'success', 'failed', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    provider_request_id TEXT,
    error_detail TEXT,
    result_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_phase_item_fingerprint
    ON tasks (phase, item_id, fingerprint);
"""


def open_ledger(path: str = DEFAULT_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def ensure_task(conn: sqlite3.Connection, phase: str, item_id: str, fingerprint: str) -> str:
    """Return the task_id for (phase, item_id, fingerprint), creating it if absent."""
    row = conn.execute(
        "SELECT task_id FROM tasks WHERE phase = ? AND item_id = ? AND fingerprint = ?",
        (phase, item_id, fingerprint),
    ).fetchone()
    if row:
        return row[0]
    task_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tasks (task_id, phase, item_id, fingerprint, status) "
        "VALUES (?, ?, ?, ?, 'pending')",
        (task_id, phase, item_id, fingerprint),
    )
    conn.commit()
    return task_id


STALE_CLAIM_SECONDS = 3600  # a claim older than this is a crashed run, not a live one


def claim(conn: sqlite3.Connection, task_id: str, stale_after_s: int = STALE_CLAIM_SECONDS) -> bool:
    """Atomically claim a task. True if this call won the claim.

    Stale claims (a process that died between claim and complete/fail) are re-claimable.
    """
    cur = conn.execute(
        "UPDATE tasks SET status = 'claimed', attempts = attempts + 1, "
        "claimed_at = datetime('now'), updated_at = datetime('now') "
        "WHERE task_id = ? AND (status IN ('pending', 'failed') "
        "OR (status = 'claimed' AND claimed_at <= datetime('now', ?)))",
        (task_id, f"-{stale_after_s} seconds"),
    )
    conn.commit()
    return cur.rowcount > 0


def task_status(conn: sqlite3.Connection, task_id: str) -> str:
    """The task's current status, e.g. why ``claim`` just declined it."""
    return conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0]


def complete(conn: sqlite3.Connection, task_id: str, result_path: str) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'success', result_path = ?, updated_at = datetime('now') "
        "WHERE task_id = ?",
        (result_path, task_id),
    )
    conn.commit()


def fail(conn: sqlite3.Connection, task_id: str, error_detail: str) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'failed', error_detail = ?, updated_at = datetime('now') "
        "WHERE task_id = ?",
        (error_detail, task_id),
    )
    conn.commit()


def successes(conn: sqlite3.Connection, phase: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT rowid, * FROM tasks WHERE phase = ? AND status = 'success' "
        "ORDER BY updated_at, rowid",
        (phase,),
    ).fetchall()


def _result_exists(task: sqlite3.Row) -> bool:
    return Path(task["result_path"]).exists()


def latest_successes(
    conn: sqlite3.Connection,
    phase: str,
    exists: Callable[[sqlite3.Row], bool] = _result_exists,
) -> list[sqlite3.Row]:
    """The newest success per item_id whose result still ``exists``.

    A re-run (--force, or a changed fingerprint) leaves an older success row
    for the same item_id behind; exporting both would duplicate that item.
    The ledger is a rebuildable cache, so a missing result just drops the row.
    ``updated_at`` has 1-second resolution, so ties go to the later rowid.
    """
    latest: dict[str, sqlite3.Row] = {}
    for task in successes(conn, phase):
        if not exists(task):
            continue
        prev = latest.get(task["item_id"])
        if prev is None or (task["updated_at"], task["rowid"]) > (
            prev["updated_at"],
            prev["rowid"],
        ):
            latest[task["item_id"]] = task
    return list(latest.values())
