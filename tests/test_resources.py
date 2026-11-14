"""What a frame may cost (0.8.0).

A live dashboard took 14.7 % of one core at its default interval. Four things were wrong, and
each has a pin here: the process scan asked every process for everything, slow facts were
re-read at frame rate, every frame was painted twice (test_timing.py), and the charts were
drawn by a plotting library (test_charts.py).
"""

from types import SimpleNamespace

import pytest

from termstats import cli
from termstats.demo import DemoSource
from helpers import plain


class RealisticProc:
    """Like psutil.Process after process_iter(['pid', 'cpu_percent']): `info` carries only
    what was asked for, and everything else costs a call."""

    detail_calls = 0

    def __init__(self, pid, cpu, name="proc", gone=False):
        self.info = {"pid": pid, "cpu_percent": cpu}
        self._name, self._gone = name, gone

    def as_dict(self, attrs=None):
        RealisticProc.detail_calls += 1
        if self._gone:
            raise cli.psutil.NoSuchProcess(self.info["pid"])
        return {"name": self._name, "memory_percent": 1.0,
                "memory_info": SimpleNamespace(rss=50 * 1024 ** 2)}


@pytest.fixture
def machine(monkeypatch):
    """700 processes; the scan records which attributes it was asked for."""
    RealisticProc.detail_calls = 0
    asked = []
    procs = [RealisticProc(pid, float(pid % 90), name=f"proc{pid}") for pid in range(1, 701)]

    def process_iter(attrs):
        asked.append(list(attrs))
        for p in procs:
            p.info = {"pid": p.info["pid"], "cpu_percent": p.info["cpu_percent"]}   # a fresh scan
        return procs

    monkeypatch.setattr(cli.psutil, "process_iter", process_iter)
    return SimpleNamespace(asked=asked, procs=procs)


# --- the process scan -----------------------------------------------------------------

def test_the_scan_asks_every_process_for_its_cpu_share_and_nothing_else(machine):
    """Name, memory share and RSS for 700 processes, to draw eight rows, was half of a
    frame's CPU - and a long name costs a second system call on macOS."""
    cli.get_top_processes(100, n=8)
    assert machine.asked == [["pid", "cpu_percent"]]


def test_details_are_read_for_the_rows_that_are_drawn(machine):
    table = cli.get_top_processes(100, n=8)
    assert table.row_count == 8
    assert RealisticProc.detail_calls == 8, f"{RealisticProc.detail_calls} processes were asked for details"


def test_the_drawn_rows_carry_the_details(machine):
    out = plain(cli.get_top_processes(100, n=3), width=100)
    busiest = sorted(machine.procs, key=lambda p: (-p.info["cpu_percent"], p.info["pid"]))[:3]
    for p in busiest:
        assert f"proc{p.info['pid']}" in out


def test_a_process_that_vanished_since_the_scan_gives_its_row_to_the_next(monkeypatch):
    procs = [RealisticProc(1, 90.0, "ghost", gone=True), RealisticProc(2, 50.0, "second"),
             RealisticProc(3, 10.0, "third")]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    table = cli.get_top_processes(100, n=2)
    out = plain(table, width=100)
    assert table.row_count == 2 and "second" in out and "third" in out and "ghost" not in out


# --- slow facts at their own pace -------------------------------------------------------

@pytest.fixture
def live_clock(monkeypatch):
    clock = SimpleNamespace(t=500.0)
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock.t)
    cli.LIVE = True
    return clock


def counter():
    calls = []
    return calls, lambda: calls.append(1) or len(calls)


def test_a_live_session_reuses_a_reading_inside_its_window(live_clock):
    calls, read = counter()
    assert [cli._live_cached("k", 5.0, read) for _ in range(4)] == [1, 1, 1, 1]
    live_clock.t += 4.9
    assert cli._live_cached("k", 5.0, read) == 1 and len(calls) == 1


def test_a_reading_is_refreshed_once_its_window_has_passed(live_clock):
    calls, read = counter()
    cli._live_cached("k", 5.0, read)
    live_clock.t += 5.0
    assert cli._live_cached("k", 5.0, read) == 2
    live_clock.t -= 100.0                                   # a clock that went backwards
    assert cli._live_cached("k", 5.0, read) == 3, "a stale reading must not be kept for ever"


def test_a_snapshot_never_prints_a_remembered_value():
    calls, read = counter()
    assert cli.LIVE is False
    assert [cli._live_cached("k", 5.0, read) for _ in range(3)] == [1, 2, 3]


def test_the_demo_never_reuses_a_reading(live_clock):
    """The demo's story advances by frames, not by the wall clock: a remembered process
    table would freeze the picture the screenshots are rendered from."""
    cli.set_demo(DemoSource())
    calls, read = counter()
    assert [cli._live_cached("k", 5.0, read) for _ in range(3)] == [1, 2, 3]


def test_readings_do_not_share_a_slot(live_clock):
    assert cli._live_cached("a", 5.0, lambda: "a") == "a"
    assert cli._live_cached("b", 5.0, lambda: "b") == "b"


def test_the_process_table_is_read_once_a_second_live(machine, live_clock):
    cli.get_top_processes(100, n=8)
    live_clock.t += 0.5
    cli.get_top_processes(100, n=8)
    assert len(machine.asked) == 1, "the second frame of the second scanned again"
    live_clock.t += 0.5
    cli.get_top_processes(100, n=8)
    assert len(machine.asked) == 2


def test_a_one_second_interval_still_scans_on_every_frame(machine, live_clock):
    """Timer jitter puts a frame at 0.98 s as often as at 1.02 s; without slack every other
    frame of `-i 1` would show a table two seconds old."""
    for _ in range(4):
        cli.get_top_processes(100, n=8)
        live_clock.t += 0.98
    assert len(machine.asked) == 4


def test_partitions_are_read_once_per_window_not_twice_per_frame(monkeypatch, live_clock):
    calls = []
    monkeypatch.setattr(cli.psutil, "disk_partitions", lambda all=False: calls.append(1) or [])
    for _ in range(6):                                      # three frames: rows + section each
        cli.disk_entries()
    assert len(calls) == 1
    live_clock.t += cli.DISK_SCAN_S
    cli.disk_entries()
    assert len(calls) == 2


def test_the_connection_count_is_read_once_per_window(monkeypatch, live_clock):
    calls = []
    monkeypatch.setattr(cli.psutil, "net_connections", lambda: calls.append(1) or [1, 2, 3])
    assert cli.network_section_rows() == 3 and cli._connection_count() == 3
    assert len(calls) == 1


def test_a_refused_connection_count_still_drops_the_row(monkeypatch):
    def refused():
        raise cli.psutil.AccessDenied()
    monkeypatch.setattr(cli.psutil, "net_connections", refused)
    assert cli.network_section_rows() == 2 and cli._connection_count() == -1


# --- the chart costs what a chart may cost ----------------------------------------------

def test_a_chart_is_cheap_enough_to_draw_on_every_frame(primed_history):
    """The plotting library needed 20-50 ms for one chart. The bound here is generous on
    purpose - it has to hold on a loaded CI runner - and still an order of magnitude below
    what it replaces: 20 charts in under 400 ms of CPU."""
    import time
    cli.get_net_chart(96, 20)
    start = time.process_time()
    for _ in range(20):
        cli.get_net_chart(96, 20)
    assert time.process_time() - start < 0.4
