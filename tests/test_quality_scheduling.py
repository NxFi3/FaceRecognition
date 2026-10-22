from concurrent.futures import Future
from types import SimpleNamespace
import numpy as np
from src.runtime import face_observation_due, face_priority
from src.smart_tracking import PersonTrack, SmartPersonTracker, MAX_MISSES
from src.global_identity import GlobalIdentityManager
from src.smart_events import AttendanceEventEngine, AttendancePresenceState


FRAME = np.zeros((200, 200, 3), np.uint8)
DETECTION = {'bbox': (10, 10, 80, 150), 'confidence': .9}


class Appearance:
    def __init__(self): self.calls = 0
    def embed_batch(self, crops):
        self.calls += 1
        return np.tile(np.array([1., 0., 0.], np.float32), (len(crops), 1))


def test_unknown_gets_three_opportunities_without_completion_cooldown():
    track = PersonTrack(1, DETECTION['bbox'], hits=2)
    attempts = []
    for frame_no in range(1, 9):
        if frame_no % 2 == 0 and face_observation_due(track, frame_no, .6):
            attempts.append(frame_no)
            track.metadata['face_submitted_frame'] = frame_no
            track.last_face_frame = frame_no + 1  # async completion must not add a cooldown
    assert attempts == [2, 4, 6, 8]
    track.student_id = 10
    track.metadata['face_submitted_quality'] = .6
    assert not face_observation_due(track, 10, .6)
    assert face_observation_due(track, 14, .8)  # better sample still gets a chance
    assert face_observation_due(track, 38, .6)  # bounded periodic refresh


def test_priority_favours_unknown_then_longest_wait_without_quality_starvation():
    known = PersonTrack(1, DETECTION['bbox'], student_id=1)
    new = PersonTrack(2, DETECTION['bbox'])
    recent = PersonTrack(3, DETECTION['bbox'])
    recent.metadata['face_submitted_frame'] = 8
    assert face_priority(new, False, .2) < face_priority(known, True, 1.)
    assert face_priority(new, False, .2) < face_priority(recent, True, 1.)


def test_repeat_detection_or_face_evidence_survives_short_occlusion():
    tracker = SmartPersonTracker()
    repeat = PersonTrack(1, DETECTION['bbox'], hits=2)
    face = PersonTrack(2, DETECTION['bbox'])
    face.metadata['face_submitted_frame'] = 2
    noise = PersonTrack(3, DETECTION['bbox'])
    tracker.tracks = {1: repeat, 2: face, 3: noise}
    for frame_no in (3, 6, 9):
        tracker.update([], FRAME, frame_no)
    assert set(tracker.tracks) == {1, 2}
    for frame_no in range(4, MAX_MISSES + 2):
        tracker.update([], FRAME, frame_no)
    assert not tracker.tracks


def test_reid_initial_refresh_and_missed_modulo_ticks():
    appearance = Appearance()
    tracker = SmartPersonTracker(appearance, reid_interval=6)
    observed = []
    # None of these detection ticks lands on a multiple of six.
    for frame_no in (1, 4, 7, 10, 13):
        track = tracker.update([DETECTION], FRAME, frame_no)[0]
        if track.last_reid_frame == frame_no:
            observed.append(frame_no)
    assert observed == [1, 4, 10]
    assert track.hits == 5 and track.metadata['appearance_samples'] == 3
    assert appearance.calls == 3
    track.student_id = 4
    tracker.update([DETECTION], FRAME, 16)
    assert appearance.calls == 3  # stable cadence reduced
    tracker.update([DETECTION], FRAME, 19, reid_priority=True)
    assert appearance.calls == 4  # explicit region transition refresh


