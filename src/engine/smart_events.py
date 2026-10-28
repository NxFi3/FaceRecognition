"""Event and presence state machine for the smart attendance camera."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from datetime import datetime
from threading import RLock

from .runtime_profile import profile


@dataclass
class AttendancePresenceState:
    present: set[int] = field(default_factory=set)
    last_entered: dict[int, datetime] = field(default_factory=dict)
    active_tracks: dict[int, set[tuple[str, int]]] = field(default_factory=dict)
    current_date: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d")
    )
    lock: RLock = field(default_factory=RLock, repr=False)


EVENT_FACE_RECOGNIZED = "FACE_RECOGNIZED"
EVENT_PERSON_ENTERED = "PERSON_ENTERED"
EVENT_PERSON_EXITED = "PERSON_EXITED"
EVENT_LATE_ARRIVAL = "LATE_ARRIVAL"
EVENT_EARLY_DEPARTURE = "EARLY_DEPARTURE"
EVENT_UNKNOWN_PERSON = "UNKNOWN_PERSON"
EVENT_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
EVENT_IDENTITY_REID_LINKED = "IDENTITY_REID_LINKED"
EVENT_UNEXPECTED_EXIT = "UNEXPECTED_EXIT"


class AttendanceEventEngine:
    def __init__(
        self,
        add_event_callback,
        commit_transition_callback,
        get_setting_callback,
        state=None,
    ):
        self._add_event = add_event_callback
        self._commit_transition = commit_transition_callback
        self._get_setting = get_setting_callback
        self._state = state or AttendancePresenceState()

    @property
    def present(self):
        return self._state.present

    def ensure_current_day(self):
        """Reset in-memory presence when the calendar day rolls over."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._state.lock:
            if self._state.current_date == today:
                return False
            self._state.current_date = today
            self._state.present.clear()
            self._state.last_entered.clear()
            self._state.active_tracks.clear()
        return True

    @property
    def last_entered(self):
        return self._state.last_entered

    @staticmethod
    def _bool_setting(value, default=True):
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _time_setting(self, key, default):
        value = self._get_setting(key, default)
        try:
            return datetime.strptime(str(value), "%H:%M").time()
        except ValueError:
            return datetime.strptime(default, "%H:%M").time()

    def _emit(self, event_type, track, camera_id, confidence=None, metadata=None):
        return self._add_event(
            event_type=event_type,
            student_id=track.student_id,
            track_id=track.track_id,
            camera_id=f"camera_{camera_id}",
            confidence=confidence if confidence is not None else track.identity_confidence,
            metadata=metadata or {},
        )

    def _apply_identity(
        self,
        track,
        student_id,
        student_code,
        student_name,
        confidence,
        camera_id,
        event_type,
        metadata=None,
    ):
        old_student_id = track.student_id
        if old_student_id == student_id:
            track.student_code = student_code
            track.student_name = student_name
            track.identity_confidence = max(track.identity_confidence, confidence)

            # A Re-ID hand-off can assign the same student before the face
            # recognizer confirms them. When the face later agrees, still emit
            # the stronger FACE_RECOGNIZED event and reconcile a crossing that
            # happened while the provisional identity was active.
            if event_type == EVENT_FACE_RECOGNIZED:
                payload = dict(metadata or {})
                payload.setdefault("student_code", student_code)
                payload.setdefault("student_name", student_name)
                self._emit(
                    event_type,
                    track,
                    camera_id,
                    confidence,
                    payload,
                )
                if (
                    track.metadata.get("pending_crossing") is None
                    and track.last_crossing == "enter"
                    and student_id not in self.present
                ):
                    self._enter(track, camera_id)
                    return True
                if (
                    track.metadata.get("pending_crossing") is None
                    and track.last_crossing == "exit"
                    and student_id in self.present
                ):
                    self._exit(track, camera_id)
                    return True
            return False

        if old_student_id is not None and old_student_id != student_id:
            self._emit(
                EVENT_IDENTITY_MISMATCH,
                track,
                camera_id,
                confidence,
                {
                    "old_student_id": old_student_id,
                    "new_student_id": student_id,
                },
            )

        track.student_id = student_id
        track.student_code = student_code
        track.student_name = student_name
        track.identity_confidence = confidence

        payload = dict(metadata or {})
        payload.setdefault("student_code", student_code)
        payload.setdefault("student_name", student_name)
        self._emit(
            event_type,
            track,
            camera_id,
            confidence,
            payload,
        )

        # A person can cross first and become identified a few frames later.
        # Reconcile that already-recorded crossing as soon as the identity is
        # trusted instead of losing the attendance transition.
        if (
            track.metadata.get("pending_crossing") is None
            and track.last_crossing == "enter"
            and student_id not in self.present
        ):
            self._enter(track, camera_id)
            return True

        if (
            track.metadata.get("pending_crossing") is None
            and track.last_crossing == "exit"
            and student_id in self.present
        ):
            self._exit(track, camera_id)
            return True

        return True

    @profile.stage("event_face_identity")
    @profile.identity("face")
    def on_face_identity(
        self,
        track,
        student_id,
        student_code,
        student_name,
        confidence,
        camera_id,
    ):
        return self._apply_identity(
            track,
            student_id,
            student_code,
            student_name,
            confidence,
            camera_id,
            EVENT_FACE_RECOGNIZED,
        )

    @profile.stage("event_reid_identity")
    @profile.identity("reid")
    def on_reid_identity(
        self,
        track,
        student_id,
        student_code,
        student_name,
        confidence,
        camera_id,
        metadata=None,
    ):
        return self._apply_identity(
            track,
            student_id,
            student_code,
            student_name,
            confidence,
            camera_id,
            EVENT_IDENTITY_REID_LINKED,
            metadata,
        )

    @profile.stage("event_crossing")
    def on_crossing(self, track, direction, camera_id):
        self.ensure_current_day()

        if track.student_id is None:
            self._emit(
                EVENT_UNKNOWN_PERSON,
                track,
                camera_id,
                0.0,
                {"direction": direction},
            )
            return [EVENT_UNKNOWN_PERSON]

        if direction == "enter":
            return self._enter(track, camera_id)

        return self._exit(track, camera_id)

    def _enter(self, track, camera_id):
        student_id = track.student_id
        if student_id is None:
            return [EVENT_UNKNOWN_PERSON]

        if not track.metadata.get("face_witnessed_for_crossing", False):
            self._emit(
                EVENT_UNKNOWN_PERSON,
                track,
                camera_id,
                track.identity_confidence,
                {
                    "direction": "enter",
                    "reason": "face_witness_required",
                },
            )
            return [EVENT_UNKNOWN_PERSON]

        # Both cameras share this state. The database transaction also checks
        # the latest durable movement, so a crash after commit but before this
        # in-memory update remains retryable without another PERSON_ENTERED.
        with self._state.lock:
            if student_id in self.present:
                return []

            start = self._time_setting("school_start_time", "08:00")
            late_enabled = self._bool_setting(
                self._get_setting("enable_late_events", "1"),
                True,
            )
            is_late = late_enabled and datetime.now().time() > start

            result = self._commit_transition(
                student_id=student_id,
                direction="enter",
                camera_id=f"camera_{camera_id}",
                track_id=track.track_id,
                confidence=track.identity_confidence,
                status="late" if is_late else "present",
                notes=(
                    f"ورود بعد از زمان شروع مدرسه ({start.strftime('%H:%M')})"
                    if is_late
                    else ""
                ),
                auxiliary_event=EVENT_LATE_ARRIVAL if is_late else None,
                auxiliary_metadata={"school_start_time": start.strftime("%H:%M")} if is_late else None,
            )
            # A previous commit may have completed just before a worker crash.
            # Synchronize RAM with that durable entry but publish no duplicate.
            self.present.add(student_id)
            self.last_entered[student_id] = datetime.now()
            if not result.applied:
                return []

            emitted = [EVENT_PERSON_ENTERED]

            if is_late:
                emitted.append(EVENT_LATE_ARRIVAL)

            if result.attendance_inserted:
                track.metadata["attendance_marked"] = True

            return emitted

    def _exit(self, track, camera_id):
        student_id = track.student_id
        if student_id is None:
            return [EVENT_UNKNOWN_PERSON]

        with self._state.lock:
            if student_id not in self.present:
                self._emit(
                    EVENT_UNEXPECTED_EXIT,
                    track,
                    camera_id,
                    track.identity_confidence,
                )
                return [EVENT_UNEXPECTED_EXIT]

            end = self._time_setting("school_end_time", "15:00")
            is_early = (
                self._bool_setting(
                    self._get_setting("enable_early_events", "1"),
                    True,
                )
                and datetime.now().time() < end
            )
            result = self._commit_transition(
                student_id=student_id,
                direction="exit",
                camera_id=f"camera_{camera_id}",
                track_id=track.track_id,
                confidence=track.identity_confidence,
                auxiliary_event=EVENT_EARLY_DEPARTURE if is_early else None,
                auxiliary_metadata={"school_end_time": end.strftime("%H:%M")} if is_early else None,
            )
            # Preserve prior presence if the database transaction fails.
            self.present.remove(student_id)
            if not result.applied:
                return []

            emitted = [EVENT_PERSON_EXITED]
            if is_early:
                emitted.append(EVENT_EARLY_DEPARTURE)

            return emitted

    def on_track_seen(self, track, camera_id):
        """Keep global per-student visibility synchronized across cameras."""
        student_id = track.student_id
        if student_id is None or track.misses != 0:
            return

        with self._state.lock:
            tracks = self._state.active_tracks.setdefault(student_id, set())
            tracks.add((str(camera_id), int(track.track_id)))

    def on_track_lost(self, track, camera_id):
        """Remove a local observation without changing school presence.

        Losing sight of a student is not an exit. The configured virtual exit
        line is authoritative; otherwise an entry camera would mark every
        student leaving its field of view as an unexpected exit.
        """
        student_id = track.student_id
        if student_id is None:
            return False

        with self._state.lock:
            tracks = self._state.active_tracks.get(student_id)
            if tracks is not None:
                tracks.discard((str(camera_id), int(track.track_id)))
                if not tracks:
                    self._state.active_tracks.pop(student_id, None)

        return False

    def clear(self):
        with self._state.lock:
            self.present.clear()
            self.last_entered.clear()
            self._state.active_tracks.clear()
            self._state.current_date = datetime.now().strftime("%Y-%m-%d")
