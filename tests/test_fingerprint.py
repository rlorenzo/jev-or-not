from jev_or_not.fingerprint import fingerprint


def test_fingerprint_stable_across_kwarg_order():
    a = fingerprint(episode_id="362", model="jev-v1", seed=42)
    b = fingerprint(seed=42, episode_id="362", model="jev-v1")
    assert a == b
