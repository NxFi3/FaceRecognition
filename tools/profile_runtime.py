"""Launch the normal application with opt-in telemetry, without tuning its runtime.

Recording stops after --seconds of camera activity; the application stays open.
Close it normally. Authentication, camera settings and attendance writes are real.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

from src.engine.runtime_profile import profile


class ProcessSampler:
    def __init__(self):
        try:
            import psutil
            self.process = psutil.Process()
            self.process.cpu_percent(None)  # Prime nonblocking delta measurement.
        except ImportError:
            self.process = None

    def sample(self):
        if self.process is None:
            return {"cpu_percent": None, "rss_bytes": None, "threads": None,
                    "unavailable": "Install psutil for process CPU/RSS/native-thread metrics"}
        try:
            with self.process.oneshot():
                return {"cpu_percent": self.process.cpu_percent(None),
                        "rss_bytes": self.process.memory_info().rss,
                        "threads": self.process.num_threads()}
        except Exception as exc:
            return {"cpu_percent": None, "rss_bytes": None, "threads": None,
                    "unavailable": type(exc).__name__}


def metadata():
    versions = {}
    for name in ("numpy", "opencv-python", "opencv-python-headless", "PySide6",
                 "onnxruntime", "torch", "torchreid", "dlib", "psutil"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).resolve().parents[1], text=True,
            stderr=subprocess.DEVNULL, timeout=2).strip())
    except (OSError, subprocess.SubprocessError):
        revision, dirty = None, None
    return {"type": "metadata", "schema": 1, "revision": revision, "tracked_files_modified": dirty,
            "python": platform.python_version(), "platform": platform.platform(),
            "logical_cpus": os.cpu_count(), "packages": versions,
            "cpu_percent_unit": "100 percent = one logical CPU",
            "timing_scope": "cumulative inclusive milliseconds; p50/p95 histogram upper bounds within 10%",
            "fps_scope": "interval capture publications / completed processing iterations",
            "camera_keys": "configured UI slots; application = work outside camera scope",
            "frame_age_origin": "capture publication after read/demosaic; not sensor exposure",
            "scheduling_changes": False}


class Reporter:
    def __init__(self, collector, output, seconds, interval=1.0, startup_timeout=300.0):
        self.collector = collector
        self.output = output
        self.seconds = seconds
        self.interval = interval
        self.startup_timeout = startup_timeout
        self.stop_event = threading.Event()
        self.sampler = ProcessSampler()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="runtime-profiler", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5.0)

    def _write(self, kind, reason=None):
        record = self.collector.snapshot()
        record.update(type=kind, timestamp_utc=datetime.now(timezone.utc).isoformat(),
                      process=self.sampler.sample())
        if reason is not None:
            record["reason"] = reason
        self.output.write(json.dumps(record, allow_nan=False) + "\n")
        self.output.flush()

    def _run(self):
        reason = "application_closed"
        try:
            while not self.stop_event.wait(self.interval):
                now = time.perf_counter()
                first = self.collector.first_frame_at
                if first is not None and now - first >= self.seconds:
                    reason = "duration_complete"
                    break
                if first is None and now - self.collector.started_at >= self.startup_timeout:
                    reason = "no_processed_frames_before_timeout"
                    break
                self._write("sample" if first is not None else "startup")
            self._write("summary", reason)
        except Exception as exc:
            reason = "telemetry_error"
            self.error = str(exc)
            print(f"Runtime profiling stopped: {exc}", file=sys.stderr)
        finally:
            self.collector.enabled = False
            self.output.close()
            print(f"Runtime profiling finished ({reason}); close the application normally.", file=sys.stderr)


def positive_number(value):
    result = float(value)
    if not 0 < result < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=positive_number, default=120)
    parser.add_argument("--output", type=Path, default=Path("baseline.jsonl"))
    parser.add_argument("--interval", type=positive_number, default=1)
    parser.add_argument("--startup-timeout", type=positive_number, default=300)
    args, app_args = parser.parse_known_args(argv)
    # Fail before launching cameras if the output is unavailable; never overwrite a baseline.
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(metadata(), allow_nan=False) + "\n")
        output.flush()
        profile.enable()
        reporter = Reporter(profile, output, args.seconds, args.interval, args.startup_timeout)
        reporter.start()
        previous_argv = sys.argv
        sys.argv = [previous_argv[0], *app_args]
        try:
            from src.app import main as app_main
            app_main()
        finally:
            sys.argv = previous_argv
            reporter.stop()


if __name__ == "__main__":
    main()
