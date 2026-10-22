"""Small local hot-path check; not a camera throughput or target-PC benchmark."""
import json
import statistics
import time
import cv2
import numpy as np
from src.runtime import DetectionSchedule
from src.profiling import Metrics


def main():
    cv2.setNumThreads(1)
    frame = np.zeros((720, 1280, 3), np.uint8)
    schedule = DetectionSchedule(3, .3)
    timings = []
    for n in range(120):
        start = time.perf_counter()
        schedule.due(frame, n, {})
        timings.append((time.perf_counter() - start) * 1000)
    report = {'motion_720p_ms': {'p50': statistics.median(timings),
                               'p95': sorted(timings)[int(len(timings)*.95)]}}
    for enabled in (False, True):
        metrics = Metrics(enabled)
        start = time.perf_counter()
        for _ in range(10000):
            metrics.observe('stage', .001)
        report[f'observe_enabled_{enabled}_us'] = (time.perf_counter() - start) * 1e6 / 10000
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
