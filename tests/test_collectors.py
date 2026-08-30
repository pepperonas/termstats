"""Collector resilience.

House rule: a collector may return less, it may not raise. A dashboard that dies on one
unreadable mountpoint is worse than one missing a line.
"""

from types import SimpleNamespace
from unittest.mock import mock_open, patch

import pytest

from termstats import cli


def usage(percent, used_gb=1.0, total_gb=10.0):
    return SimpleNamespace(percent=percent, used=used_gb * 1024 ** 3, total=total_gb * 1024 ** 3)


def partition(mountpoint, fstype="ext4"):
    return SimpleNamespace(device="/dev/fake", mountpoint=mountpoint, fstype=fstype, opts="")


# --- disks ------------------------------------------------------------------------

@pytest.fixture
def fake_disks(monkeypatch):
    def install(partitions, usage_for):
        monkeypatch.setattr(cli.psutil, "disk_partitions", lambda all=False: partitions)
        monkeypatch.setattr(cli.psutil, "disk_usage", usage_for)
        monkeypatch.setattr(cli.psutil, "disk_io_counters", lambda: None)
    return install


def test_unreadable_partition_is_skipped_not_fatal(fake_disks):
    def usage_for(mount):
        if mount == "/locked":
            raise PermissionError("nope")
        return usage(42.0)

    fake_disks([partition("/"), partition("/locked")], usage_for)
    out = cli.get_disk_section()
    assert "/locked" not in out
    assert "42.0%" in out


def test_oserror_on_a_partition_is_skipped(fake_disks):
    def usage_for(mount):
        raise OSError("device not ready")

    fake_disks([partition("/broken")], usage_for)
    assert cli.get_disk_section() == "  No disks found"


@pytest.mark.parametrize("fstype", ["tmpfs", "devtmpfs", "squashfs", "overlay", "devfs"])
def test_pseudo_filesystems_are_filtered_out(fake_disks, fstype):
    fake_disks([partition("/run", fstype)], lambda m: usage(1.0))
    assert cli.get_disk_section() == "  No disks found"


def test_duplicate_mountpoints_appear_once(fake_disks):
    fake_disks([partition("/data"), partition("/data")], lambda m: usage(50.0))
    assert cli.get_disk_section().count("/data") == 1


def test_long_mountpoints_are_truncated_from_the_left(fake_disks):
    long_mount = "/Volumes/Some/Very/Long/Mount/Point"
    fake_disks([partition(long_mount)], lambda m: usage(3.0))
    out = cli.get_disk_section()
    assert "...unt/Point" in out          # "..." + the last 9 characters
    assert long_mount not in out


def test_disk_io_needs_a_baseline_before_it_reports(fake_disks, monkeypatch):
    io = SimpleNamespace(read_bytes=0, write_bytes=0)
    fake_disks([partition("/")], lambda m: usage(10.0))
    monkeypatch.setattr(cli.psutil, "disk_io_counters", lambda: io)
    assert "Read" not in cli.get_disk_section()   # first call: nothing to diff against
    assert "Read" in cli.get_disk_section()       # second call: a delta exists


def test_failing_disk_io_does_not_lose_the_partition_list(fake_disks, monkeypatch):
    fake_disks([partition("/")], lambda m: usage(10.0))
    monkeypatch.setattr(cli.psutil, "disk_io_counters",
                        lambda: (_ for _ in ()).throw(RuntimeError("no counters")))
    assert "10.0%" in cli.get_disk_section()


# --- memory -----------------------------------------------------------------------

@pytest.fixture
def fake_memory(monkeypatch):
    def install(swap_total_gb):
        monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
            percent=61.0, used=8 * 1024 ** 3, total=16 * 1024 ** 3, available=6 * 1024 ** 3))
        monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(
            percent=12.0, used=1 * 1024 ** 3, total=swap_total_gb * 1024 ** 3))
    return install


def test_memory_reports_used_and_available(fake_memory):
    fake_memory(0)
    out = cli.get_memory_section()
    assert "61.0%" in out and "8.0G / 16.0G" in out and "avail 6.0G" in out


def test_swap_row_is_omitted_when_there_is_no_swap(fake_memory):
    fake_memory(0)
    assert "Swap" not in cli.get_memory_section()


def test_swap_row_appears_when_swap_exists(fake_memory):
    fake_memory(4)
    assert "Swap" in cli.get_memory_section()


# --- network ----------------------------------------------------------------------

@pytest.fixture
def fake_net(monkeypatch):
    def install(connections):
        monkeypatch.setattr(cli.psutil, "net_io_counters", lambda: SimpleNamespace(
            bytes_sent=10 * 1024 ** 3, bytes_recv=20 * 1024 ** 3))
        monkeypatch.setattr(cli.psutil, "net_connections", connections)
    return install


