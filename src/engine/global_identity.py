"""Global cross-camera identity linking for the two-camera attendance setup.

The module deliberately treats face recognition as the strongest identity signal and
uses OSNet person Re-ID only as a conservative bridge between camera-local tracks.
A weak appearance match is never forced into a student identity.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import RLock
import time

import numpy as np


DEFAULT_WINDOW_SEC = 15.0
DEFAULT_MIN_SIMILARITY = 0.82
DEFAULT_STRONG_SIMILARITY = 0.88
DEFAULT_MIN_MARGIN = 0.045
MAX_PROFILE_EMBEDDINGS = 8
MAX_TRACK_AGE_SEC = 90.0


@dataclass(frozen=True)
class IdentityMatch:
    student_id: int
    student_code: str
    student_name: str
    similarity: float
    score: float
    source_camera: str
    source_track_id: int
    reason: str


@dataclass
class _StudentProfile:
    student_id: int
    student_code: str
    student_name: str
    embeddings: deque = field(
        default_factory=lambda: deque(maxlen=MAX_PROFILE_EMBEDDINGS)
    )
    last_seen: float = 0.0
    last_camera: str = ""
    last_track_id: int = -1


class GlobalIdentityManager:
    """Thread-safe shared identity memory for all active camera workers."""

    def __init__(self, presence_state=None, setting_callback=None):
        self.presence_state = presence_state
        self.setting_callback = setting_callback
        self._lock = RLock()
        self._profiles: dict[int, _StudentProfile] = {}
        self._track_links: dict[tuple[str, int], int] = {}
        self._track_last_seen: dict[tuple[str, int], float] = {}
        self._camera_roles: dict[str, str] = {}

        # Settings are read from SQLite. Cache them briefly so the hot
        # per-track path does not open/query the database on every frame.
        self._settings_cache = {}
        self._settings_cache_time = 0.0
        self._settings_cache_ttl = 1.0

    def register_camera(self, camera_id, role):
        """Register the semantic role of a camera for safe hand-off gating."""
        role = str(role or "observe")
        if role not in {"entry", "exit", "observe"}:
            role = "observe"
        with self._lock:
            self._camera_roles[str(camera_id)] = role

    @staticmethod
    def _normalise(embedding):
        if embedding is None:
            return None
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.size == 0:
            return None
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-8:
            return None
        return vector / norm

    @staticmethod
    def _cosine(a, b):
        if a is None or b is None:
            return 0.0
        return float(np.dot(a, b))

    def _refresh_settings_cache(self, now):
        if (
            self.setting_callback is None
            or now - self._settings_cache_time < self._settings_cache_ttl
        ):
            return

        values = {}
        keys = (
            "global_identity_enabled",
            "global_identity_window_sec",
            "global_identity_similarity",
            "global_identity_strong_similarity",
            "global_identity_min_margin",
        )
        for key in keys:
            try:
                values[key] = self.setting_callback(key, None)
            except Exception:
                values[key] = None

        self._settings_cache = values
        self._settings_cache_time = now

    def _setting(self, key, default, now=None):
        if self.setting_callback is None:
            return default
        if now is None:
            now = time.monotonic()

        with self._lock:
            self._refresh_settings_cache(now)
            value = self._settings_cache.get(key, None)

        return default if value is None else value

    def _enabled(self, now=None):
        value = self._setting("global_identity_enabled", "1", now)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _window_sec(self, now=None):
        try:
            return max(
                1.0,
                min(
                    120.0,
                    float(
                        self._setting(
                            "global_identity_window_sec",
                            DEFAULT_WINDOW_SEC,
                            now,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_WINDOW_SEC

    def _min_similarity(self, now=None):
        try:
            return max(
                0.70,
                min(
                    0.97,
                    float(
                        self._setting(
                            "global_identity_similarity",
                            DEFAULT_MIN_SIMILARITY,
                            now,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_MIN_SIMILARITY

    def _presence_snapshot(self):
        if self.presence_state is None:
            return set()
        lock = getattr(self.presence_state, "lock", None)
        if lock is None:
            return set(getattr(self.presence_state, "present", set()))
        with lock:
            return set(getattr(self.presence_state, "present", set()))

    def _profile_for(self, student_id, student_code="", student_name=""):
        profile = self._profiles.get(int(student_id))
        if profile is None:
            profile = _StudentProfile(
                student_id=int(student_id),
                student_code=str(student_code or ""),
                student_name=str(student_name or ""),
            )
            self._profiles[int(student_id)] = profile
        else:
            if student_code:
                profile.student_code = str(student_code)
            if student_name:
                profile.student_name = str(student_name)
        return profile

    def _add_embedding_locked(self, profile, embedding):
        vector = self._normalise(embedding)
        if vector is None:
            return
        for existing in profile.embeddings:
            if self._cosine(existing, vector) >= 0.995:
                return
        profile.embeddings.append(vector)

    def register_face_identity(
        self,
        track,
        camera_id,
        student_id,
        student_code,
        student_name,
        now=None,
    ):
        """Register a face-confirmed identity as a trusted appearance anchor."""
        timestamp = time.monotonic() if now is None else float(now)
        camera_key = str(camera_id)
        track_key = (camera_key, int(track.track_id))

        with self._lock:
            profile = self._profile_for(
                student_id,
                student_code,
                student_name,
            )
            self._add_embedding_locked(profile, getattr(track, "appearance", None))
            profile.last_seen = timestamp
            profile.last_camera = camera_key
            profile.last_track_id = int(track.track_id)

            self._track_links[track_key] = int(student_id)
            self._track_last_seen[track_key] = timestamp
            self._prune_tracks_locked(timestamp)

    def observe_track(self, track, camera_id, now=None):
        """Observe a camera-local track and conservatively resolve an unknown one."""
        timestamp = time.monotonic() if now is None else float(now)
        camera_key = str(camera_id)
        track_key = (camera_key, int(track.track_id))

        with self._lock:
            self._prune_tracks_locked(timestamp)

            student_id = getattr(track, "student_id", None)
            if student_id is not None:
                profile = self._profile_for(
                    student_id,
                    getattr(track, "student_code", "") or "",
                    getattr(track, "student_name", "") or "",
                )
                self._add_embedding_locked(profile, getattr(track, "appearance", None))
                profile.last_seen = timestamp
                profile.last_camera = camera_key
                profile.last_track_id = int(track.track_id)
                self._track_links[track_key] = int(student_id)
                self._track_last_seen[track_key] = timestamp
                return None

            if not self._enabled(timestamp):
                return None

            query = self._normalise(getattr(track, "appearance", None))
            if query is None:
                return None

            window_sec = self._window_sec(timestamp)
            min_similarity = self._min_similarity(timestamp)

            current_role = self._camera_roles.get(camera_key, "observe")
            candidates = []
            for profile in self._profiles.values():
                if not profile.embeddings:
                    continue

                same_camera_link = any(
                    linked_student == profile.student_id
                    and key[0] == camera_key
                    for key, linked_student in self._track_links.items()
                )
                if same_camera_link:
                    continue

                age = (
                    timestamp - profile.last_seen
                    if profile.last_seen
                    else float("inf")
                )
                source_camera = str(profile.last_camera or "")
                source_role = self._camera_roles.get(source_camera, "observe")
                recent_other_camera = (
                    bool(source_camera)
                    and source_camera != camera_key
                    and age <= window_sec
                )

                # Cross-camera identity is a real hand-off, not a global
                # "who is currently present?" lookup. For the two-camera school
                # setup, entry -> exit and exit -> entry are the intended paths.
                # This prevents an arbitrary student who is merely present inside
                # the school from being assigned to an unrelated unknown track.
                complementary_roles = (
                    {source_role, current_role} == {"entry", "exit"}
                )
                observe_handoff = (
                    "observe" in {source_role, current_role}
                    and recent_other_camera
                )
                if not recent_other_camera:
                    continue
                if not complementary_roles and not observe_handoff:
                    continue

                similarity = max(
                    self._cosine(query, reference)
                    for reference in profile.embeddings
                )
                if similarity < min_similarity:
                    continue

                # A recent complementary-camera hand-off gets a small
                # score boost, but appearance similarity remains dominant.
                bonus = 0.030
                reasons = ["recent-handoff"]

                score = similarity + bonus
                candidates.append(
                    (
                        score,
                        similarity,
                        profile,
                        str(profile.last_camera),
                        int(profile.last_track_id),
                        ",".join(reasons),
                    )
                )

            if not candidates:
                return None

            candidates.sort(key=lambda item: item[0], reverse=True)
            best = candidates[0]
            second_score = candidates[1][0] if len(candidates) > 1 else None
            best_score, best_similarity, profile, source_camera, source_track, reason = best

            strong_similarity = max(
                min_similarity,
                min(
                    0.97,
                    float(
                        self._setting(
                            "global_identity_strong_similarity",
                            DEFAULT_STRONG_SIMILARITY,
                            timestamp,
                        )
                    ),
                ),
            )
            min_margin = max(
                0.0,
                min(
                    0.20,
                    float(
                        self._setting(
                            "global_identity_min_margin",
                            DEFAULT_MIN_MARGIN,
                            timestamp,
                        )
                    ),
                ),
            )

            if best_similarity < strong_similarity:
                if second_score is not None and best_score - second_score < min_margin:
                    return None

            self._track_links[track_key] = profile.student_id
            self._track_last_seen[track_key] = timestamp

            return IdentityMatch(
                student_id=profile.student_id,
                student_code=profile.student_code,
                student_name=profile.student_name,
                similarity=best_similarity,
                score=best_score,
                source_camera=source_camera,
                source_track_id=source_track,
                reason=reason or "appearance",
            )

    def track_lost(self, track, camera_id):
        camera_key = str(camera_id)
        key = (camera_key, int(track.track_id))
        with self._lock:
            self._track_links.pop(key, None)
            self._track_last_seen.pop(key, None)

    def clear(self):
        with self._lock:
            self._profiles.clear()
            self._track_links.clear()
            self._track_last_seen.clear()

    def _prune_tracks_locked(self, now):
        cutoff = now - MAX_TRACK_AGE_SEC
        stale = [
            key
            for key, last_seen in self._track_last_seen.items()
            if last_seen < cutoff
        ]
        for key in stale:
            self._track_last_seen.pop(key, None)
            self._track_links.pop(key, None)
