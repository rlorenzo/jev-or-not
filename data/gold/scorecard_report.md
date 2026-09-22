# Scorecard report

## Counts
- rows: 174
- distinct episodes: 114
- label distribution: {'no': 121, 'yes': 51, 'unresolved': 2}
- unresolved rulings: 2

## Subjects duplicated across episodes
- karr (knight rider): ep 0: no, ep 11: no
- the robot (dance): ep 0: yes, ep 1: no, ep 100: no
- telepresence robot: ep 0: no, ep 7: no
- roomba: ep 0: yes, ep 8: yes
- siri: ep 0: no, ep 16: no
- synth (humans): ep 58: yes, ep 263: no
- thomas the tank engine: ep 39: no, ep 47: no

## Quarantined rulings
- sc-263-synth-humans (episode 263, subject 'Synth (Humans)'): episode 263 out of page order by more than 50 relative to neighbors (22, 23)

## Excluded rulings (adjudicated)
- sc-0-clippy: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json
- sc-0-karr-knight-rider: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json
- sc-0-the-robot-dance: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json
- sc-0-telepresence-robot: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json
- sc-0-roomba: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json
- sc-0-siri: episode 0 excluded from the evaluation; see data/gold/excluded_episodes.json

## "Episode 263" row(s)
```json
{"schema_version":1,"source_row_id":"9eed2915ef9e-1","row_index":49,"item":"Synth (Humans)","episode":263,"episode_link_text":"Episode 263","verdict":"not_robot","verdict_raw":"not:✘","reference_url":"https://en.wikipedia.org/wiki/Humans_(TV_series)","episode_url":"https://www.theincomparable.com/robot/263/","retrieved_at":"2026-09-22T05:43:47.960785+00:00","source_html_hash":"c1cbf470bed625901dfd1c601cf062d8771a720043671fda64217f659399d47a"}
```
