"""`termstats --demo`: a deterministic stand-in for psutil.

The README screenshots and the VHS tape need a machine that does something worth looking
at and does the SAME thing every time: a network burst, a CPU load spike with disk I/O
behind it, a disk that slowly fills, a process table that reacts. This module is that
machine. It carries exactly the psutil surface cli.py uses (a test pins that against the
source), a clock of its own that advances one interval per frame - so rates come out as
designed however fast the frames are produced - and a seeded RNG for the noise.

Nothing here touches the real system, and the dashboard says DEMO in its header.
"""

import math
import random
from collections import namedtuple

import psutil as _real

Mem = namedtuple("Mem", "total available percent used free")
Swap = namedtuple("Swap", "total used free percent")
Usage = namedtuple("Usage", "total used free percent")
Part = namedtuple("Part", "device mountpoint fstype opts")
DiskIO = namedtuple("DiskIO", "read_bytes write_bytes read_count write_count")
NetIO = namedtuple("NetIO", "bytes_sent bytes_recv packets_sent packets_recv")
MemInfo = namedtuple("MemInfo", "rss vms")

MiB = 1024 ** 2
GiB = 1024 ** 3

DEFAULT_SEED = 7
PERIOD = 150            # frames; the story repeats, so a live demo keeps happening
BURST = (22, 42)        # network burst - fully inside the first visible chart window
SPIKE = (46, 74)        # CPU load spike, disk reads with it - the first visible frame (63:
PREFILL = 60            #   init + prime + 60 prefilled + 1) lands near its peak
NCPU = 8
RAM = 16 * GiB
SWAP = 4 * GiB
UPTIME_S = 3 * 86400 + 4 * 3600 + 17 * 60

# (pid, name, idle cpu %, memory %, rss MiB, reacts to the spike)
PROCS = (
    (1187, "postgres", 2.4, 4.1, 612, False),
    (2210, "python3", 1.1, 2.3, 340, True),
    (2288, "ffmpeg", 0.4, 1.2, 180, True),
    (901, "nginx", 0.9, 0.3, 42, False),
    (1402, "node", 1.6, 1.9, 288, False),
    (777, "redis-server", 0.7, 0.8, 120, False),
    (3105, "rsync", 0.2, 0.2, 24, True),
    (612, "dockerd", 0.5, 0.9, 136, False),
    (1, "systemd", 0.1, 0.1, 12, False),
    (2871, "sshd", 0.1, 0.1, 9, False),
    (1990, "prometheus", 0.8, 1.4, 210, False),
    (2403, "gunicorn", 0.6, 1.1, 164, False),
    (3311, "cron", 0.0, 0.0, 3, False),
    (3350, "termstats", 0.3, 0.2, 31, False),
)


def bump(frame, start, end):
    """A raised-cosine envelope, 0..1 over [start, end) and 0 outside it."""
    if not start <= frame < end:
        return 0.0
    x = (frame - start) / (end - start)
    return 0.5 - 0.5 * math.cos(2 * math.pi * x)


class DemoProcess:
    def __init__(self, info):
        self.info = info
        self.pid = info["pid"]

    def cpu_percent(self, interval=None):
        return self.info["cpu_percent"]


