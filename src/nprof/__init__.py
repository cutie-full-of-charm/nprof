"""
nprof -- naive profiler
    You can call:
        profiling_start()           to start process/thread
        with phase():               to separate your stats and graph to phases
        or mark()                   same as phases but without indents
        profiling_stop()            to stop process/thread

    also available: list_gpus()

    In short: watch README.md
"""

import argparse
import contextlib
import csv
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import psutil

__version__ = "0.6.0"
__all__ = [
    "list_gpus",
    "mark",
    "phase",
    "profiling_start",
    "profiling_stop",
    "start",
    "stop",
]

_FIELDS = (
    "timestamp",
    "elapsed_s",
    "sample_duration_ms",
    "pids",
    "process_count",
    "cpu_pct",
    "cpu_time_s",
    "rss_mib",
    "memory_elapsed_s",
    "memory_sample_duration_ms",
    "uss_mib",
    "pss_mib",
    "gpu_elapsed_s",
    "gpu_sample_duration_ms",
    "gpu_device_util_pct",
    "gpu_device_mem_util_pct",
    "gpu_device_enc_pct",
    "gpu_device_dec_pct",
    "gpu_device_vram_mib",
    "gpu_device_power_w",
    "gpu_process_vram_mib",
    "gpu_intel_render_pct",
    "gpu_intel_compute_pct",
    "gpu_intel_video_pct",
    "phase",
)
_METRICS = (
    "process_count",
    "cpu_pct",
    "cpu_time_s",
    "rss_mib",
    "uss_mib",
    "pss_mib",
    "gpu_device_util_pct",
    "gpu_device_mem_util_pct",
    "gpu_device_enc_pct",
    "gpu_device_dec_pct",
    "gpu_device_vram_mib",
    "gpu_device_power_w",
    "gpu_process_vram_mib",
    "gpu_intel_render_pct",
    "gpu_intel_compute_pct",
    "gpu_intel_video_pct",
)
_MEMORY_KEYS = ("uss_mib", "pss_mib")
_GPU_KEYS = tuple(key for key in _METRICS if key.startswith("gpu_"))

_UNITS = {
    "cpu_pct": "%",
    "cpu_time_s": "s",
    "rss_mib": "MiB",
    "uss_mib": "MiB",
    "pss_mib": "MiB",
    "gpu_device_util_pct": "%",
    "gpu_device_mem_util_pct": "%",
    "gpu_device_enc_pct": "%",
    "gpu_device_dec_pct": "%",
    "gpu_device_vram_mib": "MiB",
    "gpu_process_vram_mib": "MiB",
    "gpu_device_power_w": "W",
    "gpu_intel_render_pct": "%",
    "gpu_intel_compute_pct": "%",
    "gpu_intel_video_pct": "%",
}
_LOCK = threading.RLock()
_ACTIVE = None


def _mib(n):
    return n / 1048576 if n is not None else None


def _safe(fn):
    try:
        return fn()
    except (OSError, ValueError, AttributeError, psutil.Error):
        return None


def _gpu_devices():
    devices = []
    try:
        import pynvml as nv

        nv.nvmlInit()
        try:
            for i in range(nv.nvmlDeviceGetCount()):
                h = nv.nvmlDeviceGetHandleByIndex(i)
                name = nv.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode(errors="replace")
                devices.append(
                    {
                        "id": f"nvidia:{i}",
                        "name": str(name),
                        "backend": "nvml",
                        "index": i,
                    }
                )
        finally:
            nv.nvmlShutdown()
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        drm = Path("/sys/class/drm")
        for card in sorted(drm.glob("card[0-9]*")):
            vendor_path = card / "device/vendor"
            try:
                vendor = vendor_path.read_text().strip().lower()
            except OSError:
                continue
            if vendor not in ("0x8086", "0x1002"):
                continue
            driver = "intel" if vendor == "0x8086" else "amd"
            devices.append(
                {
                    "id": f"{driver}:{card.name}",
                    "name": f"{driver.upper()} {card.name}",
                    "backend": "intel_gpu_top"
                    if driver == "intel" and shutil.which("intel_gpu_top")
                    else "sysfs",
                    "card": str(card),
                }
            )
    return devices


def list_gpus():
    return _gpu_devices()


