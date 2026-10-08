"""Event and presence state machine for the smart attendance camera."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock


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
        mark_attendance_callback,
        get_setting_callback,
        state=None,
    ):
        self._add_event = add_event_callback
        self._mark_attendance = mark_attendance_callback
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
            track.last_crossing == "enter"
            and student_id not in self.present
        ):
            self._enter(track, camera_id)
            return True

        if (
            track.last_crossing == "exit"
            and student_id in self.present
        ):
            self._exit(track, camera_id)
            return True

        return True

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

        with self._state.lock:
            if student_id in self.present:
                return []

        start = self._time_setting("school_start_time", "08:00")
        late_enabled = self._bool_setting(
            self._get_setting("enable_late_events", "1"),
            True,
        )
        is_late = late_enabled and datetime.now().time() > start

        inserted = self._mark_attendance(
            student_id,
            f"camera_{camera_id}",
            "late" if is_late else "present",
            (
                f"ورود بعد از زمان شروع مدرسه ({start.strftime('%H:%M')})"
                if is_late
                else ""
            ),
        )

        with self._state.lock:
            self.present.add(student_id)
            self.last_entered[student_id] = datetime.now()

        emitted = [EVENT_PERSON_ENTERED]
        self._emit(
            EVENT_PERSON_ENTERED,
            track,
            camera_id,
            track.identity_confidence,
        )

        if is_late:
            self._emit(
                EVENT_LATE_ARRIVAL,
                track,
                camera_id,
                track.identity_confidence,
                {"school_start_time": start.strftime("%H:%M")},
            )
            emitted.append(EVENT_LATE_ARRIVAL)

        if inserted:
            track.metadata["attendance_marked"] = True

        return emitted

    def _exit(self, track, camera_id):
        student_id = track.student_id
        if student_id is None:
            return [EVENT_UNKNOWN_PERSON]

        with self._state.lock:
            if student_id not in self.present:
                unexpected = True
            else:
                self.present.remove(student_id)
                unexpected = False

        if unexpected:
            self._emit(
                EVENT_UNEXPECTED_EXIT,
                track,
                camera_id,
                track.identity_confidence,
            )
            return [EVENT_UNEXPECTED_EXIT]

        self._emit(
            EVENT_PERSON_EXITED,
            track,
            camera_id,
            track.identity_confidence,
        )
        emitted = [EVENT_PERSON_EXITED]

        end = self._time_setting("school_end_time", "15:00")
        if (
            self._bool_setting(
                self._get_setting("enable_early_events", "1"),
                True,
            )
            and datetime.now().time() < end
        ):
            self._emit(
                EVENT_EARLY_DEPARTURE,
                track,
                camera_id,
                track.identity_confidence,
                {"school_end_time": end.strftime("%H:%M")},
            )
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