class DemoSource:
    """The psutil surface cli.py uses, fed from a scripted story instead of the kernel."""

    AccessDenied = _real.AccessDenied
    NoSuchProcess = _real.NoSuchProcess
    node = "demo-box"
    system = "Linux"
    T0 = 1_700_000_000.0

    def __init__(self, seed=DEFAULT_SEED, interval=0.5):
        self.seed = seed
        self.interval = interval
        self.rng = random.Random(seed)
        self.frame = 0
        self._bias = [self.rng.uniform(0.55, 1.0) for _ in range(NCPU)]
        self._cores = [0.0] * NCPU
        self._total = 0.0
        self._rx = self._tx = self._rd = self._wr = 0.0
        self._net = [12 * GiB, 87 * GiB]   # cumulative bytes sent, received - a machine
        self._io = [40 * GiB, 15 * GiB]    #   that has been up for three days, not a fresh one
        self._step()

    # --- the story ---------------------------------------------------------------------
    def now(self):
        return self.T0 + self.frame * self.interval

    def phase(self):
        return self.frame % PERIOD

    def _step(self):
        """Advance one frame; everything below derives from the new frame number."""
        self.frame += 1
        f = self.phase()
        rng = self.rng
        spike, burst = bump(f, *SPIKE), bump(f, *BURST)
        cores = []
        for i, bias in enumerate(self._bias):
            base = 5 + 9 * bias + 8 * bias * math.sin(f / 6.0 + i * 1.3)
            lift = 92 * spike * (0.7 + 0.3 * bias)     # the spike takes every core with it
            cores.append(min(100.0, max(0.0, base + lift + rng.uniform(-3, 3))))
        self._cores = cores
        self._total = sum(cores) / len(cores)
        self._rx = max(0.0, 250_000 + 38 * MiB * burst + rng.uniform(-20_000, 20_000))
        self._tx = max(0.0, 80_000 + 0.22 * self._rx + rng.uniform(-8_000, 8_000))
        self._rd = max(0.0, 1.5 * MiB + 60 * MiB * spike + rng.uniform(-100_000, 100_000))
        self._wr = max(0.0, 0.6 * MiB + 20 * MiB * burst + rng.uniform(-50_000, 50_000))
        self._net[0] += int(self._tx * self.interval)
        self._net[1] += int(self._rx * self.interval)
        self._io[0] += int(self._rd * self.interval)
        self._io[1] += int(self._wr * self.interval)

    # --- psutil surface ----------------------------------------------------------------
    def cpu_percent(self, interval=None, percpu=False):
        if percpu:
            self._step()                   # the per-core read is the one call per frame
            return list(self._cores)
        return round(self._total, 1)

    def cpu_count(self, logical=True):
        return NCPU

    def getloadavg(self):
        load = self._total / 100 * NCPU
        return (round(load, 2), round(load * 0.8 + 0.5, 2), round(load * 0.6 + 0.8, 2))

    def boot_time(self):
        return self.now() - UPTIME_S

    def pids(self):
        return list(range(1, 413))

    def virtual_memory(self):
        used = int(RAM * (0.52 + 0.08 * min(self.frame, 600) / 600))   # a slow leak
        cache = int(RAM * 0.17)
        available = RAM - used - cache
        percent = round(100.0 * (RAM - available) / RAM, 1)
        return Mem(RAM, available, percent, used, RAM - used)

    def swap_memory(self):
        used = int(SWAP * 0.12)
        return Swap(SWAP, used, SWAP - used, 12.0)

    def disk_partitions(self, all=False):
        return [Part("/dev/nvme0n1p2", "/", "ext4", "rw,relatime"),
                Part("/dev/sdb1", "/data", "xfs", "rw,noatime")]

    def disk_usage(self, path):
        if path == "/data":
            total, frac = 2 * 1024 * GiB, 0.34
        else:                              # the root fills, slowly but visibly
            total, frac = 512 * GiB, min(0.97, 0.61 + self.frame * 0.00005)
        used = int(total * frac)
        return Usage(total, used, total - used, round(100.0 * used / total, 1))

    def disk_io_counters(self):
        return DiskIO(self._io[0], self._io[1], self._io[0] // 4096, self._io[1] // 4096)

    def net_io_counters(self):
        return NetIO(self._net[0], self._net[1], self._net[0] // 1400, self._net[1] // 1400)

    def net_connections(self, kind="inet"):
        return [None] * (42 + int(6 * math.sin(self.phase() / 9.0)))

    def process_iter(self, attrs=None, ad_value=None):
        spike = bump(self.phase(), *SPIKE)
        for pid, name, idle, mem_pct, rss_mib, reacts in PROCS:
            cpu = idle + (38 * spike if reacts else 0.0) + self.rng.uniform(-0.2, 0.2)
            yield DemoProcess({
                "pid": pid, "name": name, "cpu_percent": round(max(0.0, cpu), 1),
                "memory_percent": mem_pct, "memory_info": MemInfo(rss_mib * MiB, rss_mib * 3 * MiB),
            })
