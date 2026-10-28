import unittest

from src.engine.gate_config import load_drawn_gate_line, save_drawn_gate_line
from src.engine.smart_tracking import LineCrossing, PersonTrack


class DrawnGateConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.get_setting = lambda key, default=None: self.store.get(key, default)
        self.set_setting = lambda key, value: self.store.__setitem__(key, str(value))

    def test_new_line_is_persisted_and_loaded_at_rounded_coordinates(self):
        points = (0.27381947, 0.14726318, 0.68102479, 0.92561734)
        saved_points, saved_side = save_drawn_gate_line(
            1, points, -1, self.set_setting
        )
        loaded_points, loaded_side = load_drawn_gate_line(1, self.get_setting)
        self.assertEqual(self.store["camera_1_line_mode"], "custom")
        self.assertEqual(saved_side, -1)
        self.assertEqual(loaded_side, -1)
        self.assertEqual(loaded_points, tuple(round(value, 6) for value in points))
        self.assertTrue(all(abs(a - b) <= 0.000001 for a, b in zip(saved_points, loaded_points)))

    def test_new_saved_line_replaces_old_coordinates(self):
        save_drawn_gate_line(1, (0.5, 0.1, 0.5, 0.9), -1, self.set_setting)
        save_drawn_gate_line(1, (0.25, 0.15, 0.30, 0.85), 1, self.set_setting)
        points, side = load_drawn_gate_line(1, self.get_setting)
        self.assertEqual(points, (0.25, 0.15, 0.3, 0.85))
        self.assertEqual(side, 1)

    def test_clearing_line_switches_back_to_legacy_mode(self):
        save_drawn_gate_line(2, (0.5, 0.1, 0.5, 0.9), -1, self.set_setting)
        points, side = save_drawn_gate_line(2, None, None, self.set_setting)
        self.assertIsNone(points)
        self.assertIsNone(side)
        self.assertEqual(self.store["camera_2_line_mode"], "legacy")
        self.assertEqual(load_drawn_gate_line(2, self.get_setting), (None, None))

    def test_invalid_degenerate_line_is_not_activated(self):
        points, side = save_drawn_gate_line(1, (0.5, 0.5, 0.5, 0.5), 1, self.set_setting)
        self.assertIsNone(points)
        self.assertIsNone(side)
        self.assertEqual(self.store["camera_1_line_mode"], "legacy")

    def test_invalid_stored_line_is_not_passed_to_worker(self):
        self.store.update({
            "camera_1_line_mode": "custom",
            "camera_1_line_x1": "0.5",
            "camera_1_line_y1": "0.5",
            "camera_1_line_x2": "0.5",
            "camera_1_line_y2": "0.5",
            "camera_1_inside_side": "1",
        })
        self.assertEqual(load_drawn_gate_line(1, self.get_setting), (None, None))

    def test_saved_new_position_reaches_crossing_engine(self):
        # This is the storage -> reload -> detector path, not a drawn-line mock.
        save_drawn_gate_line(
            1, (0.25, 0.1, 0.25, 0.9), -1, self.set_setting
        )
        points, side = load_drawn_gate_line(1, self.get_setting)
        line = LineCrossing(line_points=points, inside_side=side, cooldown_frames=5)
        track = PersonTrack(
            track_id=1,
            bbox=(25, 40, 35, 60),
            hits=3,
            age=3,
            misses=0,
            previous_foot=(20.0, 60.0),
        )
        direction = line.check(track, (100, 100, 3), 20)
        self.assertEqual(points, (0.25, 0.1, 0.25, 0.9))
        self.assertEqual(direction, "enter")


if __name__ == "__main__":
    unittest.main()
