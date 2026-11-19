"""Measure, don't guess: CPU / RAM sampling and per-stage timings, using psutil.

CPU numbers are for THIS process (all its threads) as a percentage of the whole machine
(100% = every logical core busy), so they are comparable with Task Manager's total CPU column.
"""
import statistics
import sys
import threading
import time
from contextlib import contextmanager

import psutil


def lower_priority() -> bool:
    """Run below normal priority so the user's other apps stay responsive. True if it was applied."""
    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
        return True
    except (psutil.Error, OSError, AttributeError):
        return False


class Monitor:
    def __init__(self, interval_s: float = 1.0):
        self.interval_s = interval_s
        self.proc = psutil.Process()
        self.ncpu = psutil.cpu_count(logical=True) or 1
        self.samples: list[tuple[float, float, float]] = []  # (process % of machine, whole-system %, process RSS MB)
        self.timings: dict[str, list[float]] = {}            # name -> seconds, one entry per measurement
        self.stage_cpu: dict[str, list[float]] = {}          # name -> process CPU % of machine while the stage ran
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- background sampling ----
    def start(self) -> None:
        self.proc.cpu_percent(None)   # the first call only primes the counters
        psutil.cpu_percent(None)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            proc_pct = self.proc.cpu_percent(None) / self.ncpu
            self.samples.append((proc_pct, psutil.cpu_percent(None), self.proc.memory_info().rss / 1e6))

    # ---- per-stage timings ----
    @contextmanager
    def stage(self, name: str):
        """`with monitor.stage("stt"):` records wall time and the CPU this process used meanwhile."""
        cpu0, t0 = sum(self.proc.cpu_times()[:2]), time.perf_counter()
        try:
            yield
        finally:
            wall = time.perf_counter() - t0
            cpu = sum(self.proc.cpu_times()[:2]) - cpu0
            self.timings.setdefault(name, []).append(wall)
            self.stage_cpu.setdefault(name, []).append(100 * cpu / wall / self.ncpu if wall > 0 else 0.0)

    def add_timing(self, name: str, seconds: float) -> None:
        self.timings.setdefault(name, []).append(seconds)

    # ---- reporting ----
    def summary(self) -> dict:
        if not self.samples:
            return {"samples": 0}
        proc, system, rss = zip(*self.samples)
        return {
            "samples": len(self.samples),
            "process_cpu_avg_pct": statistics.fmean(proc), "process_cpu_max_pct": max(proc),
            "system_cpu_avg_pct": statistics.fmean(system),
            "rss_avg_mb": statistics.fmean(rss), "rss_max_mb": max(rss),
            "logical_cpus": self.ncpu,
        }

    def report(self) -> str:
        s = self.summary()
        lines = []
        if s["samples"]:
            lines.append(
                f"CPU (this process, % of whole machine, {s['logical_cpus']} logical cores): "
                f"avg {s['process_cpu_avg_pct']:.1f}%  max {s['process_cpu_max_pct']:.1f}%   "
                f"(whole system avg {s['system_cpu_avg_pct']:.0f}%, so it includes your other apps)")
            lines.append(f"RAM (this process): avg {s['rss_avg_mb']:.0f} MB  max {s['rss_max_mb']:.0f} MB   [{s['samples']} samples]")
        else:
            lines.append("CPU/RAM: run was too short for a sample (needs at least one full interval).")
        for name, values in self.timings.items():
            ms = sorted(v * 1000 for v in values)
            p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
            cpu = self.stage_cpu.get(name)
            cpu_txt = f"  cpu avg {statistics.fmean(cpu):.1f}%" if cpu else ""
            lines.append(f"{name}: n={len(ms)}  median {statistics.median(ms):.2f} ms  p95 {p95:.2f} ms  max {ms[-1]:.2f} ms{cpu_txt}")
        return "\n".join(lines)
