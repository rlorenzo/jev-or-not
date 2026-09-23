"""Pilot reference annotation desk (docs/annotation_procedure.md).

``jev-or-not annotate`` serves a single-page tool on 127.0.0.1 for building
``data/gold/pilot_reference.jsonl``. The docket works in two stages:

* **Open record.** Each pilot episode is a session. The reference is
  *pooled*: every extraction run's rulings plus the scorecard's subjects are
  merged into blind candidates (``review_pool``), and Rex approves, fixes,
  or rejects each one against its audio span, adding any ruling no source
  found. Every decision is logged to ``data/gold/pilot_review.jsonl``;
  approved and added rulings become validated ``PilotReference`` rows. A
  session closes once no candidate is pending.
* **Sealed record.** When all sessions are closed, the record is sealed at
  1.0.0 (``data/gold/pilot_reference.seal.json``). Rows are then never edited
  in place: a correction is a superseding row plus an adjudication entry and
  a ``reference_version`` bump. Scorecard disagreements and the extraction
  review unlock only after the seal, so neither can leak into annotation.

The ruling number ``k`` in ``<episode_id>#<k>`` is issued from a per-session
counter and never reused, even after a pre-seal delete.
"""

import http.server
import json
import mimetypes
import re
import socketserver
import subprocess  # nosec B404 - fixed ffmpeg argv below, no shell
import threading
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import ValidationError

from jev_or_not.catalog import ADJUDICATION_PATH
from jev_or_not.common import read_jsonl, write_json_atomic, write_lines_atomic
from jev_or_not.download import MANIFEST_PATH
from jev_or_not.extract import (
    ENROLLED_INDEX_PATH,
    MODEL_ALIASES,
    TRANSCRIPT_INDEX_PATH,
    VERDICTS_PATH,
    index_rows,
)
from jev_or_not.fingerprint import file_hash
from jev_or_not.models import AdjudicationEntry, PilotReference
from jev_or_not.pilot import PILOT_PATH, SCORECARD_VERIFIED_PATH
from jev_or_not.review_pool import pool
from jev_or_not.scorecard import ensure_adjudication_file, read_adjudication_entries

SCHEMA_VERSION = 1
REFERENCE_PATH = Path("data/gold/pilot_reference.jsonl")
SEAL_PATH = Path("data/gold/pilot_reference.seal.json")
REVIEW_PATH = Path("data/gold/pilot_review.jsonl")
STATE_PATH = Path("local/annotate/state.json")
PEAKS_DIR = Path("local/annotate/peaks")
UI_PATH = Path(__file__).parent / "annotate_ui" / "index.html"
FONTS_DIR = UI_PATH.parent / "fonts"  # vendored Public Sans (OFL), no CDN at runtime

ANNOTATOR = "Rex Lorenzo"  # matches decided_by in data/gold/adjudication.jsonl
SEAL_VERSION = "1.0.0"
TRANSCRIPT_CANDIDATE = "B"  # transcript shown for finding missed rulings
PEAKS_PER_S = 20
PEAKS_RATE_HZ = 4000
EDITABLE_FIELDS = (
    "subject",
    "category",
    "verdict",
    "verdict_strength",
    "ruling_start_s",
    "ruling_end_s",
    "speaker",
)


# Every session's stored state starts from these; see Desk._session_state.
SESSION_DEFAULTS = {"status": "open", "next_k": 1, "no_rulings": False, "resume_s": 0.0}
_MODEL_BY_ID = {model_id: alias for alias, model_id in MODEL_ALIASES.items()}


