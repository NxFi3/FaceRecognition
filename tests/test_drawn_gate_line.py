import unittest

import numpy as np
from src.database import MovementTransitionResult

from src.engine.smart_camera_worker import (
    _camera_role_accepts_crossing,
    _check_camera_gate,
    _draw_gate_overlay,
)
from src.engine.smart_events import (
    AttendanceEventEngine,
    AttendancePresenceState,
    EVENT_PERSON_ENTERED,
)
from src.engine.smart_tracking import LineCrossing, PersonTrack


FRAME_SHAPE = (100, 100, 3)


def track_moving(previous, current):
    x, y = current
    return PersonTrack(
        track_id=1,
        bbox=(int(x) - 1, 0, int(x) + 1, int(y)),
        hits=3,
        age=3,
        misses=0,
        previous_foot=previous,
    )


class DrawnGateLineTests(unittest.TestCase):
    def setUp(self):
        # Diagonal segment (25,25) -> (75,75); the upper/right side is inside.
        self.line = LineCrossing(
            line_points=(0.25, 0.25, 0.75, 0.75),
            inside_side=-1,
            cooldown_frames=10,
        )

    def test_outside_to_inside_is_entry(self):
        track = track_moving((30, 70), (70, 30))
        self.assertEqual(self.line.check(track, FRAME_SHAPE, 100), "enter")
        self.assertEqual(track.last_crossing, "enter")

    def test_inside_to_outside_is_exit(self):
        track = track_moving((70, 30), (30, 70))
        self.assertEqual(self.line.check(track, FRAME_SHAPE, 100), "exit")

    def test_crossing_extension_beyond_segment_is_ignored(self):
        track = track_moving((10, 30), (30, 10))
        self.assertIsNone(self.line.check(track, FRAME_SHAPE, 100))

    def test_invalid_custom_line_falls_back_to_legacy_behavior(self):
        line = LineCrossing(
            orientation="vertical",
            position=0.5,
            primary_direction="enter",
            line_points=(0.5, 0.5, 0.5, 0.5),
            inside_side=1,
        )
        track = track_moving((40, 50), (60, 50))
        self.assertIsNone(line.line_points)
        self.assertEqual(line.check(track, FRAME_SHAPE, 100), "enter")

    def test_camera_roles_accept_only_configured_direction(self):
        self.assertTrue(_camera_role_accepts_crossing("entry", "enter"))
        self.assertFalse(_camera_role_accepts_crossing("entry", "exit"))
        self.assertTrue(_camera_role_accepts_crossing("exit", "exit"))
        self.assertFalse(_camera_role_accepts_crossing("exit", "enter"))
        self.assertFalse(_camera_role_accepts_crossing("observe", "enter"))
        self.assertFalse(_camera_role_accepts_crossing("observe", "exit"))

    def test_dashboard_draws_custom_line_at_saved_coordinates(self):
        frame = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        _draw_gate_overlay(frame, self.line, "entry")
        # Normalized endpoints (0.25, 0.25) and (0.75, 0.75) -> pixels ~25 and ~75.
        self.assertTrue(np.any(frame[25, 25]))
        self.assertTrue(np.any(frame[75, 75]))
        self.assertGreater(np.count_nonzero(frame), 0)

    def test_observe_only_dashboard_has_no_gate_overlay(self):
        frame = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        result = _draw_gate_overlay(frame, self.line, "observe")
        self.assertIs(result, frame)
        self.assertEqual(np.count_nonzero(frame), 0)

    def test_legacy_gate_overlay_still_draws(self):
        frame = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        legacy = LineCrossing(orientation="vertical", position=0.5)
        _draw_gate_overlay(frame, legacy, "entry")
        self.assertGreater(np.count_nonzero(frame), 0)
        self.assertTrue(np.any(frame[:, 50]))

    def _track_crossing_vertical_line(self, camera_role, reverse=False):
        # New gate at x=25, deliberately not the old center line at x=50.
        line = LineCrossing(
            line_points=(0.25, 0.1, 0.25, 0.9),
            inside_side=-1,
            cooldown_frames=10,
        )
        track = PersonTrack(
            track_id=7,
            bbox=(5, 30, 15, 60),
            hits=1,
            age=1,
            misses=0,
        )
        events = []
        # Foot moves across x=25; reverse direction is a true exit.
        boxes = [
            (5, 30, 15, 60),
            (10, 30, 20, 60),
            (15, 30, 25, 60),
            (20, 30, 30, 60),
            (25, 30, 35, 60),
            (30, 30, 40, 60),
            (35, 30, 45, 60),
        ]
        if reverse:
            boxes.reverse()
        for hit, bbox in enumerate(boxes, start=1):
            track.bbox = bbox
            track.hits = hit
            track.age = hit
            direction = _check_camera_gate(
                line, track, FRAME_SHAPE, hit * 3, camera_role
            )
            if direction is not None:
                events.append(direction)
        return track, events

    def test_real_track_sequence_uses_new_custom_position(self):
        _, events = self._track_crossing_vertical_line("entry")
        self.assertEqual(events, ["enter"])

    def test_exit_only_camera_filters_entry_but_does_not_consume_cooldown(self):
        track, events = self._track_crossing_vertical_line("exit")
        self.assertEqual(events, [])
        self.assertIsNone(track.last_crossing)
        self.assertLess(track.last_crossing_frame, 0)

    def test_real_track_sequence_emits_exit_for_exit_only_camera(self):
        _, events = self._track_crossing_vertical_line("exit", reverse=True)
        self.assertEqual(events, ["exit"])

    def test_entry_only_camera_filters_outbound_crossing(self):
        track, events = self._track_crossing_vertical_line("entry", reverse=True)
        self.assertEqual(events, [])
        self.assertIsNone(track.last_crossing)
        self.assertLess(track.last_crossing_frame, 0)

    def test_crossing_flows_into_real_attendance_event_engine(self):
        track, directions = self._track_crossing_vertical_line("entry")
        self.assertEqual(directions, ["enter"])
        track.student_id = 123
        track.student_code = "TEST123"
        track.student_name = "Test Student"
        track.identity_confidence = 0.99
        track.metadata["face_witnessed_for_crossing"] = True
        events = []
        marked = []
        def commit_transition(**payload):
            marked.append(payload)
            events.append({'event_type': EVENT_PERSON_ENTERED})
            return MovementTransitionResult(True, True, ((1, EVENT_PERSON_ENTERED, {}),))
        engine = AttendanceEventEngine(
            add_event_callback=lambda **payload: events.append(payload),
            commit_transition_callback=commit_transition,
            get_setting_callback=lambda key, default=None: {
                "school_start_time": "23:59",
                "enable_late_events": "1",
            }.get(key, default),
            state=AttendancePresenceState(),
        )
        emitted = engine.on_crossing(track, directions[0], camera_id=0)
        self.assertIn(EVENT_PERSON_ENTERED, emitted)
        self.assertTrue(any(item["event_type"] == EVENT_PERSON_ENTERED for item in events))
        self.assertEqual(marked[0]['student_id'], 123)
        self.assertIn(123, engine.present)


if __name__ == "__main__":
    unittest.main()