def test_cross_camera_continuity_exactly_once_without_identity_promotion(worker_module):
    manager = GlobalIdentityManager()
    manager.register_camera(1, 'entry')
    manager.register_camera(2, 'exit')
    source_tracker = SmartPersonTracker(Appearance())
    source = source_tracker.update([DETECTION], FRAME, 1)[0]
    source_tracker.update([DETECTION], FRAME, 4)
    source.student_id, source.student_code, source.student_name = 7, 'S7', 'Synthetic'
    source_worker = worker_module.SmartCameraWorker(camera_slot=1, identity_manager=manager)
    # Simulate cache confirmation arriving AFTER the source has disappeared.
    source_tracker.tracks.clear()
    source_worker._register_confirmed_track(source, 1, source_tracker)
    assert ('1', source.track_id) not in manager._track_links
    destination_tracker = SmartPersonTracker(Appearance())
    destination = destination_tracker.update([DETECTION], FRAME, 1)[0]
    dest_worker = worker_module.SmartCameraWorker(camera_slot=2, identity_manager=manager)
    dest_worker._observe_global_track(destination, 2)
    first = destination.metadata['pending_global_identity']
    assert first['student_id'] == 7 and first['source_camera'] == '1'
    assert manager._track_links[('2', destination.track_id)] == 7
    dest_worker._observe_global_track(destination, 2)
    assert destination.metadata['pending_global_identity'] is first
    assert destination.student_id is None  # existing attendance proof policy remains intact


def test_pending_cache_exit_is_emitted_once_after_identity(worker_module):
    events = []
    state = AttendancePresenceState(present={7})
    engine = AttendanceEventEngine(lambda **event: events.append(event), lambda *a: True,
                                   lambda key, default: '0' if key == 'enable_early_events' else default, state)
    track = PersonTrack(1, DETECTION['bbox'], last_crossing='exit')
    track.metadata['pending_crossing'] = {'direction': 'exit'}
    engine.on_face_identity(track, 7, 'S7', 'Synthetic', .9, 2)
    assert 7 in state.present  # deferred crossing owns this event
    pending = track.metadata.pop('pending_crossing')
    engine.on_crossing(track, pending['direction'], 2)
    types = [event['event_type'] for event in events]
    assert types.count('PERSON_EXITED') == 1 and 'UNEXPECTED_EXIT' not in types


def test_cache_reuses_exact_observation_result_without_mutation(worker_module):
    Worker = worker_module.SmartCameraWorker
    class Matcher:
        def __init__(self): self.calls = 0
        def recognize_crop_with_embedding(self, crop, bbox):
            self.calls += 1
            return np.array([3., 0., 0.], np.float32), (0, .2, .1)
    matcher = Matcher()
    track = PersonTrack(1, DETECTION['bbox'])
    futures = []
    for n in range(3):
        crop = np.full((240, 240, 3), n, np.uint8)
        future = Future()
        future.set_result(Worker._recognize_task(matcher, crop, (10, 10, 200, 200), 1))
        futures.append(future)
        compact, bbox = Worker._compact_face_sample(crop, (10, 10, 200, 200))
        track.cache_face_sample(compact, bbox, .8, n, recognition_future=future, matcher=matcher)
    result = Worker._finalize_face_cache_task(matcher, track.snapshot_face_cache())
    again = Worker._finalize_face_cache_task(matcher, track.snapshot_face_cache())
    assert result == again and result['votes'] == 3
    assert matcher.calls == 3  # three live observations, zero finalization re-encodings
    assert all(f.result()[1][0] == 3 for f in futures)  # normalization is local
    # No descriptor or result is shared with another enrollment snapshot/matcher.
    other = Matcher()
    Worker._finalize_face_cache_task(other, track.snapshot_face_cache())
    assert other.calls == 3


def test_cache_fallback_and_reused_paths_preserve_voting_result(worker_module):
    Worker = worker_module.SmartCameraWorker
    class Matcher:
        def recognize_crop_with_embedding(self, crop, bbox):
            return np.array([1., 0.], np.float32), (0, .2, .1)
    matcher = Matcher()
    track = PersonTrack(1, DETECTION['bbox'])
    for n in range(3):
        track.cache_face_sample(FRAME, (0, 0, 100, 100), .8, n)
    expected = Worker._finalize_face_cache_task(matcher, track.snapshot_face_cache())
    for sample in track.face_cache:
        future = Future()
        future.set_result(Worker._recognize_task(matcher, sample['crop'], sample['bbox'], 1))
        sample.update(recognition_future=future, matcher=matcher)
    assert Worker._finalize_face_cache_task(matcher, track.snapshot_face_cache()) == expected