class DeskError(Exception):
    """A request the desk refuses; ``status`` is the HTTP status to send."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_sha256(path: Path) -> str | None:
    return file_hash(str(path)) if path.exists() else None


def _bump_minor(version: str) -> str:
    major, minor, _ = (int(p) for p in version.split("."))
    return f"{major}.{minor + 1}.0"


def _ruling_k(ruling_id: str) -> int:
    return int(ruling_id.rpartition("#")[2])


class _PoolSources(NamedTuple):
    verdicts_by_episode: dict[str, list[dict]]  # each tagged with its blind ``run`` key
    runs_total: int
    scorecard: list[dict]
    review_log: list[dict]


def _pending(candidates: list[dict]) -> int:
    return sum(not c["decision"] for c in candidates)


def _find_entry(rows: list[PilotReference], ep: dict, ruling_id: str) -> PilotReference:
    row = next((r for r in rows if r.ruling_id == ruling_id), None)
    if row is None or row.episode_id != ep["episode_id"]:
        raise DeskError(f"no entry {ruling_id} in this session", 404)
    return row


@dataclass(kw_only=True)
class Desk:
    """All docket state and rules; the HTTP handler only translates.

    Every path is injectable so tests run against a temp tree.
    """

    pilot_path: Path = PILOT_PATH
    reference_path: Path = REFERENCE_PATH
    seal_path: Path = SEAL_PATH
    adjudication_path: Path = ADJUDICATION_PATH
    scorecard_path: Path = SCORECARD_VERIFIED_PATH
    verdicts_path: Path = VERDICTS_PATH
    enrolled_index_path: Path = ENROLLED_INDEX_PATH
    transcript_index_path: Path = TRANSCRIPT_INDEX_PATH
    manifest_path: Path = MANIFEST_PATH
    state_path: Path = STATE_PATH
    peaks_dir: Path = PEAKS_DIR
    review_path: Path = REVIEW_PATH
    # one writer at a time; the server is threaded
    lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ---- stored state -------------------------------------------------

    def pilot(self) -> list[dict]:
        return json.loads(self.pilot_path.read_text())["episodes"]

    def _episode(self, number: int) -> dict:
        for ep in self.pilot():
            if ep["episode"] == number:
                return ep
        raise DeskError(f"episode {number} is not in the pilot set", 404)

    def rows(self) -> list[PilotReference]:
        if not self.reference_path.exists():
            return []
        return [PilotReference(**d) for d in read_jsonl(self.reference_path)]

    def _write_rows(self, rows: list[PilotReference]) -> None:
        order = {ep["episode_id"]: i for i, ep in enumerate(self.pilot())}
        rows = sorted(
            rows, key=lambda r: (order.get(r.episode_id, 1 << 30), _ruling_k(r.ruling_id))
        )
        write_lines_atomic(self.reference_path, (r.model_dump_json() for r in rows))

    def state(self) -> dict:
        if not self.state_path.exists():
            return {"sessions": {}}
        return json.loads(self.state_path.read_text())

    def _write_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.state_path, state)

    def seal(self) -> dict | None:
        return json.loads(self.seal_path.read_text()) if self.seal_path.exists() else None

    def _session_state(self, state: dict, episode_id: str) -> dict:
        """The session's entry in ``state``, created and filled with defaults."""
        s = state["sessions"].setdefault(episode_id, {})
        for key, value in SESSION_DEFAULTS.items():
            s.setdefault(key, value)
        return s

    def _require_unsealed(self) -> None:
        if self.seal():
            raise DeskError("the record is sealed; corrections go through supersession", 409)

    def _require_sealed(self) -> dict:
        seal = self.seal()
        if not seal:
            raise DeskError("available only after the record is sealed", 409)
        return seal

    def _unsealed_session(
        self, number: int, *, require_open: bool = False
    ) -> tuple[dict, dict, dict]:
        """(episode, state, session) for a pre-seal write; the caller holds the lock."""
        self._require_unsealed()
        ep = self._episode(number)
        state = self.state()
        s = self._session_state(state, ep["episode_id"])
        if require_open and s["status"] != "open":
            raise DeskError("session is closed; reopen it to change entries", 409)
        return ep, state, s

    @staticmethod
    def _issue_ruling_id(s: dict, rows: list[PilotReference], episode_id: str) -> str:
        """Next ``<episode_id>#<k>``; ``k`` is never reused, even if the
        session counter was lost."""
        used = max((_ruling_k(r.ruling_id) for r in rows if r.episode_id == episode_id), default=0)
        k = max(s["next_k"], used + 1)
        s["next_k"] = k + 1
        return f"{episode_id}#{k}"

    # ---- read views ---------------------------------------------------

    def _audio_paths(self) -> dict[str, Path]:
        """Episode id -> downloaded audio path, per the manifest."""
        if not self.manifest_path.exists():
            return {}
        return {
            m["episode_id"]: Path(m["file_path"])
            for m in read_jsonl(self.manifest_path)
            if m.get("status") == "success"
        }

    @staticmethod
    def _has_audio(audio_paths: dict[str, Path], episode_id: str) -> bool:
        path = audio_paths.get(episode_id)
        return path is not None and path.exists()

    def overview(self) -> dict:
        rows = self.rows()
        state = self.state()
        seal = self.seal()
        audio_paths = self._audio_paths()
        sources = self._pool_sources()
        sessions = []
        for ep in self.pilot():
            s = self._session_state(state, ep["episode_id"])
            sessions.append(
                {
                    "episode": ep["episode"],
                    "episode_id": ep["episode_id"],
                    "title": ep["title"],
                    "duration_s": ep["duration_s"],
                    "status": "sealed" if seal else s["status"],
                    "entries": sum(1 for r in rows if r.episode_id == ep["episode_id"]),
                    "resume_s": s["resume_s"],
                    "pending": _pending(self.candidates(ep, sources)),
                    "audio_available": self._has_audio(audio_paths, ep["episode_id"]),
                }
            )
        return {
            "sessions": sessions,
            "sealed": seal,
            "reference_version": seal["reference_version"] if seal else SEAL_VERSION,
            "annotator": ANNOTATOR,
        }

    def session(self, number: int) -> dict:
        ep = self._episode(number)
        s = self._session_state(self.state(), ep["episode_id"])
        seal = self.seal()
        entries = [r.model_dump() for r in self.rows() if r.episode_id == ep["episode_id"]]
        entries.sort(key=lambda e: (e["ruling_start_s"], _ruling_k(e["ruling_id"])))
        superseded = self._superseded_ids() if seal else {}
        for e in entries:
            e["superseded_by"] = superseded.get(e["ruling_id"])
        candidates = self.candidates(ep)
        return {
            **ep,
            "status": "sealed" if seal else s["status"],
            "no_rulings": s["no_rulings"],
            "resume_s": s["resume_s"],
            "next_k": s["next_k"],
            "entries": entries,
            "candidates": candidates,
            "pending": _pending(candidates),
            "sealed": seal,
            "audio_available": self._has_audio(self._audio_paths(), ep["episode_id"]),
            # Pooled review already shows model findings, so the transcript is
            # open throughout: it is how missed rulings get found quickly.
            "transcript": self._transcript(ep["episode_id"]),
        }

    def _transcript(self, episode_id: str) -> list[dict] | None:
        """Candidate B's transcript, resolved the same way extraction resolves it."""
        row = next(
            (
                r
                for r in index_rows(
                    TRANSCRIPT_CANDIDATE, self.enrolled_index_path, self.transcript_index_path
                )
                if r["episode_id"] == episode_id
            ),
            None,
        )
        path = Path(row["path"]) if row else None
        if path is None or not path.exists():
            return None
        return [
            {
                "start": seg["start"],
                "end": seg["end"],
                "speaker": seg.get("speaker") or "UNKNOWN",
                "text": seg["text"].strip(),
            }
            for seg in json.loads(path.read_text()).get("segments", [])
        ]

    def peaks(self, number: int) -> dict:
        """Max-abs amplitude per 1/PEAKS_PER_S s, 0-1, cached per audio file."""
        audio = self.audio_file(number)
        cache = self.peaks_dir / f"{number}.json"
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached.get("audio_bytes") == audio.stat().st_size:
                return cached
        try:
            proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(audio),
                    "-ac",
                    "1",
                    "-ar",
                    str(PEAKS_RATE_HZ),
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
                check=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeskError(f"waveform unavailable: ffmpeg failed ({exc})", 500) from exc
        samples = array("h")
        samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
        step = PEAKS_RATE_HZ // PEAKS_PER_S
        peaks = []
        for i in range(0, len(samples), step):
            chunk = samples[i : i + step]  # max/min on an array slice run at C speed
            peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 3))
        result = {
            "per_s": PEAKS_PER_S,
            "duration_s": len(samples) / PEAKS_RATE_HZ,
            "peaks": peaks,
            "audio_bytes": audio.stat().st_size,
        }
        self.peaks_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(cache, result)
        return result

    def audio_file(self, number: int) -> Path:
        ep = self._episode(number)
        if not self._has_audio(audio_paths := self._audio_paths(), ep["episode_id"]):
            raise DeskError(f"no audio for episode {number}; run `jev-or-not download`", 404)
        return audio_paths[ep["episode_id"]]

    # ---- pooled review -----------------------------------------------

    def _review_log(self) -> list[dict]:
        return list(read_jsonl(self.review_path)) if self.review_path.exists() else []

    def _write_review_log(self, log: list[dict]) -> None:
        write_lines_atomic(self.review_path, (json.dumps(r, sort_keys=True) for r in log))

    def _log_decision(
        self,
        ep: dict,
        decision: str,
        *,
        candidate: dict | None = None,
        ruling_id: str | None = None,
        reason: str = "",
        subject: str | None = None,
    ) -> None:
        c = candidate or {}
        entry = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": ep["episode_id"],
            "candidate_id": c.get("candidate_id"),
            "subject": c.get("subject") or subject,
            "proposed_verdict": c.get("verdict"),
            "found_by": c.get("found_by", 0),
            "runs_total": c.get("runs_total"),
            "on_scorecard": c.get("on_scorecard", False),
            "decision": decision,
            "ruling_id": ruling_id,
            "reason": reason or None,
            "decided_by": ANNOTATOR,
            "decided_at": _now(),
        }
        self._write_review_log([*self._review_log(), entry])

    def _strike(self, rows: list[PilotReference], ruling_id: str) -> None:
        """Remove a ruling and its approve/fix/add decision; the number is
        retired, never reissued, and its finding returns to pending."""
        self._write_rows([r for r in rows if r.ruling_id != ruling_id])
        self._write_review_log([r for r in self._review_log() if r["ruling_id"] != ruling_id])

    def _candidate_by_hash(self) -> dict[str, str]:
        """Transcription candidate (A, A2, B) for each transcript hash."""
        candidate_by_hash: dict[str, str] = {}
        for path, key in (
            (self.enrolled_index_path, "enrolled_hash"),
            (self.transcript_index_path, "transcript_hash"),
        ):
            if path.exists():
                for row in read_jsonl(path):
                    candidate_by_hash.setdefault(row[key], row["candidate"])
        return candidate_by_hash

    def _pool_sources(self) -> _PoolSources:
        """Everything ``candidates`` reads, loaded once so a caller covering
        every episode doesn't re-read it per episode."""
        candidate_by_hash = self._candidate_by_hash()
        by_episode: dict[str, list[dict]] = {}
        runs: set[str] = set()
        if self.verdicts_path.exists():
            for v in read_jsonl(self.verdicts_path):
                run = f"{candidate_by_hash.get(v['transcript_hash'], '?')}|{v['extractor_model']}"
                runs.add(run)
                by_episode.setdefault(v["episode_id"], []).append({**v, "run": run})
        scorecard = list(read_jsonl(self.scorecard_path)) if self.scorecard_path.exists() else []
        return _PoolSources(by_episode, len(runs), scorecard, self._review_log())

    def candidates(self, ep: dict, sources: _PoolSources | None = None) -> list[dict]:
        """This episode's pooled candidates with Rex's decision merged in."""
        by_episode, runs_total, all_scorecard, review_log = sources or self._pool_sources()
        scorecard = [
            r
            for r in all_scorecard
            if r.get("episode_id") == ep["episode_id"] and r.get("review_status") != "excluded"
        ]
        pooled = pool(
            ep["episode_id"],
            ep["duration_s"],
            by_episode.get(ep["episode_id"], []),
            scorecard,
            runs_total,
        )
        decided = {
            r["candidate_id"]: r
            for r in review_log
            if r["episode_id"] == ep["episode_id"] and r["candidate_id"]
        }
        for c in pooled:
            d = decided.get(c["candidate_id"])
            c["decision"] = d["decision"] if d else None
            c["ruling_id"] = d["ruling_id"] if d else None
        return pooled

    def review(self, number: int, body: dict) -> dict:
        """Approve, fix, reject, or undo one pooled candidate."""
        action = body.get("action")
        if action not in ("approve", "fix", "reject", "undo"):
            raise DeskError("action must be approve, fix, reject, or undo")
        with self.lock:
            ep, state, s = self._unsealed_session(number, require_open=True)
            cand = next(
                (c for c in self.candidates(ep) if c["candidate_id"] == body.get("candidate_id")),
                None,
            )
            if cand is None:
                raise DeskError("no such finding in this session", 404)
            rows = self.rows()
            if action == "undo":
                if not cand["decision"]:
                    raise DeskError("that finding has no decision to undo", 409)
                if cand["ruling_id"]:
                    self._strike(rows, cand["ruling_id"])
                else:  # a rejection: no ruling behind it
                    self._write_review_log(
                        [r for r in self._review_log() if r["candidate_id"] != cand["candidate_id"]]
                    )
                return {"candidate_id": cand["candidate_id"], "decision": None}
            if cand["decision"]:
                raise DeskError("already decided; undo it first", 409)
            if action == "reject":
                self._log_decision(ep, "rejected", candidate=cand, reason=body.get("reason", ""))
                return {"candidate_id": cand["candidate_id"], "decision": "rejected"}
            if action == "approve":
                if not cand["span"] or not cand["verdict"]:
                    raise DeskError("this finding has no verdict or span to approve; fix it")
                payload = {
                    "subject": cand["subject"],
                    "category": cand["category"],
                    "verdict": cand["verdict"],
                    "verdict_strength": cand["verdict_strength"],
                    "ruling_start_s": cand["span"][0],
                    "ruling_end_s": cand["span"][1],
                    "speaker": "JOHN",
                }
            else:
                payload = body
            decision = "approved" if action == "approve" else "fixed"
            row = self._enter_ruling(
                ep, state, s, rows, payload, decision, candidate=cand, reason=body.get("reason", "")
            )
            return {**row.model_dump(), "decision": decision}

    # ---- open-record writes ------------------------------------------

    def _build_row(self, ep: dict, ruling_id: str, payload: dict, version: str) -> PilotReference:
        """Validate ``payload`` into a row; ``PilotReference`` owns the field rules."""
        fields = {k: payload.get(k) for k in EDITABLE_FIELDS}
        if fields["verdict"] == "no_ruling":
            fields["verdict_strength"] = None
        fields["subject"] = (fields["subject"] or "").strip()
        fields["category"] = (fields["category"] or "").strip() or "robot"
        for key in ("ruling_start_s", "ruling_end_s"):
            if fields[key] is None:
                continue
            try:
                value = float(fields[key])
            except (TypeError, ValueError) as exc:
                raise DeskError(f"{key} must be a number") from exc
            if not (0 <= value <= ep["duration_s"] + 5):
                raise DeskError(f"{key} is outside the episode (0-{ep['duration_s']}s)")
        try:
            return PilotReference(
                schema_version=SCHEMA_VERSION,
                episode_id=ep["episode_id"],
                ruling_id=ruling_id,
                annotator=ANNOTATOR,
                annotated_at=_now(),
                reference_version=version,
                **fields,
            )
        except ValidationError as exc:
            msgs = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else e["msg"]
                for e in exc.errors()
            )
            raise DeskError(msgs) from exc

    def _enter_ruling(
        self,
        ep: dict,
        state: dict,
        s: dict,
        rows: list[PilotReference],
        payload: dict,
        decision: str,
        *,
        candidate: dict | None = None,
        reason: str = "",
    ) -> PilotReference:
        """Add a new row under a fresh ruling id and log how it got there."""
        ruling_id = self._issue_ruling_id(s, rows, ep["episode_id"])
        row = self._build_row(ep, ruling_id, payload, SEAL_VERSION)
        s["no_rulings"] = False
        self._write_rows([*rows, row])
        self._write_state(state)
        self._log_decision(
            ep,
            decision,
            candidate=candidate,
            ruling_id=ruling_id,
            reason=reason,
            subject=row.subject,
        )
        return row

    def save_entry(self, number: int, payload: dict) -> dict:
        """Create (no ``ruling_id``) or update an entry in an open session."""
        with self.lock:
            ep, state, s = self._unsealed_session(number, require_open=True)
            rows = self.rows()
            ruling_id = payload.get("ruling_id")
            if ruling_id:
                _find_entry(rows, ep, ruling_id)
                row = self._build_row(ep, ruling_id, payload, SEAL_VERSION)
                self._write_rows([row if r.ruling_id == ruling_id else r for r in rows])
            else:
                # a ruling no source found: the pooled reference's added recall
                row = self._enter_ruling(ep, state, s, rows, payload, "added")
            return row.model_dump()

    def delete_entry(self, number: int, ruling_id: str) -> None:
        with self.lock:
            ep, _, _ = self._unsealed_session(number, require_open=True)
            rows = self.rows()
            _find_entry(rows, ep, ruling_id)
            self._strike(rows, ruling_id)

    def set_resume(self, number: int, position_s: float) -> None:
        with self.lock:
            ep = self._episode(number)
            state = self.state()
            self._session_state(state, ep["episode_id"])["resume_s"] = round(float(position_s), 1)
            self._write_state(state)

    def close_session(self, number: int, *, no_rulings: bool = False) -> None:
        with self.lock:
            ep, state, s = self._unsealed_session(number)
            if pending := _pending(self.candidates(ep)):
                raise DeskError(f"{pending} finding(s) still to review", 409)
            has_entries = any(r.episode_id == ep["episode_id"] for r in self.rows())
            if not has_entries and not no_rulings:
                raise DeskError("no entries: confirm the episode has no rulings to close it")
            s["status"] = "closed"
            s["no_rulings"] = not has_entries
            s["closed_at"] = _now()
            self._write_state(state)

    def reopen_session(self, number: int) -> None:
        with self.lock:
            _, state, s = self._unsealed_session(number)
            s["status"] = "open"
            self._write_state(state)

    def seal_record(self) -> dict:
        with self.lock:
            self._require_unsealed()
            state = self.state()
            pilot = self.pilot()
            sessions = {ep["episode"]: self._session_state(state, ep["episode_id"]) for ep in pilot}
            still_open = [n for n, s in sessions.items() if s["status"] != "closed"]
            if still_open:
                raise DeskError(f"close every session first; still open: {still_open}", 409)
            rows = self.rows()
            self._write_rows(rows)  # normalise order before hashing
            seal = {
                "schema_version": SCHEMA_VERSION,
                "reference_version": SEAL_VERSION,
                "sealed_at": _now(),
                "sealed_by": ANNOTATOR,
                "rows": len(rows),
                "no_ruling_sessions": sorted(n for n, s in sessions.items() if s["no_rulings"]),
                "reference_sha256": _file_sha256(self.reference_path),
            }
            write_json_atomic(self.seal_path, seal)
            return seal

    # ---- sealed-record writes ----------------------------------------

    def _adjudication(self) -> list[AdjudicationEntry]:
        if not self.adjudication_path.exists():
            return []
        return read_adjudication_entries(self.adjudication_path)

    def _append_adjudication(self, entry: AdjudicationEntry) -> None:
        ensure_adjudication_file(self.adjudication_path)
        with self.adjudication_path.open("a") as f:
            f.write(entry.model_dump_json() + "\n")

    def _superseded_ids(self) -> dict[str, str]:
        return {e.source_row_id: e.new for e in self._adjudication() if e.field == "superseded_by"}

    def supersede(self, number: int, old_id: str, payload: dict, reason: str) -> dict:
        """Correct a sealed entry: a new row with a new id and a bumped
        version, plus an adjudication entry linking old to new."""
        if not (reason or "").strip():
            raise DeskError("a supersession needs a reason")
        with self.lock:
            seal = self._require_sealed()
            ep = self._episode(number)
            rows = self.rows()
            _find_entry(rows, ep, old_id)
            if old_id in self._superseded_ids():
                raise DeskError(f"{old_id} is already superseded; correct its successor", 409)
            state = self.state()
            s = self._session_state(state, ep["episode_id"])
            version = _bump_minor(seal["reference_version"])
            ruling_id = self._issue_ruling_id(s, rows, ep["episode_id"])
            new = self._build_row(ep, ruling_id, payload, version)
            self._write_rows([*rows, new])
            self._write_state(state)
            self._append_adjudication(
                AdjudicationEntry(
                    schema_version=SCHEMA_VERSION,
                    source_row_id=old_id,
                    field="superseded_by",
                    old=None,
                    new=new.ruling_id,
                    reason=reason.strip(),
                    evidence=None,
                    decided_by=ANNOTATOR,
                    decided_at=_now(),
                )
            )
            seal.update(
                reference_version=version,
                rows=len(rows) + 1,
                reference_sha256=_file_sha256(self.reference_path),
                amended_at=_now(),
            )
            write_json_atomic(self.seal_path, seal)
            return new.model_dump()

    def scorecard(self, number: int) -> dict:
        self._require_sealed()
        ep = self._episode(number)
        if not self.scorecard_path.exists():
            return {"rulings": [], "disagreements": []}
        rows = [
            r for r in read_jsonl(self.scorecard_path) if r.get("episode_id") == ep["episode_id"]
        ]
        source_ids = {sid for r in rows for sid in r["source_row_ids"]}
        decisions = [
            e.model_dump()
            for e in self._adjudication()
            if e.field == "pilot_disagreement" and e.source_row_id in source_ids
        ]
        return {"rulings": rows, "disagreements": decisions}

    def record_disagreement(self, number: int, payload: dict) -> dict:
        """Log that a sealed pilot entry and a scorecard ruling disagree.
        ``pilot_disagreement`` is not an adjudicable field, so this records
        the conflict without changing either side."""
        reason = (payload.get("reason") or "").strip()
        if not reason:
            raise DeskError("say why they disagree and which side you believe")
        with self.lock:
            self._require_sealed()
            ep = self._episode(number)
            if not self.scorecard_path.exists():
                raise DeskError("no scorecard yet; run `jev-or-not scorecard`", 404)
            ref = next(
                (
                    r
                    for r in read_jsonl(self.scorecard_path)
                    if r.get("ruling_ref") == payload.get("ruling_ref")
                    and r.get("episode_id") == ep["episode_id"]
                ),
                None,
            )
            if ref is None:
                raise DeskError("unknown scorecard ruling for this session", 404)
            pilot = next((r for r in self.rows() if r.ruling_id == payload.get("ruling_id")), None)
            if pilot is not None and pilot.episode_id != ep["episode_id"]:
                raise DeskError("that entry belongs to another session")
            entry = AdjudicationEntry(
                schema_version=SCHEMA_VERSION,
                source_row_id=ref["source_row_ids"][0],
                field="pilot_disagreement",
                old=ref["label"],
                new=pilot.verdict if pilot else "missing",
                reason=reason,
                evidence=pilot.ruling_id if pilot else None,
                decided_by=ANNOTATOR,
                decided_at=_now(),
            )
            self._append_adjudication(entry)
            return entry.model_dump()

    def extractions(self, number: int) -> dict:
        self._require_sealed()
        ep = self._episode(number)
        candidate_by_hash = self._candidate_by_hash()
        runs: dict[str, list[dict]] = {}
        if self.verdicts_path.exists():
            for v in read_jsonl(self.verdicts_path):
                if v["episode_id"] != ep["episode_id"]:
                    continue
                cand = candidate_by_hash.get(v["transcript_hash"], "?")
                model = _MODEL_BY_ID.get(v["extractor_model"], v["extractor_model"])
                runs.setdefault(f"{cand} · {model}", []).append(
                    {
                        "subject": v["subject"],
                        "category": v["category"],
                        "verdict": v["verdict"],
                        "verdict_strength": v["verdict_strength"],
                        "confidence": v["extractor_confidence"],
                        "quotes": v["evidence_quotes"],
                    }
                )
        return {"runs": dict(sorted(runs.items()))}


