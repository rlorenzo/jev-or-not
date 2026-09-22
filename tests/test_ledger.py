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
