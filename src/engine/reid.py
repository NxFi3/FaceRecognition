"""Optional OSNet person re-identification backend.

This module is deliberately isolated from the main application.  It lets us
benchmark appearance embeddings before wiring Re-ID into the live tracker.

Install the optional dependencies from requirements-reid.txt first.
"""

from __future__ import annotations

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr

from pathlib import Path
from typing import Iterable

import numpy as np

from .runtime_profile import profile


class OSNetReID:
    """CPU/GPU OSNet feature extractor with normalized embeddings."""

    def __init__(
        self,
        model_name: str = "osnet_x0_25",
        device: str = "cpu",
        model_path: str | None = None,
    ) -> None:
        try:
            import torchreid
        except ImportError as exc:
            raise RuntimeError(
                tr('OSNet support is optional. Install requirements-reid.txt.')
            ) from exc

        self._extractor = torchreid.utils.FeatureExtractor(
            model_name=model_name,
            model_path=model_path,
            device=device,
        )
        self.model_name = model_name
        self.device = device

    @staticmethod
    def _normalize(features: np.ndarray) -> np.ndarray:
        features = np.asarray(features, dtype=np.float32)
        if features.ndim == 1:
            features = features[None, :]
        norms = np.linalg.norm(features, axis=1, keepdims=True)
        return features / np.maximum(norms, 1e-12)

    @profile.stage("osnet_single")
    def embed(self, image_bgr: np.ndarray) -> np.ndarray:
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError(tr('image_bgr must be a non-empty image'))

        features = self._extractor([image_bgr])
        if hasattr(features, "detach"):
            features = features.detach().cpu().numpy()
        return self._normalize(features)[0]

    @profile.stage("osnet_batch")
    def embed_batch(self, images_bgr: Iterable[np.ndarray]) -> np.ndarray:
        images = list(images_bgr)
        if not images:
            return np.empty((0, 0), dtype=np.float32)

        features = self._extractor(images)
        if hasattr(features, "detach"):
            features = features.detach().cpu().numpy()
        return self._normalize(features)

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        a = np.asarray(a, dtype=np.float32).reshape(-1)
        b = np.asarray(b, dtype=np.float32).reshape(-1)
        denom = max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-12)
        return float(np.dot(a, b) / denom)
