import json
from pathlib import Path

from jev_or_not.catalog import build_episodes, cross_reference, parse_feed_items

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
<title>Test Feed</title>
<item>
<title>5: Widgets</title>
<itunes:episode>5</itunes:episode>
<link>https://www.theincomparable.com/robot/5/</link>
<description>Are widgets robots? &lt;p&gt;Desc five.&lt;/p&gt;</description>
<enclosure url="https://example.com/audio5.mp3" type="audio/mpeg" length="1" />
<itunes:duration>01:02:03</itunes:duration>
<guid isPermaLink="false">theincomparable/robot/5</guid>
<pubDate>Wed, 10 Jun 2015 22:29:41 GMT</pubDate>
</item>
<item>
<title>62b: Bonus Round</title>
<itunes:episode>62b</itunes:episode>
<link>https://www.theincomparable.com/robot/62b/</link>
<description>A live bonus episode.</description>
<enclosure url="https://example.com/audio62b.mp3" type="audio/mpeg" length="1" />
<itunes:duration>200</itunes:duration>
<guid isPermaLink="false">theincomparable/robot/62b</guid>
<pubDate>Sat, 18 Jun 2016 15:56:58 GMT</pubDate>
</item>
<item>
<title>9: Conflicted</title>
<itunes:episode>9</itunes:episode>
<link>https://www.theincomparable.com/robot/12/</link>
<description>Numbers disagree on purpose.</description>
<enclosure url="https://example.com/audio9.mp3" type="audio/mpeg" length="1" />
<itunes:duration>150</itunes:duration>
<guid isPermaLink="false">theincomparable/robot/9</guid>
<pubDate>Wed, 10 Jun 2015 22:29:41 GMT</pubDate>
</item>
<item>
<title>99: Wrong Digits Title</title>
<itunes:episode>42</itunes:episode>
<link>https://www.theincomparable.com/other-page/</link>
<description>itunes:episode should win over a disagreeing title.</description>
<enclosure url="https://example.com/audio42.mp3" type="audio/mpeg" length="1" />
<itunes:duration>100</itunes:duration>
<guid isPermaLink="false">theincomparable/robot/42</guid>
<pubDate>Wed, 10 Jun 2015 22:29:41 GMT</pubDate>
</item>
</channel>
</rss>
"""

RETRIEVED_AT = "2026-01-01T00:00:00+00:00"


def _no_fetch(episode, local_dir):
    raise AssertionError(f"should not fetch archive-only page for episode {episode}")


def test_guid_based_episode_id():
    items = parse_feed_items(FEED_XML, RETRIEVED_AT)
    episodes, _ = build_episodes(items, {5, 9, 42}, RETRIEVED_AT, Path("."), fetch_page=_no_fetch)
    five = next(e for e in episodes if e.episode == 5)
    assert five.episode_id == "theincomparable/robot/5"
    assert five.source == "both"


def test_itunes_episode_precedence_over_url_and_title():
    items = parse_feed_items(FEED_XML, RETRIEVED_AT)
    episodes, _ = build_episodes(items, {5, 9, 42}, RETRIEVED_AT, Path("."), fetch_page=_no_fetch)

    conflict_url = next(e for e in episodes if e.episode_id == "theincomparable/robot/9")
    assert conflict_url.episode == 9  # itunes:episode wins over URL's /robot/12/
    assert conflict_url.blocked is True
    assert any("canonical URL" in r for r in conflict_url.review_queue)

    conflict_title = next(e for e in episodes if e.episode_id == "theincomparable/robot/42")
    assert conflict_title.episode == 42  # itunes:episode wins over title's "99:"
    assert conflict_title.blocked is True
    assert any("title pattern" in r for r in conflict_title.review_queue)


def test_bonus_episode_62b():
    items = parse_feed_items(FEED_XML, RETRIEVED_AT)
    episodes, _ = build_episodes(items, {5, 9, 42}, RETRIEVED_AT, Path("."), fetch_page=_no_fetch)
    bonus = next(e for e in episodes if e.episode_id == "theincomparable/robot/62b")
    assert bonus.episode is None
    assert bonus.episode_label == "62b"
    assert bonus.blocked is False
    assert bonus.review_queue == []
    assert bonus.source == "feed"  # not in the archive number set


def test_hhmmss_duration_and_html_stripped_description():
    items = parse_feed_items(FEED_XML, RETRIEVED_AT)
    five = next(i for i in items if i["guid"] == "theincomparable/robot/5")
    assert five["duration_s"] == 3723  # 01:02:03
    assert five["description"] == "Are widgets robots? Desc five."


def test_archive_only_entry_fetches_episode_page():
    items = parse_feed_items(FEED_XML, RETRIEVED_AT)

    def fake_fetch(episode, local_dir):
        assert episode == 20
        return (
            {
                "canonical_url": "https://www.theincomparable.com/robot/20/",
                "title": "Archive Only Episode",
                "audio_url": "https://example.com/audio20.mp3",
                "description": "Only on the archive.",
                "released_at": "2016-01-01T00:00:00+00:00",
            },
            "fakehash",
        )

    episodes, page_hashes = build_episodes(
        items, {5, 9, 20, 42}, RETRIEVED_AT, Path("."), fetch_page=fake_fetch
    )
    archive_only = next(e for e in episodes if e.episode == 20)
    assert archive_only.source == "archive"
    assert archive_only.episode_id == "archive:https://www.theincomparable.com/robot/20/"
    assert archive_only.duration_s is None
    assert archive_only.audio_url == "https://example.com/audio20.mp3"
    assert page_hashes == {"20": "fakehash"}


def _episode(episode_id, episode):
    from jev_or_not.models import Episode

    return Episode(
        schema_version=1,
        episode_id=episode_id,
        episode=episode,
        episode_label=str(episode),
        title="t",
        released_at=RETRIEVED_AT,
        description="d",
        show_notes_url="u",
        audio_url="a",
        duration_s=1,
        source="both",
        retrieved_at=RETRIEVED_AT,
    )


def test_cross_reference_flags_unmatched_row_once_and_is_idempotent(tmp_path):
    episodes = [_episode("theincomparable/robot/5", 5)]
    scorecard_path = tmp_path / "scorecard_verified.jsonl"
    scorecard_path.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "schema_version": 1,
                    "ruling_ref": "sc-5-widget",
                    "episode_id": "theincomparable/robot/5",
                    "episode": 5,
                    "subject": "Widget",
                    "label": "yes",
                    "source_row_ids": ["row-5"],
                    "review_status": "auto",
                },
                {
                    "schema_version": 1,
                    "ruling_ref": "sc-777-ghost",
                    "episode_id": "theincomparable/robot/777",
                    "episode": 777,
                    "subject": "Ghost",
                    "label": "no",
                    "source_row_ids": ["row-777"],
                    "review_status": "auto",
                },
                {
                    "schema_version": 1,
                    "ruling_ref": "sc-778-already-quarantined",
                    "episode_id": None,
                    "episode": 778,
                    "subject": "Skip me",
                    "label": "no",
                    "source_row_ids": ["row-778"],
                    "review_status": "quarantined",
                },
            ]
        )
        + "\n"
    )
    adjudication_path = tmp_path / "adjudication.jsonl"

    result1 = cross_reference(
        episodes,
        "20260101T000000Z",
        scorecard_path=scorecard_path,
        adjudication_path=adjudication_path,
    )
    assert len(result1["new_entries"]) == 1
    assert result1["new_entries"][0]["source_row_id"] == "row-777"
    assert result1["new_entries"][0]["reason"] == "scorecard episode 777 has no catalog episode"
    assert result1["new_entries"][0]["new"] == "quarantined"

    lines_after_first = adjudication_path.read_text().splitlines()
    assert len(lines_after_first) == 2  # header + 1 new entry

    result2 = cross_reference(
        episodes,
        "20260102T000000Z",
        scorecard_path=scorecard_path,
        adjudication_path=adjudication_path,
    )
    assert result2["new_entries"] == []
    lines_after_second = adjudication_path.read_text().splitlines()
    assert lines_after_second == lines_after_first  # unchanged, no duplicate appended


def test_cross_reference_with_no_scorecard_file_is_a_noop(tmp_path):
    adjudication_path = tmp_path / "adjudication.jsonl"
    result = cross_reference(
        [_episode("theincomparable/robot/263", 263)],
        "20260101T000000Z",
        scorecard_path=tmp_path / "does-not-exist.jsonl",
        adjudication_path=adjudication_path,
    )
    assert result == {"unmatched_rulings": [], "new_entries": []}
    # the adjudication file is still created, with only its header record
    assert len(adjudication_path.read_text().splitlines()) == 1