def test_connection_count_is_omitted_when_access_is_denied(fake_net):
    """Windows without administrator rights - omit the row, never print a false zero."""
    def denied():
        raise cli.psutil.AccessDenied()

    fake_net(denied)
    text, _, _ = cli.get_network_section()
    assert "Conns" not in text
    assert "0" != text


def test_connection_count_is_shown_when_available(fake_net):
    fake_net(lambda: [object()] * 7)
    text, _, _ = cli.get_network_section()
    assert "Conns" in text and "7" in text


def test_network_rates_need_a_baseline(fake_net):
    fake_net(lambda: [])
    _, sent, recv = cli.get_network_section()
    assert (sent, recv) == (0, 0)


def test_network_totals_are_reported_in_gigabytes(fake_net):
    fake_net(lambda: [])
    text, _, _ = cli.get_network_section()
    assert "10.00 GB" in text and "20.00 GB" in text


# --- processes --------------------------------------------------------------------

class FakeProc:
    def __init__(self, info=None, raises=None):
        self._info = info
        self._raises = raises

    @property
    def info(self):
        if self._raises:
            raise self._raises
        return self._info


def proc_info(pid, name, cpu, mem=1.0, rss_mb=100):
    return {"pid": pid, "name": name, "cpu_percent": cpu, "memory_percent": mem,
            "memory_info": SimpleNamespace(rss=rss_mb * 1024 ** 2)}


def render_table(table, width=100):
    from rich.console import Console
    console = Console(width=width, file=None, record=True)
    with console.capture() as cap:
        console.print(table)
    return cap.get()


def test_vanished_and_forbidden_processes_are_skipped(monkeypatch):
    procs = [
        FakeProc(raises=cli.psutil.NoSuchProcess(1)),
        FakeProc(raises=cli.psutil.AccessDenied()),
        FakeProc(proc_info(42, "survivor", 9.0)),
    ]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    assert "survivor" in render_table(cli.get_top_processes())


def test_processes_without_a_cpu_reading_are_dropped(monkeypatch):
    procs = [FakeProc(proc_info(1, "unmeasured", None)), FakeProc(proc_info(2, "measured", 5.0))]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    out = render_table(cli.get_top_processes())
    assert "measured" in out and "unmeasured" not in out


def test_processes_are_sorted_by_cpu_descending(monkeypatch):
    procs = [FakeProc(proc_info(i, f"p{i}", cpu)) for i, cpu in enumerate([3.0, 90.0, 40.0])]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    out = render_table(cli.get_top_processes())
    assert out.index("p1") < out.index("p2") < out.index("p0")


def test_only_n_processes_are_shown(monkeypatch):
    procs = [FakeProc(proc_info(i, f"p{i}", float(i))) for i in range(30)]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    # Count rows, not name substrings: "p2" is also inside "p29".
    assert cli.get_top_processes(n=3).row_count == 3
    assert cli.get_top_processes(n=8).row_count == 8


def test_a_nameless_process_still_renders(monkeypatch):
    monkeypatch.setattr(cli.psutil, "process_iter",
                        lambda attrs: [FakeProc(proc_info(7, None, 1.0))])
    assert "?" in render_table(cli.get_top_processes())


# --- steal time -------------------------------------------------------------------

PROC_STAT_1 = "cpu  100 0 100 700 0 0 0 100 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n"
PROC_STAT_2 = "cpu  200 0 200 1400 0 0 0 200 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n"


def test_steal_is_zero_off_linux(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", False)
    with patch("builtins.open", mock_open(read_data=PROC_STAT_1)) as opened:
        assert cli._read_steal_pct() == 0.0
    opened.assert_not_called()


def test_first_linux_reading_has_no_baseline(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data=PROC_STAT_1)):
        assert cli._read_steal_pct() == 0


def test_second_linux_reading_is_a_delta(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data=PROC_STAT_1)):
        cli._read_steal_pct()
    with patch("builtins.open", mock_open(read_data=PROC_STAT_2)):
        assert cli._read_steal_pct() == pytest.approx(10.0)


def test_a_standstill_reports_zero_not_a_division_error(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    for _ in range(2):
        with patch("builtins.open", mock_open(read_data=PROC_STAT_1)):
            result = cli._read_steal_pct()
    assert result == 0


@pytest.mark.parametrize("content", ["", "cpu\n", "cpu 1 2\n", "cpu a b c d e f g h\n"])
def test_malformed_proc_stat_returns_zero(monkeypatch, content):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data=content)):
        assert cli._read_steal_pct() == 0.0


def test_unreadable_proc_stat_returns_zero(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", side_effect=PermissionError):
        assert cli._read_steal_pct() == 0.0
