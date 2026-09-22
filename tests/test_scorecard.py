from jev_or_not.models import AdjudicationEntry
from jev_or_not.scorecard import apply_adjudication, build_rulings, parse_rows

FIXTURE_HTML = """
<html><body>
<ul class="list">
<li><a href="https://en.wikipedia.org/wiki/Widget">Widget</a>
<a href="https://www.theincomparable.com/robot/1/" class="ep">Episode 1</a>
<span class="robot"><b>&#10004;&#65038;</b></span></li>
<li><a href="https://en.wikipedia.org/wiki/Widget">Widget</a>
<a href="https://www.theincomparable.com/robot/1/" class="ep">Episode 1</a>
<span class="robot"><b>&#10004;&#65038;</b></span></li>
<li><a href="https://en.wikipedia.org/wiki/Robot_(dance)">The Robot (dance)</a>
<a href="https://www.theincomparable.com/robot/0/" class="ep">Episode 0</a>
<span class="robot"><b>&#10004;&#65038;</b></span></li>
<li><a href="https://en.wikipedia.org/wiki/Robot_(dance)">The Robot (dance)</a>
<a href="https://www.theincomparable.com/robot/1/" class="ep">Episode 1</a>
<span class="not"><b>&#10008;</b></span></li>
<li><a href="https://en.wikipedia.org/wiki/Mystery">Mystery Thing</a>
<a href="https://www.theincomparable.com/robot/2/" class="ep">Episode 2</a>
<span class=""><b>?</b></span></li>
</ul>
</body></html>
"""


def test_parse_rows_no_network():
    rows = parse_rows(FIXTURE_HTML, "2026-01-01T00:00:00+00:00", "deadbeef")
    assert len(rows) == 5
    assert rows[0].item == "Widget"
    assert rows[0].episode == 1
    assert rows[0].verdict == "robot"
    assert rows[4].verdict == "unresolved"


def test_verdict_mapping_and_collapse():
    rows = parse_rows(FIXTURE_HTML, "2026-01-01T00:00:00+00:00", "deadbeef")
    rulings = build_rulings(rows)

    widget = [r for r in rulings if r.subject == "Widget"]
    assert len(widget) == 1
    assert widget[0].label == "yes"
    assert widget[0].review_status == "auto"
    assert len(widget[0].source_row_ids) == 2  # within-episode duplicate collapsed

    mystery = [r for r in rulings if r.subject == "Mystery Thing"]
    assert len(mystery) == 1
    assert mystery[0].label == "unresolved"


def test_cross_episode_repeat_yields_two_rulings():
    rows = parse_rows(FIXTURE_HTML, "2026-01-01T00:00:00+00:00", "deadbeef")
    rulings = build_rulings(rows)

    dance = [r for r in rulings if "Robot (dance)" in r.subject]
    assert len(dance) == 2
    labels_by_episode = {r.episode: r.label for r in dance}
    assert labels_by_episode == {0: "yes", 1: "no"}


def _row_html(subject: str, episode: int) -> str:
    return (
        f'<li><a href="https://example.com/{episode}">{subject}</a>'
        f'<a href="https://www.theincomparable.com/robot/{episode}/" class="ep">'
        f'Episode {episode}</a><span class="not"><b>&#10008;</b></span></li>'
    )


# The scorecard is fan-maintained, so a mistyped episode link (e.g. the known
# "Episode 263" row, which sits between episodes 22 and 23 and is probably 23)
# has to be caught by page-order position, not by episode number.
OUT_OF_ORDER_HTML = (
    '<ul class="list">'
    + "".join(
        _row_html(subject, episode)
        for subject, episode in [
            ("Alpha", 20),
            ("Beta", 22),
            ("Synth", 263),
            ("Gamma", 23),
            ("Delta", 25),
        ]
    )
    + "</ul>"
)


def test_out_of_page_order_episode_is_quarantined():
    rows = parse_rows(OUT_OF_ORDER_HTML, "2026-01-01T00:00:00+00:00", "deadbeef")
    rulings = {r.subject: r for r in build_rulings(rows)}

    assert rulings["Synth"].review_status == "quarantined"
    assert "out of page order" in rulings["Synth"].quarantine_reason
    # in-order neighbours are untouched, including the ones flanking the typo
    assert [r.review_status for s, r in rulings.items() if s != "Synth"] == ["auto"] * 4


def test_apply_adjudication_overrides_field_and_records_reason():
    rows = parse_rows(OUT_OF_ORDER_HTML, "2026-01-01T00:00:00+00:00", "deadbeef")
    rulings = build_rulings(rows)
    synth = next(r for r in rulings if r.subject == "Synth")

    apply_adjudication(
        rulings,
        [
            AdjudicationEntry(
                schema_version=1,
                source_row_id=synth.source_row_ids[0],
                field="episode",
                old=263,
                new=23,
                reason="link text typo: 263 should be 23",
                decided_by="tester",
                decided_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )
    assert synth.episode == 23
    assert synth.correction_reason == "link text typo: 263 should be 23"
