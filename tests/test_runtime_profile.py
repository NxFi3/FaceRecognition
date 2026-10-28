"""Telemetry contracts; no cameras, models, Qt or biometric decisions required."""
import gc
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
from unittest.mock import Mock, patch

import numpy as np
import unittest
import tempfile
from pathlib import Path

from src.engine.runtime_profile import Histogram, RuntimeProfile, profile
from src.engine.camera_capture import LatestFrameCapture
from tools.profile_runtime import Reporter, positive_number


def enabled():
    collector = RuntimeProfile()
    collector.enable()
    return collector


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)

    def test_disabled_passthrough_preserves_object_exception_and_submission(self):
        collector = RuntimeProfile()
        payload = object()
        function = Mock(return_value=payload)
        assert collector.call("stage", function, payload, flag=True) is payload
        function.assert_called_once_with(payload, flag=True)
        failure = RuntimeError("original")
        with self.assertRaises(RuntimeError) as raised:
            collector.call("stage", Mock(side_effect=failure))
        assert raised.exception is failure
        executor = Mock()
        result = collector.submit(executor, "face_recognition", function, payload)
        assert result is executor.submit.return_value
        executor.submit.assert_called_once_with(function, payload)
        collector.count("ignored")
        collector.observe("ignored", 1)
        assert collector.snapshot()["cameras"]["aggregate"]["counters"] == {}


    def test_histogram_bounded_weighted_aggregate_and_non_destructive_snapshot(self):
        collector = enabled()
        with collector.scope(1):
            for _ in range(1000):
                collector.observe("person_detector", 10)
        with collector.scope(2):
            collector.observe("person_detector", 100)
        report = collector.snapshot()
        timing = report["cameras"]["aggregate"]["timings"]["person_detector"]
        assert timing["count"] == 1001
        self.assertAlmostEqual(timing["mean_ms"], 10100 / 1001)
        assert 10 <= timing["p95_ms"] <= 11
        assert len(collector._timings["1"]["person_detector"].bins) == 256
        assert collector.snapshot()["cameras"]["aggregate"]["timings"] == report["cameras"]["aggregate"]["timings"]
        histogram = Histogram()
        histogram.add(0)
        histogram.add(1e12)
        assert histogram.report()["max_ms"] == 1e12


    def test_nested_scopes_and_stage_exception_are_observed_without_swallowing(self):
        collector = enabled()
        failure = ValueError("unchanged")
        @collector.in_camera(lambda: 2)
        @collector.stage("failing")
        def function():
            raise failure
        with collector.scope(1):
            with self.assertRaises(ValueError) as raised:
                function()
            assert raised.exception is failure
            assert collector.camera() == "1"
        assert collector.camera() == "application"
        assert collector.snapshot()["cameras"]["2"]["timings"]["failing"]["count"] == 1


    def test_two_camera_queue_cancel_failures_context_and_simultaneous_peak(self):
        collector = enabled()
        release = threading.Event()
        started = threading.Event()
        payload = object()
        def blocked():
            assert collector.camera() == "1"
            started.set()
            assert release.wait(2)
            return payload
        def fail():
            assert collector.camera() == "2"
            raise ValueError("task failed")
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                with collector.scope(1):
                    first = collector.submit(executor, "face_recognition", blocked)
                assert started.wait(2)
                with collector.scope(2):
                    cancelled = collector.submit(executor, "face_recognition", lambda: None)
                    finalizer = collector.submit(executor, "cache_finalization", fail)
                gauges = collector.snapshot()["cameras"]["aggregate"]["gauges"]
                assert gauges["downstream.active"]["current"] == 1
                assert gauges["downstream.pending"]["current"] == 2
                assert cancelled.cancel()
                assert cancelled.cancel()  # callback must not count twice
            finally:
                release.set()
            assert first.result(2) is payload
            with self.assertRaisesRegex(ValueError, "task failed"):
                finalizer.result(2)
        report = collector.snapshot()
        agg = report["cameras"]["aggregate"]
        assert agg["gauges"]["downstream.active"] == {"current": 0, "peak": 1}
        assert agg["gauges"]["downstream.pending"]["current"] == 0
        assert agg["counters"]["face_recognition.cancelled"] == 1
        assert agg["counters"]["cache_finalization.failed"] == 1
        assert report["cameras"]["2"]["timings"]["cache_finalization.queue_delay"]["count"] == 1
        with self.assertRaises(RuntimeError):
            collector.submit(executor, "face_recognition", lambda: None)
        assert collector.snapshot()["cameras"]["aggregate"]["gauges"]["downstream.pending"]["current"] == 0


    def test_capture_publishes_latest_reference_and_counts_sequence_gaps(self):
        # Use the singleton captured by decorators, restoring it after this test.
        profile.enabled = True
        burst = threading.Event()
        burst_done = threading.Event()
        unblock = threading.Event()
        frames = [np.zeros((2, 2, 3), np.uint8) + i for i in range(4)]
        class Camera:
            calls = 0
            def read(self):
                index = self.calls
                self.calls += 1
                if index == 1:
                    assert burst.wait(2)
                if index == 4:
                    burst_done.set()
                    unblock.wait(2)
                    return False, None
                return True, frames[index]
            def get(self, key):
                return 2
            def release(self):
                unblock.set()
        camera = Camera()
        try:
            with profile.scope("capture-test"):
                capture = LatestFrameCapture(0, 30, lambda *_: camera)
                capture.start()
                try:
                    sequence, frame = capture.wait_for_frame(0)
                    assert sequence == 1 and frame is frames[0]
                    profile.frame_done()
                    burst.set()
                    assert burst_done.wait(2)
                    sequence, frame = capture.wait_for_frame(sequence)
                    assert sequence == 4 and frame is frames[3]
                    profile.frame_done()
                    assert capture.wait_for_frame(sequence, .01) == (None, None)
                    report = profile.snapshot()["cameras"]["capture-test"]
                    assert report["counters"]["captured_frames"] == 4
                    assert report["counters"]["processed_frames"] == 2
                    assert report["counters"]["skipped_capture_frames"] == 2
                    assert report["timings"]["frame_age_at_consume"]["count"] == 2
                finally:
                    capture._stop_event.set()
                    unblock.set()
                    capture.stop()
                assert capture._thread is None
        finally:
            profile.enabled = False
            burst.set()
            unblock.set()


    def test_time_to_identity_keeps_no_track_alive_or_mutates_metadata(self):
        collector = enabled()
        @dataclass
        class Track:
            student_id: int | None = None
        class Engine:
            @collector.identity("face")
            def apply(self, track):
                track.student_id = 10
                return "original result"
        with collector.scope(1):
            collector.details(test=True)
            track = Track()
            collector.track_created(track)
            assert Engine().apply(track) == "original result"
            Engine().apply(track)
            assert vars(track) == {"student_id": 10}
            report = collector.snapshot()["cameras"]["1"]
            assert report["timings"]["time_to_identity.face"]["count"] == 1
            assert report["timings"]["time_to_identity.first"]["count"] == 1
            del track
            gc.collect()
            assert not collector._tracks
            unknown = Track()
            collector.track_created(unknown)
            del unknown
            gc.collect()
            assert collector.snapshot()["cameras"]["1"]["counters"]["tracks_retired_without_any_identity"] == 1


    def test_reporter_writes_jsonl_summary_and_does_not_stop_runtime(self):
        tmp_path = self.tmp_path
        collector = enabled()
        collector.first_frame_at = time.perf_counter() - 1
        path = tmp_path / "result.jsonl"
        output = path.open("w")
        reporter = Reporter(collector, output, seconds=.01, interval=.005)
        reporter.start()
        reporter.thread.join(2)
        assert not reporter.thread.is_alive()
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert records[-1]["type"] == "summary"
        assert records[-1]["reason"] == "duration_complete"
        assert not collector.enabled
        assert reporter.error is None
        assert "threads" in records[-1]["process"]


    def test_reporter_startup_timeout_and_io_failure_disable_only_telemetry(self):
        tmp_path = self.tmp_path
        collector = enabled()
        output = (tmp_path / "empty.jsonl").open("w")
        reporter = Reporter(collector, output, seconds=120, interval=.005, startup_timeout=.001)
        reporter.start()
        reporter.thread.join(2)
        assert not collector.enabled
        assert "no_processed_frames_before_timeout" in (tmp_path / "empty.jsonl").read_text()
        collector = enabled()
        output = Mock()
        output.write.side_effect = OSError("disk full")
        reporter = Reporter(collector, output, seconds=120, interval=.005)
        reporter.start()
        reporter.thread.join(2)
        assert reporter.error == "disk full" and not collector.enabled
        output.close.assert_called_once()


    def test_cli_rejects_invalid_durations(self):
        import argparse
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_number(value)


    def test_db_wrappers_preserve_attendance_uniqueness_and_events(self):
        tmp_path = self.tmp_path
        from src import database
        previous = profile.enabled
        profile.enabled = True
        try:
            with patch.object(database, "DB_PATH", tmp_path / "attendance.db"), profile.scope("db-test"):
                database.init_db()
                student_id = database.add_student("TEST", "Test")
                assert database.mark_attendance(student_id)
                assert not database.mark_attendance(student_id)
                event_id = database.add_event("PERSON_ENTERED", student_id=student_id)
                assert event_id is not None
                rows = database.get_recent_events()
                assert len(rows) == 1 and rows[0][0] == event_id
                report = profile.snapshot()["cameras"]["db-test"]["timings"]
                assert report["db_attendance"]["count"] == 2
                assert report["db_event"]["count"] == 1
        finally:
            profile.enabled = previous


    def test_cross_camera_and_osnet_observations_preserve_opaque_results(self):
        from src.engine.global_identity import GlobalIdentityManager
        from src.engine.smart_events import AttendancePresenceState
        from src.engine.smart_tracking import PersonTrack
        from src.engine.reid import OSNetReID

        def handoff():
            state = AttendancePresenceState()
            state.present.add(7)
            manager = GlobalIdentityManager(presence_state=state)
            manager.register_camera(1, "entry")
            manager.register_camera(2, "exit")
            entry = PersonTrack(1, (0, 0, 80, 160), appearance=np.array([1., 0.]))
            manager.register_face_identity(entry, 1, 7, "test", "Test", now=1)
            target = PersonTrack(2, (0, 0, 80, 160), appearance=np.array([1., 0.]))
            return manager.observe_track(target, 2, now=2)

        profile.enabled = False
        baseline = handoff()
        assert baseline is not None
        model = OSNetReID.__new__(OSNetReID)
        model._extractor = Mock(return_value=np.array([[3., 4.]], dtype=np.float32))
        crop = np.zeros((2, 2, 3), np.uint8)
        original = model.embed_batch([crop])
        profile.enabled = True
        try:
            with profile.scope("opaque-test"):
                assert handoff() == baseline
                np.testing.assert_array_equal(model.embed_batch([crop]), original)
                report = profile.snapshot()["cameras"]["opaque-test"]
                assert report["counters"]["cross_camera_candidates"] == 1
                assert report["counters"]["cross_camera_links"] == 1
                assert report["timings"]["osnet_batch"]["count"] == 1
        finally:
            profile.enabled = False


    def test_preview_repeat_counts_and_interval_fps(self):
        collector = enabled()
        with collector.scope(1):
            collector.count("captured_frames", 30)
            collector.count("processed_frames", 25)
            collector.rendered(7, time.monotonic(), 1)
            collector.rendered(7, time.monotonic(), 1)
            collector.rendered(8, time.monotonic(), 1)
        first = collector.snapshot()
        camera = first["cameras"]["1"]
        self.assertAlmostEqual(camera["capture_fps"] * first["interval_s"], 30)
        self.assertAlmostEqual(camera["processed_fps"] * first["interval_s"], 25)
        assert camera["counters"]["gui_repeated_renders"] == 1
        assert collector.snapshot()["cameras"]["1"]["processed_fps"] == 0


    def test_cli_launches_normal_entrypoint_and_restores_arguments(self):
        tmp_path = self.tmp_path
        import sys
        from types import ModuleType
        from tools import profile_runtime
        collector = RuntimeProfile()
        self.enterContext(patch.object(profile_runtime, "profile", collector))
        app = ModuleType("src.app")
        calls = []
        def main():
            calls.append(list(sys.argv[1:]))
            assert collector.enabled
        app.main = main
        self.enterContext(patch.dict(sys.modules, {"src.app": app}))
        previous = sys.argv
        output = tmp_path / "baseline.jsonl"
        profile_runtime.main(["--seconds", "120", "--output", str(output), "--open-reports"])
        assert calls == [["--open-reports"]]
        assert sys.argv is previous
        assert not collector.enabled
        records = [json.loads(line) for line in output.read_text().splitlines()]
        assert records[0]["type"] == "metadata"
        assert records[-1]["reason"] == "application_closed"
        with self.assertRaises(FileExistsError):
            profile_runtime.main(["--output", str(output)])
        assert len(calls) == 1



if __name__ == "__main__":
    unittest.main()