class _IntelGPUtop:
    def __init__(self, card, interval):
        self.latest = None
        self.error = None
        self._lock = threading.Lock()
        self._closed = False
        self.proc = subprocess.Popen(
            [
                "intel_gpu_top",
                "-J",
                "-o",
                "-",
                "-s",
                str(max(100, round(interval * 1000))),
                "-d",
                f"drm:/dev/dri/{Path(card).name}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.reader = threading.Thread(
            target=self._read, daemon=True, name="intel-gpu-top-reader"
        )
        self.reader.start()

    @staticmethod
    def _num(v):
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse(cls, obj):
        engines = obj.get("engines", {})
        if not isinstance(engines, dict):
            return {}
        buckets = {"render": [], "compute": [], "video": []}
        for name, value in engines.items():
            if not isinstance(value, dict):
                continue
            busy = cls._num(value.get("busy"))
            if busy is None:
                continue
            n = name.lower()
            if "compute" in n:
                buckets["compute"].append(busy)
            elif "render" in n or "3d" in n:
                buckets["render"].append(busy)
            elif "video" in n:
                buckets["video"].append(busy)

        def maximum(key):
            return max(buckets[key]) if buckets[key] else None

        render, compute, video = maximum("render"), maximum("compute"), maximum("video")
        candidates = [x for x in (render, compute) if x is not None]
        return {
            "gpu_intel_render_pct": render,
            "gpu_intel_compute_pct": compute,
            "gpu_intel_video_pct": video,
            "gpu_device_util_pct": max(candidates) if candidates else None,
        }

    def _read(self):
        depth = 0
        quoted = escaped = False
        buf = []
        try:
            for chunk in self.proc.stdout:
                for ch in chunk:
                    if depth == 0:
                        if ch != "{":
                            continue
                        buf = [ch]
                        depth = 1
                        quoted = escaped = False
                        continue
                    buf.append(ch)
                    if quoted:
                        if escaped:
                            escaped = False
                        elif ch == "\\":
                            escaped = True
                        elif ch == '"':
                            quoted = False
                    elif ch == '"':
                        quoted = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                data = self._parse(json.loads("".join(buf)))
                                with self._lock:
                                    self.latest = data
                            except (ValueError, TypeError):
                                pass
                            buf = []
        except (OSError, ValueError) as exc:
            self.error = str(exc)

    def sample(self):
        with self._lock:
            return dict(self.latest or {})

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self.reader.join(timeout=2)


class _GPU:
    def __init__(self, selector, interval=0.1):
        self.intel = None
        self.selector = selector
        self.info = None
        self.error = None
        self.nv = None
        self.handle = None
        self._owned = False
        if selector in (None, "none", "off"):
            return
        devices = _gpu_devices()
        if selector == "auto":
            self.info = devices[0] if devices else None
        else:
            self.info = next((d for d in devices if d["id"] == selector), None)
        if not self.info:
            self.error = (
                f"GPU {selector!r} unavailable; detected: {[d['id'] for d in devices]}"
            )
            return
        if self.info["backend"] == "intel_gpu_top":
            try:
                self.intel = _IntelGPUtop(self.info["card"], interval)
            except (OSError, ValueError) as exc:
                self.error = f"intel_gpu_top failed: {exc}"
                self.info["backend"] = "sysfs"
        if self.info["backend"] == "nvml":
            try:
                import pynvml

                pynvml.nvmlInit()
                self.nv = pynvml
                self._owned = True
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(self.info["index"])
            except Exception as exc:
                self.error = f"NVML initialization failed: {exc}"

    def sample(self, pids):
        data = {k: None for k in _METRICS if k.startswith("gpu_")}
        if not self.info:
            return data
        if self.info["backend"] == "intel_gpu_top":
            if self.intel is not None:
                data.update(self.intel.sample())
            return data
        if self.info["backend"] == "sysfs":
            dev = Path(self.info["card"]) / "device"

            def number(name):
                try:
                    return float((dev / name).read_text().strip())
                except (OSError, ValueError):
                    return None

            data["gpu_device_util_pct"] = number("gpu_busy_percent")
            vram = number("mem_info_vram_used")
            data["gpu_device_vram_mib"] = _mib(vram)
            return data
        if not self.handle:
            return data
        nv, h = self.nv, self.handle

        def get(fn):
            try:
                return fn()
            except Exception:
                return None

        util = get(lambda: nv.nvmlDeviceGetUtilizationRates(h))
        if util:
            data["gpu_device_util_pct"] = util.gpu
            data["gpu_device_mem_util_pct"] = util.memory
        mem = get(lambda: nv.nvmlDeviceGetMemoryInfo(h))
        if mem:
            data["gpu_device_vram_mib"] = _mib(mem.used)
        power = get(lambda: nv.nvmlDeviceGetPowerUsage(h))
        if power is not None:
            data["gpu_device_power_w"] = power / 1000
        for key, method in (
            ("gpu_device_enc_pct", "nvmlDeviceGetEncoderUtilization"),
            ("gpu_device_dec_pct", "nvmlDeviceGetDecoderUtilization"),
        ):
            value = get(lambda: getattr(nv, method)(h))
            if value is not None:
                data[key] = value[0]
        procs = get(lambda: nv.nvmlDeviceGetComputeRunningProcesses(h))
        if procs is not None:
            memory = [
                p.usedGpuMemory
                for p in procs
                if p.pid in pids
                and isinstance(p.usedGpuMemory, int)
                and 0 <= p.usedGpuMemory < (1 << 60)
            ]
            data["gpu_process_vram_mib"] = _mib(sum(memory)) if memory else None
        return data

    def close(self):
        if self.intel is not None:
            self.intel.close()
        if self._owned:
            try:
                self.nv.nvmlShutdown()
            except Exception:
                pass


def _percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    x = (len(s) - 1) * q
    i = int(x)
    return s[i] + (s[min(i + 1, len(s) - 1)] - s[i]) * (x - i)


def _stats(rows, spans=None):
    result = {}
    for key in _METRICS:
        clock = (
            "_memory_tick"
            if key in _MEMORY_KEYS
            else "_gpu_tick"
            if key in _GPU_KEYS
            else "_tick"
        )
        values = [
            r[key]
            for r in rows
            if isinstance(r.get(key), (int, float))
            and math.isfinite(r[key])
            and (
                spans is None
                or (
                    r.get(clock) is not None
                    and any(a <= r[clock] < b for a, b in spans)
                )
            )
        ]
        result[key] = {
            "start": values[0] if values else None,
            "min": min(values) if values else None,
            "avg": statistics.mean(values) if values else None,
            "max": max(values) if values else None,
            "p95": _percentile(values, 0.95),
            "samples": len(values),
        }
    return result


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _table(columns, rows, right=None):
    right = set(right or ())
    lines = [[str(v) for v in columns]] + [[_cell(v) for v in r] for r in rows]
    widths = [max(len(line[i]) for line in lines) for i in range(len(columns))]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def render(values):
        return (
            "|"
            + "|".join(
                " " + (v.rjust(w) if columns[i] in right else v.ljust(w)) + " "
                for i, (v, w) in enumerate(zip(values, widths))
            )
            + "|"
        )

    return "\n".join(
        [border, render(lines[0]), border, *(render(r) for r in lines[1:]), border]
    )


def _write_txt(path, report, rows):
    cols = ["metric", "unit", "start", "min", "avg", "max", "p95", "n"]

    def metric_table(metrics):
        return _table(
            cols,
            [
                [
                    key,
                    _UNITS.get(key, ""),
                    *(v.get(k) for k in ("start", "min", "avg", "max", "p95")),
                    v["samples"],
                ]
                for key, v in metrics.items()
            ],
            cols[2:],
        )

    text = [
        "RESOURCE PROFILER",
        "=================",
        f"Platform: {report['platform']}",
        f"GPU: {report['gpu'] or 'none'}",
        f"Duration: {_cell(report['duration_s'])} s",
        f"Selection: {_cell(report['selection']['start_s'])} .. {_cell(report['selection']['end_s'])} s",
        f"Samples: {len(rows)}",
        "",
        "SUMMARY",
        "-------",
        metric_table(report["metrics"]),
        "",
        "PHASES",
        "------",
    ]
    for name, phase_data in report["phase_summaries"].items():
        text.extend(
            [
                f"[{name}] duration={_cell(phase_data['duration_s'])} s",
                metric_table(phase_data["metrics"]),
                "",
            ]
        )
    if not report["phase_summaries"]:
        text.append("No named phases.\n")
    text.extend(
        [
            "DETAILS / SAMPLES",
            "-----------------",
            _table(
                _FIELDS,
                [[row.get(k) for k in _FIELDS] for row in rows],
                set(_FIELDS) - {"pids", "phase"},
            ),
            "",
            "NOTES",
            "-----",
            "GPU metrics are device-wide except gpu_process_vram_mib (NVIDIA compute only).",
            "Intel gpu_device_util_pct is max(render, compute) engine busy, not overall GPU busy.",
            "Unavailable GPU metrics are '-'; AMD/Intel sysfs support is Linux-only.",
            "CPU % is relative to one logical CPU (100% = one core).",
            "RSS sums can double-count shared memory. Sampling can miss short peaks.",
            "A phase boundary can fall between samples.",
            "",
        ]
    )
    path.write_text("\n".join(text), encoding="utf-8")


def _union_ns(intervals):
    """Length of a union of half-open nanosecond intervals."""
    merged_end = None
    total = 0
    for a, b in sorted((a, b) for a, b in intervals if b > a):
        if merged_end is None or a >= merged_end:
            total += b - a
            merged_end = b
        elif b > merged_end:
            total += b - merged_end
            merged_end = b
    return total


def _sample_quality(
    rows, target_interval, tick_key="_tick", cost_key="sample_duration_ms"
):
    measured = [r for r in rows if r.get(tick_key) is not None]
    gaps = [(b[tick_key] - a[tick_key]) / 1e9 for a, b in zip(measured, measured[1:])]
    costs = [r[cost_key] for r in measured if r.get(cost_key) is not None]
    return {
        "samples": len(measured),
        "target_interval_s": target_interval,
        "mean_interval_s": statistics.mean(gaps) if gaps else None,
        "max_interval_s": max(gaps) if gaps else None,
        "p95_interval_s": _percentile(gaps, 0.95),
        "mean_sample_duration_ms": statistics.mean(costs) if costs else None,
        "max_sample_duration_ms": max(costs) if costs else None,
        "gaps_over_2x_target": sum(g > 2 * target_interval for g in gaps),
    }


class _Sampler:
    def __init__(
        self,
        root_pid,
        interval,
        memory_interval,
        gpu_interval,
        gpu,
        include_children,
        start_ns,
        excluded_pid=None,
    ):
        self.pid = root_pid
        self.root = psutil.Process(root_pid)
        self.interval = interval
        self.memory_interval = memory_interval
        self.gpu_interval = gpu_interval
        self.gpu = _GPU(gpu, gpu_interval)
        self.include_children = include_children
        self.excluded_pid = excluded_pid
        self.start_wall = time.time() - (time.monotonic_ns() - start_ns) / 1e9
        self.rows = []
        self.last_cpu = {}
        self.total_cpu = 0.0
        self.error = None
        self.next_memory_ns = None
        self.next_gpu_ns = None
        self.gpu_enabled = self.gpu.info is not None

    def sample(self):
        began = time.monotonic_ns()
        memory_due = self.next_memory_ns is None or began >= self.next_memory_ns
        gpu_due = self.gpu_enabled and (
            self.next_gpu_ns is None or began >= self.next_gpu_ns
        )
        if memory_due:
            self.next_memory_ns = began + round(self.memory_interval * 1e9)
        if gpu_due:
            self.next_gpu_ns = began + round(self.gpu_interval * 1e9)

        procs = [self.root]
        if self.include_children:
            procs.extend(_safe(lambda: self.root.children(recursive=True)) or [])
        excluded = set()
        if self.excluded_pid is not None:
            excluded.add(self.excluded_pid)
            excluded.update(
                p.pid
                for p in (
                    _safe(
                        lambda: psutil.Process(self.excluded_pid).children(
                            recursive=True
                        )
                    )
                    or []
                )
            )
        rss = uss = pss = cpu_pct = 0.0
        n_rss = n_uss = n_pss = cpu_rates = 0
        pids = []
        valid_procs = []
        for p in procs:
            if p.pid in excluded:
                continue
            try:
                if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                    continue
                c = p.cpu_times()
                measured_at = time.monotonic_ns()
                ct = c.user + c.system
                created = p.create_time()
                identity = (p.pid, created)
                previous = self.last_cpu.get(identity)
                if previous is not None:
                    delta = max(0.0, ct - previous[1])
                    elapsed = (measured_at - previous[0]) / 1e9
                    if elapsed > 0:
                        cpu_pct += delta / elapsed * 100
                        cpu_rates += 1
                    self.total_cpu += delta
                elif p.pid != self.pid and created >= self.start_wall - 0.1:
                    self.total_cpu += max(0.0, ct)
                self.last_cpu[identity] = (measured_at, ct)
                info = p.memory_info()
                rss += info.rss
                n_rss += 1
                pids.append(p.pid)
                valid_procs.append(p)
            except (psutil.Error, OSError):
                pass
        cpu_tick = time.monotonic_ns()
        memory_tick = gpu_tick = None
        memory_cost_ms = gpu_cost_ms = None
        if memory_due:
            memory_began = time.monotonic_ns()
            for p in valid_procs:
                try:
                    info = p.memory_full_info()
                except (psutil.Error, OSError):
                    continue
                if getattr(info, "uss", None) is not None:
                    uss += info.uss
                    n_uss += 1
                if getattr(info, "pss", None) is not None:
                    pss += info.pss
                    n_pss += 1
            memory_tick = time.monotonic_ns()
            memory_cost_ms = (memory_tick - memory_began) / 1e6
        g = {k: None for k in _GPU_KEYS}
        if gpu_due:
            gpu_began = time.monotonic_ns()
            g = self.gpu.sample(set(pids))
            gpu_tick = time.monotonic_ns()
            gpu_cost_ms = (gpu_tick - gpu_began) / 1e6
        finished = time.monotonic_ns()
        row = dict(
            timestamp=time.time() - (finished - cpu_tick) / 1e9,
            _tick=cpu_tick,
            _memory_tick=memory_tick,
            _gpu_tick=gpu_tick,
            sample_duration_ms=(finished - began) / 1e6,
            memory_sample_duration_ms=memory_cost_ms,
            gpu_sample_duration_ms=gpu_cost_ms,
            pids=";".join(map(str, pids)),
            process_count=len(pids),
            cpu_pct=cpu_pct if cpu_rates else None,
            cpu_time_s=self.total_cpu,
            rss_mib=_mib(rss) if n_rss else None,
            uss_mib=_mib(uss) if n_uss else None,
            pss_mib=_mib(pss) if n_pss else None,
            **g,
        )
        self.rows.append(row)
        return row

    def run(self, stop_event, ready_event=None):
        try:
            next_tick = time.monotonic()
            while not stop_event.is_set():
                if stop_event.wait(max(0.0, next_tick - time.monotonic())):
                    break
                self.sample()
                if ready_event is not None:
                    ready_event.set()
                next_tick += self.interval
                now = time.monotonic()
                if next_tick <= now:
                    next_tick += (
                        int((now - next_tick) / self.interval) + 1
                    ) * self.interval
        except Exception as exc:
            self.error = repr(exc)
        finally:
            if ready_event is not None:
                ready_event.set()

    def close(self):
        self.gpu.close()


class _Session:
    def __init__(
        self,
        output,
        interval,
        memory_interval,
        gpu_interval,
        gpu,
        include_children,
        html,
        json,
        md,
        description_md,
        sampler,
    ):
        for name, value in (
            ("interval", interval),
            ("memory_interval", memory_interval),
            ("gpu_interval", gpu_interval),
        ):
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        if sampler not in ("thread", "process"):
            raise ValueError("sampler must be 'thread' or 'process'")
        self.output = Path(output)
        self.interval = interval
        self.memory_interval = memory_interval
        self.gpu_interval = gpu_interval
        self.sampler_mode = sampler
        self.include_children = include_children
        self.html = bool(html)
        self.json = bool(json)
        self.md = bool(md)
        self.description_md = description_md
        self.pid = os.getpid()
        self.t0 = time.monotonic_ns()
        self.rows = []
        self.events = []
        self.active_phases = []
        self._phase_seq = 0
        self.window_start = None
        self.window_end = None
        self.proc = None
        self.worker_path = None
        self.thread = None
        self.engine = None
        self.gpu_info = None
        self.gpu_error = None
        if sampler == "thread":
            self.engine = _Sampler(
                self.pid,
                interval,
                memory_interval,
                gpu_interval,
                gpu,
                include_children,
                self.t0,
            )
            self.gpu_info = self.engine.gpu.info
            self.gpu_error = self.engine.gpu.error
            self.stop_event = threading.Event()
            ready = threading.Event()
            self.thread = threading.Thread(
                target=self.engine.run,
                args=(self.stop_event, ready),
                name="nprof-sampler",
                daemon=True,
            )
            self.thread.start()
            if not ready.wait(timeout=15) or self.engine.error or not self.engine.rows:
                self.stop_event.set()
                self.thread.join(timeout=3)
                self.engine.close()
                raise RuntimeError(
                    f"Sampler initialization failed: {self.engine.error or 'timed out'}"
                )
            self.rows = self.engine.rows
            self.t0 = self.rows[0]["_tick"]
        else:
            self._start_process(gpu)

    def _start_process(self, gpu):
        import queue

        fd, name = tempfile.mkstemp(prefix="nprof-", suffix=".json")
        os.close(fd)
        self.worker_path = Path(name)
        cmd = [
            sys.executable,
            "-m",
            "nprof._worker",
            str(self.pid),
            str(self.interval),
            str(self.memory_interval),
            str(self.gpu_interval),
            "none" if gpu is None else str(gpu),
            "1" if self.include_children else "0",
            str(self.t0),
            name,
        ]
        try:
            env = os.environ.copy()

            source_root = str(Path(__file__).resolve().parent.parent)
            search_path = [source_root, *(str(p) for p in sys.path if p)]

            if env.get("PYTHONPATH"):
                search_path.append(env["PYTHONPATH"])

            env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(search_path))

            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            messages = queue.Queue(maxsize=1)

            def await_ready():
                try:
                    messages.put(self.proc.stdout.readline())
                except Exception as exc:
                    messages.put(str(exc))

            threading.Thread(target=await_ready, daemon=True).start()
            try:
                response = messages.get(timeout=15)
                if not response:
                    raise RuntimeError("worker exited before readiness handshake")
                ready = json.loads(response)
                if not ready.get("ready"):
                    raise RuntimeError(str(ready.get("error") or "worker not ready"))
            except (queue.Empty, ValueError, RuntimeError) as exc:
                if self.proc.poll() is None:
                    self.proc.kill()

                try:
                    _, stderr = self.proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    _, stderr = self.proc.communicate()

                details = f"worker exit code {self.proc.returncode}"

                if stderr.strip():
                    details += f"\nWorker stderr:\n{stderr.strip()[-4000:]}"

                raise RuntimeError(
                    f"Sampler process failed to start: {exc}\n{details}"
                ) from exc
            self.t0 = ready["first_tick"]
            self.gpu_info = ready.get("gpu")
            self.gpu_error = ready.get("gpu_error")
        except Exception:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
                self.proc.communicate()
            self.worker_path.unlink(missing_ok=True)
            raise

    def event(self, kind, name=None):
        with _LOCK:
            t = time.monotonic_ns()
            detail = {}
            if kind == "window_start" and self.window_start is None:
                self.window_start = t
            elif kind == "window_stop" and self.window_end is None:
                self.window_end = t
            elif kind == "phase_begin":
                self._phase_seq += 1
                pid = f"p{self._phase_seq}"
                parent_id = self.active_phases[-1]["id"] if self.active_phases else None
                self.active_phases.append(
                    {"id": pid, "name": name, "parent_id": parent_id}
                )
                detail = {"phase_id": pid, "parent_id": parent_id}
            elif kind == "phase_end":
                for i in range(len(self.active_phases) - 1, -1, -1):
                    if self.active_phases[i]["name"] == name:
                        item = self.active_phases.pop(i)
                        detail = {"phase_id": item["id"]}
                        break
            self.events.append(
                {
                    "event": kind,
                    "name": name,
                    "time_s": (t - self.t0) / 1e9,
                    "pid": self.pid,
                    **detail,
                }
            )
            return t

    def _finish_sampling(self):
        if self.sampler_mode == "thread":
            self.stop_event.set()
            self.thread.join(timeout=max(5, self.interval * 3))
            if self.thread.is_alive():
                raise RuntimeError("Profiler sampler did not stop")
            if self.engine.error:
                raise RuntimeError(f"Profiler sampler failed: {self.engine.error}")
            self.engine.close()
            return
        try:
            output, errors = self.proc.communicate(
                input="stop\n", timeout=max(15, self.interval * 5)
            )
            if self.proc.returncode != 0:
                raise RuntimeError(
                    f"Profiler subprocess exited with {self.proc.returncode}: {errors[-2000:]}"
                )
            reply = json.loads(output.strip().splitlines()[-1])
            if not reply.get("finished"):
                raise RuntimeError(f"Profiler subprocess failed: {reply}")
            self.rows = json.loads(self.worker_path.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired as exc:
            self.proc.kill()
            self.proc.communicate()
            raise RuntimeError("Profiler subprocess did not stop") from exc
        finally:
            self.worker_path.unlink(missing_ok=True)

    def finish(self):
        self._finish_sampling()
        end = time.monotonic_ns()
        low = self.window_start if self.window_start is not None else self.t0
        high = self.window_end if self.window_end is not None else end
        if high < low:
            raise ValueError("stop() was called before start()")

        intervals = []
        stack = []
        active_mark = None
        mark_idx = 0
        for ev in self.events:
            t = self.t0 + round(ev["time_s"] * 1e9)
            kind, name = ev["event"], ev["name"]
            if kind == "phase_begin":
                stack.append(
                    {
                        "id": ev["phase_id"],
                        "name": name,
                        "parent_id": ev.get("parent_id"),
                        "start": t,
                        "kind": "phase",
                    }
                )
            elif kind == "phase_end":
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i]["id"] == ev.get("phase_id"):
                        item = stack.pop(i)
                        item["end"] = t
                        intervals.append(item)
                        break
            elif kind == "mark":
                if active_mark is not None:
                    active_mark["end"] = t
                    intervals.append(active_mark)
                mark_idx += 1
                active_mark = (
                    {
                        "id": f"m{mark_idx}",
                        "name": name,
                        "kind": "mark",
                        "parent_id": None,
                        "start": t,
                    }
                    if name != "unmarked"
                    else None
                )
        for item in stack:
            item["end"] = end
            intervals.append(item)
        if active_mark is not None:
            active_mark["end"] = end
            intervals.append(active_mark)
        intervals = [
            dict(it, start=max(it["start"], low), end=min(it["end"], high))
            for it in intervals
            if min(it["end"], high) > max(it["start"], low)
        ]
        intervals.sort(key=lambda it: (it["start"], -it["end"], it["id"]))
        by_id = {it["id"]: it for it in intervals}
        for it in intervals:
            depth = 0
            ancestor = it.get("parent_id")
            seen = set()
            while ancestor in by_id and ancestor not in seen:
                seen.add(ancestor)
                depth += 1
                ancestor = by_id[ancestor].get("parent_id")
            it["depth"] = depth
        rows = []
        last_before_low = None
        for r in self.rows:
            if r["_tick"] < low:
                last_before_low = r
            if low <= r["_tick"] < high:
                item = dict(r)
                if (
                    item.get("_memory_tick") is not None
                    and not low <= item["_memory_tick"] < high
                ):
                    item["_memory_tick"] = None
                    item["memory_sample_duration_ms"] = None
                    for key in _MEMORY_KEYS:
                        item[key] = None
                if (
                    item.get("_gpu_tick") is not None
                    and not low <= item["_gpu_tick"] < high
                ):
                    item["_gpu_tick"] = None
                    item["gpu_sample_duration_ms"] = None
                    for key in _GPU_KEYS:
                        item[key] = None
                names = [
                    (it["start"], it["depth"], it["name"])
                    for it in intervals
                    if it["start"] <= item["_tick"] < it["end"]
                ]
                item["phase"] = max(names)[2] if names else "unmarked"
                item["elapsed_s"] = (item["_tick"] - self.t0) / 1e9
                item["memory_elapsed_s"] = (
                    (item["_memory_tick"] - self.t0) / 1e9
                    if item.get("_memory_tick") is not None
                    else None
                )
                item["gpu_elapsed_s"] = (
                    (item["_gpu_tick"] - self.t0) / 1e9
                    if item.get("_gpu_tick") is not None
                    else None
                )
                rows.append(item)
        cpu_baseline = (last_before_low or (rows[0] if rows else None) or {}).get(
            "cpu_time_s", 0.0
        )
        for row in rows:
            row["cpu_time_s"] = max(0.0, row["cpu_time_s"] - cpu_baseline)
        summaries = {}
        for name in sorted({it["name"] for it in intervals}):
            spans = [(it["start"], it["end"]) for it in intervals if it["name"] == name]
            chosen = [r for r in rows if any(a <= r["_tick"] < b for a, b in spans)]
            summaries[name] = {
                "duration_s": _union_ns(spans) / 1e9,
                "samples": len(chosen),
                "metrics": _stats(rows, spans),
                "aggregation": "inclusive",
            }
        phases = []
        for it in intervals:
            a, b = it["start"], it["end"]
            descendants = []
            for other in intervals:
                ancestor = other.get("parent_id")
                while ancestor in by_id:
                    if ancestor == it["id"]:
                        descendants.append(
                            (max(a, other["start"]), min(b, other["end"]))
                        )
                        break
                    ancestor = by_id[ancestor].get("parent_id")
            chosen = [r for r in rows if a <= r["_tick"] < b]
            phases.append(
                {
                    "id": it["id"],
                    "name": it["name"],
                    "kind": it["kind"],
                    "parent_id": it["parent_id"],
                    "depth": it["depth"],
                    "start_s": (a - self.t0) / 1e9,
                    "end_s": (b - self.t0) / 1e9,
                    "duration_s": (b - a) / 1e9,
                    "exclusive_duration_s": ((b - a) - _union_ns(descendants)) / 1e9,
                    "samples": len(chosen),
                    "metrics": _stats(rows, [(a, b)]),
                    "aggregation": "inclusive",
                }
            )
        quality = _sample_quality(rows, self.interval)
        memory_quality = _sample_quality(
            rows, self.memory_interval, "_memory_tick", "memory_sample_duration_ms"
        )
        gpu_quality = _sample_quality(
            rows, self.gpu_interval, "_gpu_tick", "gpu_sample_duration_ms"
        )
        report = {
            "schema_version": 3,
            "version": __version__,
            "description_md": self.description_md,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid": self.pid,
            "gpu": self.gpu_info,
            "gpu_error": self.gpu_error,
            "sampler": self.sampler_mode,
            "duration_s": (end - self.t0) / 1e9,
            "interval_s": self.interval,
            "memory_interval_s": self.memory_interval,
            "gpu_interval_s": self.gpu_interval,
            "selection": {
                "start_s": (low - self.t0) / 1e9,
                "end_s": (high - self.t0) / 1e9,
                "duration_s": (high - low) / 1e9,
                "has_start_marker": self.window_start is not None,
                "has_stop_marker": self.window_end is not None,
            },
            "sampling": quality,
            "memory_sampling": memory_quality,
            "gpu_sampling": gpu_quality,
            "events": self.events,
            "phases": phases,
            "phase_summaries": summaries,
            "metrics": _stats(rows),
            "metric_semantics": {
                "cpu_pct": "100% equals one logical CPU",
                "cpu_time_s": "cumulative tracked CPU seconds since selection baseline; children retained after exit",
                "phase_metrics": "inclusive (nested phases included)",
                "slow_metrics": "USS/PSS and GPU are null between their independently timed observations; no forward-fill",
                "metric_timestamps": "elapsed_s for CPU/RSS, memory_elapsed_s for USS/PSS, gpu_elapsed_s for GPU",
            },
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.with_suffix(".csv").open(
            "w", newline="", encoding="utf-8"
        ) as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            w.writeheader()
            w.writerows({k: r.get(k) for k in _FIELDS} for r in rows)
        _write_txt(self.output.with_suffix(".txt"), report, rows)
        csv_path = self.output.with_suffix(".csv")
        json_path = self.output.with_suffix(".json")
        if self.json:
            json_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if self.md:
            from .viewer import build_markdown

            self.output.with_suffix(".md").write_text(
                build_markdown(csv_path, report=report), encoding="utf-8"
            )
        if self.html and rows:
            from .viewer import build

            self.output.with_suffix(".html").write_text(
                build(csv_path, report=report), encoding="utf-8"
            )
        return report


def profiling_start(
    output="results",
    interval=0.1,
    gpu="auto",
    include_children=True,
    html=True,
    json=False,
    md=False,
    *,
    description=None,
    description_file=None,
    sampler="thread",
    memory_interval=None,
    gpu_interval=None,
):
    if memory_interval is None:
        memory_interval = interval
    if gpu_interval is None:
        gpu_interval = interval
    if description is not None and description_file is not None:
        raise ValueError("Use either description or description_file, not both")
    if description_file is not None:
        description = Path(description_file).read_text(encoding="utf-8")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a Markdown string")
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None:
            raise RuntimeError("Profiler already running")
        _ACTIVE = _Session(
            output,
            interval,
            memory_interval,
            gpu_interval,
            gpu,
            include_children,
            html,
            json,
            md,
            description or "",
            sampler,
        )
        return _ACTIVE


def profiling_stop():
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is None:
            raise RuntimeError("Profiler not running")
        session = _ACTIVE
        _ACTIVE = None
    return session.finish()


def start():
    if _ACTIVE:
        _ACTIVE.event("window_start")


def stop():
    if _ACTIVE:
        _ACTIVE.event("window_stop")


def mark(name):
    if _ACTIVE:
        _ACTIVE.event("mark", str(name))


@contextlib.contextmanager
def phase(name):
    if _ACTIVE:
        _ACTIVE.event("phase_begin", str(name))
    try:
        yield
    finally:
        if _ACTIVE:
            _ACTIVE.event("phase_end", str(name))


def main():
    ap = argparse.ArgumentParser(description="Small in-process resource profiler")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("gpus", help="List available GPU backends")
    viewer = sub.add_parser(
        "view", help="Create optional HTML/Markdown from a CSV report"
    )
    viewer.add_argument("csv", type=Path)
    viewer.add_argument(
        "--html",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate HTML (default: enabled; --no-html to disable)",
    )
    viewer.add_argument("--md", action="store_true", help="Generate Markdown")
    viewer.add_argument("--json", type=Path, help="Existing JSON summary (input only)")
    viewer.add_argument("--open", action="store_true")
    args = ap.parse_args()
    if args.command == "gpus":
        print(json.dumps(list_gpus(), indent=2))
    else:
        from .viewer import build, build_markdown

        if not args.html and not args.md:
            ap.error("Enable at least one output: --html or --md")
        if args.open and not args.html:
            ap.error("--open requires HTML output")
        json_path = args.json or args.csv.with_suffix(".json")
        if args.html:
            output = args.csv.with_suffix(".html")
            output.write_text(build(args.csv, json_path), encoding="utf-8")
            print(output)
        if args.md:
            md_path = args.csv.with_suffix(".md")
            md_path.write_text(build_markdown(args.csv, json_path), encoding="utf-8")
            print(md_path)
        if args.open:
            webbrowser.open(output.resolve().as_uri())
