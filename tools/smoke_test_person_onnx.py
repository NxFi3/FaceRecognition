"""Smoke-test and benchmark the app's ONNX person detector on one image."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "yolo26n.onnx")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--conf", type=float, default=0.20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import cv2

    from src.engine.smart_tracking import PersonDetector

    if not args.model.is_file():
        raise SystemExit(f"ONNX model not found: {args.model}")
    if not args.image.is_file():
        raise SystemExit(f"Test image not found: {args.image}")
    image = cv2.imread(str(args.image))
    if image is None or image.size == 0:
        raise SystemExit(f"Could not decode test image: {args.image}")

    detector = PersonDetector(
        args.model,
        device="cpu",
        conf=args.conf,
        imgsz=args.imgsz,
    )
    for _ in range(max(0, args.warmup)):
        detector.detect(image)

    timings_ms = []
    detections = []
    for _ in range(max(1, args.runs)):
        started = time.perf_counter()
        detections = detector.detect(image)
        timings_ms.append((time.perf_counter() - started) * 1000.0)

    # bus.jpg is a fixed public smoke-test image containing multiple people.
    # An empty result usually means a preprocessing/output-layout regression.
    if not detections:
        raise RuntimeError(
            "ONNX inference ran but found zero people in the smoke-test image."
        )

    sorted_ms = sorted(timings_ms)
    report = {
        "backend": detector.backend,
        "providers": detector._onnx_session.get_providers(),
        "model": str(args.model),
        "image": str(args.image),
        "image_shape": list(image.shape),
        "imgsz": args.imgsz,
        "confidence_threshold": args.conf,
        "detections_last_run": len(detections),
        "latency_ms_mean": round(statistics.mean(timings_ms), 3),
        "latency_ms_median": round(statistics.median(timings_ms), 3),
        "latency_ms_min": round(min(timings_ms), 3),
        "latency_ms_p95_approx": round(sorted_ms[min(len(sorted_ms) - 1, int(0.95 * len(sorted_ms)))], 3),
        "runs": len(timings_ms),
    }
    result = json.dumps(report, sort_keys=True)
    print("ONNX_CPU_SMOKE_OK " + result)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
