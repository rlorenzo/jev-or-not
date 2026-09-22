from jev_or_not import ledger


def test_claim_once_and_successes(tmp_path):
    conn = ledger.open_ledger(str(tmp_path / "ledger.sqlite"))
    task_id = ledger.ensure_task(conn, "catalog", "ep-1", "fp-1")

    assert ledger.claim(conn, task_id) is True
    assert ledger.claim(conn, task_id) is False

    other_id = ledger.ensure_task(conn, "catalog", "ep-2", "fp-2")
    ledger.claim(conn, other_id)
    ledger.fail(conn, other_id, "boom")

    ledger.complete(conn, task_id, "data/episodes.jsonl")

    rows = ledger.successes(conn, "catalog")
    assert [r["task_id"] for r in rows] == [task_id]


def test_stale_claim_is_reclaimable(tmp_path):
    conn = ledger.open_ledger(str(tmp_path / "ledger.sqlite"))
    task_id = ledger.ensure_task(conn, "download", "ep-1", "fp-1")
    assert ledger.claim(conn, task_id) is True
    assert ledger.claim(conn, task_id) is False  # live claim holds
    assert ledger.claim(conn, task_id, stale_after_s=0) is True  # crashed run: reclaim


def test_task_status_reports_why_claim_declined(tmp_path):
    conn = ledger.open_ledger(str(tmp_path / "ledger.sqlite"))
    task_id = ledger.ensure_task(conn, "enroll", "ep-1", "fp-1")
    ledger.claim(conn, task_id)
    ledger.complete(conn, task_id, "out.json")

    assert ledger.claim(conn, task_id) is False
    assert ledger.task_status(conn, task_id) == "success"


def test_latest_successes_keeps_newest_existing_result_per_item(tmp_path):
    conn = ledger.open_ledger(str(tmp_path / "ledger.sqlite"))
    old, new, gone = (tmp_path / n for n in ("old.json", "new.json", "gone.json"))
    old.write_text("{}")
    new.write_text("{}")

    def success(item_id: str, fp: str, path, updated_at: str) -> str:
        task_id = ledger.ensure_task(conn, "enroll", item_id, fp)
        ledger.claim(conn, task_id)
        ledger.complete(conn, task_id, str(path))
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (updated_at, task_id))
        return task_id

    success("ep-1", "fp-old", old, "2026-01-01 00:00:00")
    newest = success("ep-1", "fp-new", new, "2026-01-02 00:00:00")
    success("ep-2", "fp-1", gone, "2026-01-03 00:00:00")  # result file was deleted

    rows = ledger.latest_successes(conn, "enroll")
    assert [r["task_id"] for r in rows] == [newest]


def test_latest_successes_same_second_tie_goes_to_later_row(tmp_path):
    conn = ledger.open_ledger(str(tmp_path / "ledger.sqlite"))
    out = tmp_path / "out.json"
    out.write_text("{}")

    task_ids = []
    for fp in ("fp-1", "fp-2"):
        task_id = ledger.ensure_task(conn, "enroll", "ep-1", fp)
        ledger.claim(conn, task_id)
        ledger.complete(conn, task_id, str(out))
        conn.execute(
            "UPDATE tasks SET updated_at = '2026-01-01 00:00:00' WHERE task_id = ?", (task_id,)
        )
        task_ids.append(task_id)

    rows = ledger.latest_successes(conn, "enroll")
    assert [r["task_id"] for r in rows] == [task_ids[-1]]
