import unittest

from src.smart_tracking import LineCrossing, PersonTrack


FRAME_SHAPE = (100, 100, 3)


def make_track(previous_foot, current_foot):
    x, y = current_foot
    track = PersonTrack(
        track_id=1,
        bbox=(int(x) - 1, 0, int(x) + 1, int(y)),
        hits=3,
        age=3,
        misses=0,
        previous_foot=previous_foot,
    )
    return track


class DrawnLineCrossingTests(unittest.TestCase):
    def setUp(self):
        # A short diagonal segment, from (25, 25) to (75, 75) in a 100x100 frame.
        self.line = LineCrossing(
            line_points=(0.25, 0.25, 0.75, 0.75),
            inside_side=-1,
            cooldown_frames=10,
        )

    def test_outside_to_inside_is_entry(self):
        track = make_track((30, 70), (70, 30))
        self.assertEqual(self.line.check(track, FRAME_SHAPE, 100), "enter")
        self.assertEqual(track.last_crossing, "enter")

    def test_inside_to_outside_is_exit(self):
        track = make_track((70, 30), (30, 70))
        track.metadata["_gate_line_side"] = -1
        track.last_crossing_frame = 0
        self.assertEqual(self.line.check(track, FRAME_SHAPE, 100), "exit")

    def test_crossing_infinite_extension_does_not_count(self):
        # This movement crosses the infinite diagonal, but before its endpoint.
        track = make_track((10, 30), (30, 10))
        self.assertIsNone(self.line.check(track, FRAME_SHAPE, 100))

    def test_legacy_vertical_line_still_works(self):
        legacy = LineCrossing(
            orientation="vertical",
            position=0.5,
            enter_direction="positive",
            primary_direction="enter",
            cooldown_frames=10,
        )
        track = make_track((40, 50), (60, 50))
        self.assertEqual(legacy.check(track, FRAME_SHAPE, 100), "enter")

    def test_invalid_custom_geometry_falls_back_to_legacy(self):
        line = LineCrossing(
            orientation="vertical",
            position=0.5,
            primary_direction="enter",
            line_points=(0.5, 0.5, 0.5, 0.5),
            inside_side=1,
        )
        self.assertIsNone(line.line_points)
        track = make_track((40, 50), (60, 50))
        self.assertEqual(line.check(track, FRAME_SHAPE, 100), "enter")


if __name__ == "__main__":
    unittest.main()