# ---- HTTP ---------------------------------------------------------------

_ALLOWED_HOSTS = ("127.0.0.1", "localhost")


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "jev-or-not-annotate"
    desk: Desk  # set per server by make_handler

    def log_message(self, *_: Any) -> None:
        pass  # quiet; errors surface in the UI

    def _send_bytes(
        self, body: bytes, ctype: str, *, cache: str = "no-store", status: int = 200
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        self._send_bytes(json.dumps(data).encode(), "application/json", status=status)

    def _host_ok(self) -> bool:
        # Refuse DNS-rebinding style requests from other origins.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in _ALLOWED_HOSTS

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise DeskError(f"bad JSON: {exc}") from exc

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._send_json({"error": "forbidden host"}, 403)
            return
        # Every POST must be JSON: a cross-site <form> cannot send that
        # without a CORS preflight, which this server never answers.
        if method == "POST" and (
            self.headers.get("Content-Type", "").split(";")[0] != "application/json"
        ):
            self._send_json({"error": "expected application/json"}, 415)
            return
        path = self.path.split("?", 1)[0]
        for m, pattern, route in _ROUTES:
            match = pattern.match(path)
            if m == method and match:
                try:
                    route(self, *match.groups())
                except DeskError as exc:
                    self._send_json({"error": str(exc)}, exc.status)
                except BrokenPipeError, ConnectionResetError:
                    pass  # the browser cancelled an audio range request
                return
        self._send_json({"error": "not found"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def send_index(self) -> None:
        self._send_bytes(UI_PATH.read_bytes(), "text/html; charset=utf-8")

    def send_font(self, name: str) -> None:
        path = FONTS_DIR / name  # the route regex admits no path separators
        if not path.is_file():
            raise DeskError("not found", 404)
        self._send_bytes(path.read_bytes(), "font/woff2", cache="max-age=86400")

    def send_audio(self, number: int) -> None:
        path = self.desk.audio_file(number)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else size - 1
            else:
                start = max(0, size - int(rng.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def _reply(h: _Handler, result: Any) -> None:
    h._send_json({"ok": True} if result is None else result)


def _get(fn: Callable[..., Any]) -> Callable[..., None]:
    """A JSON GET route: ``fn(desk, *int_groups)``; ``None`` replies ``{"ok": true}``."""
    return lambda h, *groups: _reply(h, fn(h.desk, *map(int, groups)))


def _post(fn: Callable[..., Any]) -> Callable[..., None]:
    """A JSON POST route: ``fn(desk, body, *int_groups)``."""
    return lambda h, *groups: _reply(h, fn(h.desk, h.body(), *map(int, groups)))


_S = r"^/api/sessions/(\d+)"
_ROUTES: list[tuple[str, re.Pattern, Callable[..., None]]] = [
    (method, re.compile(pattern), route)
    for method, pattern, route in [
        ("GET", r"^/$", _Handler.send_index),
        ("GET", r"^/fonts/([a-z0-9-]+\.woff2)$", _Handler.send_font),
        ("GET", r"^/audio/(\d+)$", lambda h, n: h.send_audio(int(n))),
        ("GET", r"^/api/overview$", _get(Desk.overview)),
        ("GET", _S + "$", _get(Desk.session)),
        ("GET", _S + "/peaks$", _get(Desk.peaks)),
        ("GET", _S + "/scorecard$", _get(Desk.scorecard)),
        ("GET", _S + "/extractions$", _get(Desk.extractions)),
        ("POST", _S + "/entries$", _post(lambda d, b, n: d.save_entry(n, b))),
        (
            "POST",
            _S + "/entries/delete$",
            _post(lambda d, b, n: d.delete_entry(n, b.get("ruling_id", ""))),
        ),
        (
            "POST",
            _S + "/resume$",
            _post(lambda d, b, n: d.set_resume(n, float(b.get("position_s", 0)))),
        ),
        (
            "POST",
            _S + "/close$",
            _post(lambda d, b, n: d.close_session(n, no_rulings=bool(b.get("no_rulings")))),
        ),
        ("POST", _S + "/reopen$", _post(lambda d, b, n: d.reopen_session(n))),
        ("POST", _S + "/review$", _post(lambda d, b, n: d.review(n, b))),
        (
            "POST",
            _S + "/supersede$",
            _post(lambda d, b, n: d.supersede(n, b.get("ruling_id", ""), b, b.get("reason", ""))),
        ),
        ("POST", _S + "/disagreements$", _post(lambda d, b, n: d.record_disagreement(n, b))),
        ("POST", r"^/api/seal$", _post(lambda d, b: d.seal_record())),
    ]
]


def make_handler(desk: Desk) -> type[http.server.BaseHTTPRequestHandler]:
    return type("Handler", (_Handler,), {"desk": desk})


class _LocalServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind resolves socket.getfqdn(), a reverse-DNS
        # lookup that can stall startup for tens of seconds; a 127.0.0.1-only
        # server has no use for the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(port: int = 8765, desk: Desk | None = None) -> http.server.ThreadingHTTPServer:
    """Build the server bound to 127.0.0.1; the caller runs ``serve_forever``."""
    return _LocalServer(("127.0.0.1", port), make_handler(desk or Desk()))
