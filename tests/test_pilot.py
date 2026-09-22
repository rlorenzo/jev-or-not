from jev_or_not.pilot import select_pilot

TITLES = [
    {"episode_id": "r/0", "episode": 0, "title": "Robot Origins", "duration_s": 100},
    {"episode_id": "r/1", "episode": 1, "title": "Is Robot", "duration_s": 100},
    {"episode_id": "r/6", "episode": 6, "title": "Robots Are Cool", "duration_s": 100},
    # 50 is intentionally missing -> nearest existing higher episode is 55.
    {"episode_id": "r/55", "episode": 55, "title": "Some Gadget", "duration_s": 200},
    {"episode_id": "r/100", "episode": 100, "title": "Robot Talk", "duration_s": 100},
    {"episode_id": "r/200", "episode": 200, "title": "Something Mysterious", "duration_s": 100},
    {"episode_id": "r/297", "episode": 297, "title": "Live Robot Show", "duration_s": 900},
    {"episode_id": "r/300", "episode": 300, "title": "Robot Party", "duration_s": 100},
    {"episode_id": "r/345", "episode": 345, "title": "Another Robot Day", "duration_s": 100},
    {"episode_id": "r/350", "episode": 350, "title": "Yet More Robots", "duration_s": 100},
    # Not a plan candidate; only in the pool for non-robot coverage replacement.
    {"episode_id": "r/400", "episode": 400, "title": "Curious Raft", "duration_s": 300},
]
RULINGS = [{"episode": 200}]  # episode 200 has scorecard coverage -> doesn't qualify


def test_substitutes_missing_candidate_with_nearest_higher():
    result = select_pilot(TITLES, RULINGS, seed=42)
    ep50 = next(e for e in result["episodes"] if e["reason"].startswith("substituted for 50"))
    assert ep50["episode"] == 55


def test_replaces_highest_nonpinned_candidate_for_non_robot_coverage():
    result = select_pilot(TITLES, RULINGS, seed=42)
    episodes = result["episodes"]

    # 200 has scorecard coverage so it doesn't count despite a non-robot title.
    ep200 = next(e for e in episodes if e["episode"] == 200)
    assert ep200["non_robot_candidate"] is False

    qualifying = [e for e in episodes if e["non_robot_candidate"]]
    assert len(qualifying) >= 2

    replaced = [e for e in episodes if e["reason"].startswith("replaced ")]
    assert replaced, "expected a replacement to satisfy non-robot coverage"
    assert replaced[0]["episode"] == 400
    assert replaced[0]["reason"] == "replaced 350 for non-robot coverage"

    # Pinned candidates are never replaced; the replaced episode (350) is gone.
    selected = {e["episode"] for e in episodes}
    assert {0, 1, 6, 297}.issubset(selected)
    assert 350 not in selected


def test_deterministic_with_fixed_seed():
    first = select_pilot(TITLES, RULINGS, seed=42)
    second = select_pilot(TITLES, RULINGS, seed=42)
    assert first == second
