import json

import pytest

from jev_or_not.common import (
    episode_sort_key,
    page_title,
    parse_duration,
    read_jsonl,
    write_json_atomic,
    write_lines_atomic,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01:02:03", 3723),
        ("12:30", 750),
        ("200", 200),
        ("  90  ", 90),
        ("", None),
        (None, None),
        ("1234.5", 1234),  # some feeds publish fractional seconds
        ("12:34.5", 754),
        ("not a duration", None),
    ],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


def test_page_title_strips_site_suffix():
    assert page_title("<TITLE>5: Widgets - The Incomparable</TITLE>", "x") == "5: Widgets"
    assert page_title("<title>\n  Bare\n</title>", "x") == "Bare"
    assert page_title("<p>no title here</p>", "fallback") == "fallback"


def test_episode_sort_key_puts_unnumbered_last():
    entries = [(None, "62b"), (9, "9"), (None, "12a"), (1, "1")]
    assert sorted(entries, key=lambda e: episode_sort_key(*e)) == [
        (1, "1"),
        (9, "9"),
        (None, "12a"),
        (None, "62b"),
    ]


def test_jsonl_roundtrip_skips_blank_lines(tmp_path):
    path = tmp_path / "nested" / "rows.jsonl"
    write_lines_atomic(path, (json.dumps({"n": n}) for n in range(3)))

    assert list(read_jsonl(path)) == [{"n": 0}, {"n": 1}, {"n": 2}]

    path.write_text('{"n": 0}\n\n  \n{"n": 1}\n')
    assert list(read_jsonl(path)) == [{"n": 0}, {"n": 1}]


def test_atomic_writes_leave_no_temp_file_on_failure(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("original\n")

    def boom():
        yield "fine"
        raise RuntimeError("mid-write failure")

    with pytest.raises(RuntimeError):
        write_lines_atomic(path, boom())

    assert path.read_text() == "original\n"  # target untouched
    assert list(tmp_path.iterdir()) == [path]  # temp file cleaned up


def test_write_json_atomic(tmp_path):
    path = tmp_path / "out.json"
    write_json_atomic(path, {"b": 1, "a": 2})
    assert json.loads(path.read_text()) == {"b": 1, "a": 2}
    assert path.read_text().endswith("\n")
