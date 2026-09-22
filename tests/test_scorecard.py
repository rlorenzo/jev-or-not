from jev_or_not.scorecard import build_rulings, parse_rows

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
