"""Collector resilience and the data decisions each one makes.

House rule: a collector may return less, it may not raise. A dashboard that dies on one
unreadable mountpoint is worse than one missing a line.
"""

from types import SimpleNamespace
from unittest.mock import mock_open, patch

import pytest

from termstats import cli
from helpers import plain


def usage(percent, used_gb=1.0, total_gb=10.0):
    return SimpleNamespace(percent=percent, used=used_gb * 1024 ** 3, total=total_gb * 1024 ** 3)


def partition(mountpoint, fstype="ext4", opts=""):
    return SimpleNamespace(device="/dev/fake", mountpoint=mountpoint, fstype=fstype, opts=opts)


# --- disks --------------------------------------------------------------------------

@pytest.fixture
def fake_disks(monkeypatch):
    def install(partitions, usage_for, macos=False):
        monkeypatch.setattr(cli.psutil, "disk_partitions", lambda all=False: partitions)
        monkeypatch.setattr(cli.psutil, "disk_usage", usage_for)
        monkeypatch.setattr(cli.psutil, "disk_io_counters", lambda: None)
        monkeypatch.setattr(cli, "IS_MACOS", macos)
    return install


def test_unreadable_partition_is_skipped_not_fatal(fake_disks):
    def usage_for(mount):
        if mount == "/boot":
            raise PermissionError
        return usage(10.0)

    fake_disks([partition("/"), partition("/boot")], usage_for)
    out = plain(cli.get_disk_section(60), width=60)
    assert "/boot" not in out and "10.0%" in out


def test_oserror_on_a_partition_is_skipped(fake_disks):
    def usage_for(mount):
        raise OSError("gone")

    fake_disks([partition("/")], usage_for)
    assert "No disks found" in plain(cli.get_disk_section(60), width=60)


@pytest.mark.parametrize("fstype", ["tmpfs", "devtmpfs", "squashfs", "overlay", "devfs", "autofs"])
def test_pseudo_filesystems_are_filtered_out(fake_disks, fstype):
    fake_disks([partition("/x", fstype=fstype)], lambda m: usage(50.0))
    assert "No disks found" in plain(cli.get_disk_section(60), width=60)


def test_duplicate_mountpoints_appear_once(fake_disks):
    fake_disks([partition("/data"), partition("/data")], lambda m: usage(50.0))
    assert plain(cli.get_disk_section(60), width=60).count("/data") == 1


@pytest.mark.parametrize("opts", ["rw,dontbrowse,journaled", "rw,nobrowse", "NOBROWSE"])
def test_volumes_the_os_hides_from_the_user_are_hidden_here_too(fake_disks, opts):
    """A stock Mac lists nine partitions for one physical disk. Seven of them carry
    Apple's own "not for the user" flag, and four report the same total because APFS
    shares space across a container."""
    fake_disks([partition("/"), partition("/System/Volumes/Preboot", opts=opts)],
               lambda m: usage(50.0))
    out = plain(cli.get_disk_section(60), width=60)
    assert "Preboot" not in out
    assert "50.0%" in out


def test_a_normal_volume_is_not_hidden(fake_disks):
    """Counter-check: the filter must not swallow real mounts."""
    fake_disks([partition("/Volumes/Backup", opts="rw,local,journaled")], lambda m: usage(50.0))
    assert "Backup" in plain(cli.get_disk_section(60), width=60)


def test_macos_root_reports_the_data_volume_not_the_sealed_snapshot(fake_disks):
    """On macOS "/" is a read-only system snapshot; every byte the user owns lives in the
    Data volume, which the nobrowse filter just removed. Reporting "/" verbatim shows 11G
    used on a disk that is 98% full."""
    def usage_for(mount):
        if mount == cli.MACOS_DATA_VOLUME:
            return usage(98.5, used_gb=418.0, total_gb=460.0)
        return usage(3.2, used_gb=11.0, total_gb=460.0)

    fake_disks([partition("/", opts="ro,rootfs"),
                partition(cli.MACOS_DATA_VOLUME, opts="rw,dontbrowse")], usage_for, macos=True)
    out = plain(cli.get_disk_section(70), width=70)
    assert "98.5%" in out and "3.2%" not in out
    assert "418.0G" in out


def test_a_missing_data_volume_leaves_root_alone(fake_disks):
    def usage_for(mount):
        if mount == cli.MACOS_DATA_VOLUME:
            raise OSError("no such volume")
        return usage(42.0)

    fake_disks([partition("/", opts="ro,rootfs")], usage_for, macos=True)
    assert "42.0%" in plain(cli.get_disk_section(60), width=60)


def test_the_data_substitution_is_macos_only(fake_disks):
    def usage_for(mount):
        return usage(98.5) if mount == cli.MACOS_DATA_VOLUME else usage(3.2)

    fake_disks([partition("/")], usage_for, macos=False)
    assert "3.2%" in plain(cli.get_disk_section(60), width=60)


@pytest.mark.parametrize("mount,expected", [
    ("/", "/"),
    ("/data", "/data"),
    ("/Volumes/Untitled", "…Untitled"),
    ("/System/Volumes/Data", "…Data"),
])
def test_mountpoints_are_shortened_to_the_part_a_human_recognises(mount, expected):
    """Cutting blindly turned "/Volumes/Untitled" into "…umes/Un", which names nothing."""
    assert cli.short_mount(mount) == expected


def test_a_single_very_long_component_falls_back_to_a_left_cut():
    assert cli.short_mount("/" + "x" * 40).endswith("x")
    assert len(cli.short_mount("/" + "x" * 40)) <= cli.DISK_LABEL_W


def test_ascii_mode_uses_an_ascii_ellipsis(ascii_mode):
    assert cli.short_mount("/Volumes/Untitled").isascii()


def test_disk_io_needs_a_baseline_before_it_reports(fake_disks, monkeypatch):
    io = SimpleNamespace(read_bytes=1000, write_bytes=2000)
    fake_disks([partition("/")], lambda m: usage(10.0))
    monkeypatch.setattr(cli.psutil, "disk_io_counters", lambda: io)
    # The line is there from the first frame - a line that appears one frame later
    # reflows the whole panel - but it says n/a until there is something to diff against.
    first = plain(cli.get_disk_section(70), width=70)
    assert "read" in first and "n/a" in first
    second = plain(cli.get_disk_section(70), width=70)
    assert "read" in second and "n/a" not in second and "/s" in second


def test_failing_disk_io_does_not_lose_the_partition_list(fake_disks, monkeypatch):
    fake_disks([partition("/")], lambda m: usage(10.0))
    monkeypatch.setattr(cli.psutil, "disk_io_counters",
                        lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert "10.0%" in plain(cli.get_disk_section(60), width=60)


# --- memory -------------------------------------------------------------------------

@pytest.fixture
def fake_memory(monkeypatch):
    def install(swap_total_gb):
        monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
            percent=61.0, used=8 * 1024 ** 3, total=16 * 1024 ** 3, available=6 * 1024 ** 3))
        monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(
            percent=12.0, used=1 * 1024 ** 3, total=swap_total_gb * 1024 ** 3))
    return install


def test_memory_reports_used_and_total(fake_memory):
    fake_memory(0)
    out = plain(cli.get_memory_section(60), width=60)
    assert "61.0%" in out and " 8.0G/ 16.0G" in out      # fixed-width gigabyte fields


def test_swap_row_is_omitted_when_there_is_no_swap(fake_memory):
    fake_memory(0)
    assert "swap" not in plain(cli.get_memory_section(60), width=60)
    assert cli.memory_section_rows() == 1


def test_swap_row_appears_when_swap_exists(fake_memory):
    fake_memory(4)
    assert "swap" in plain(cli.get_memory_section(60), width=60)
    assert cli.memory_section_rows() == 2


# --- network ------------------------------------------------------------------------

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
    body, _, _ = cli.get_network_section(60)
    assert "conns" not in plain(body, width=60)
    assert cli.network_section_rows() == 2


def test_connection_count_is_shown_when_available(fake_net):
    fake_net(lambda: [object()] * 7)
    body, _, _ = cli.get_network_section(60)
    out = plain(body, width=60)
    assert "conns" in out and "7" in out
    assert cli.network_section_rows() == 3


def test_network_rates_need_a_baseline(fake_net):
    fake_net(lambda: [])
    _, sent, recv = cli.get_network_section(60)
    assert (sent, recv) == (0, 0)


def test_network_totals_are_reported_in_gigabytes(fake_net):
    fake_net(lambda: [])
    body, _, _ = cli.get_network_section(70)
    out = plain(body, width=70)
    assert "10.0G" in out and "20.0G" in out


# --- cpu ------------------------------------------------------------------------------

@pytest.mark.parametrize("ncores,max_rows,expected", [
    (4, 20, 1),      # fits in one column - wider bars, fills a tall panel
    (10, 12, 1),
    (10, 6, 2),      # too tall for one column
    (32, 9, 4),
    (128, 9, 0),     # not even four columns fit -> heat strip
])
def test_core_layout_prefers_one_tall_column_then_splits(ncores, max_rows, expected):
    assert cli.core_columns(ncores, max_rows) == expected


def test_many_cores_collapse_to_a_heat_strip(monkeypatch):
    """256 per-core meters are not information, they are wallpaper."""
    monkeypatch.setattr(cli.psutil, "cpu_percent",
                        lambda percpu=False: [50.0] * 256 if percpu else 50.0)
    body, _, _ = cli.get_cpu_section(60, max_rows=8)
    out = plain(body, width=60)
    assert "cores" in out
    assert "cpu100" not in out


def test_per_core_rows_are_listed_when_they_fit(monkeypatch):
    monkeypatch.setattr(cli.psutil, "cpu_percent",
                        lambda percpu=False: [50.0] * 4 if percpu else 50.0)
    out = plain(cli.get_cpu_section(60, max_rows=20)[0], width=60)
    for i in range(4):
        assert f"cpu{i}" in out
    assert "TOTAL" in out


def test_cpu_height_prediction_matches_what_is_drawn(monkeypatch):
    """render_dashboard sizes the row from this number before the panel exists; if the
    two disagree the layout either clips the TOTAL bar or leaves a gap."""
    for ncores, max_rows in ((4, 20), (10, 12), (10, 6), (32, 9), (128, 9)):
        monkeypatch.setattr(cli.psutil, "cpu_percent",
                            lambda percpu=False, n=ncores: [50.0] * n if percpu else 50.0)
        predicted = cli.cpu_section_rows(ncores, max_rows)
        drawn = plain(cli.get_cpu_section(60, max_rows=max_rows)[0], width=60).rstrip("\n")
        assert len(drawn.split("\n")) == predicted, f"{ncores} cores in {max_rows} rows"


# --- processes -------------------------------------------------------------------------

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


def test_vanished_and_forbidden_processes_are_skipped(monkeypatch):
    procs = [
        FakeProc(raises=cli.psutil.NoSuchProcess(1)),
        FakeProc(raises=cli.psutil.AccessDenied()),
        FakeProc(info=proc_info(3, "survivor", 5.0)),
    ]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    assert "survivor" in plain(cli.get_top_processes(100), width=100)


def test_processes_without_a_cpu_reading_are_dropped(monkeypatch):
    procs = [FakeProc(info=proc_info(1, "nocpu", None)), FakeProc(info=proc_info(2, "ok", 1.0))]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    out = plain(cli.get_top_processes(100), width=100)
    assert "ok" in out and "nocpu" not in out


def test_processes_are_sorted_by_cpu_descending(monkeypatch):
    procs = [FakeProc(info=proc_info(i, f"proc{i}", float(i))) for i in (1, 9, 5)]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    out = plain(cli.get_top_processes(100), width=100)
    assert out.index("proc9") < out.index("proc5") < out.index("proc1")


def test_only_n_processes_are_shown(monkeypatch):
    procs = [FakeProc(info=proc_info(i, f"p{i}", float(i))) for i in range(30)]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    assert cli.get_top_processes(100, n=3).row_count == 3
    assert cli.get_top_processes(100, n=8).row_count == 8


def test_a_nameless_process_still_renders(monkeypatch):
    monkeypatch.setattr(cli.psutil, "process_iter",
                        lambda attrs: [FakeProc(info=proc_info(1, None, 1.0))])
    assert "?" in plain(cli.get_top_processes(100), width=100)


def test_the_inline_bar_is_dropped_on_a_narrow_terminal(monkeypatch):
    """Below ~86 columns the bar would squeeze the process name into nothing."""
    monkeypatch.setattr(cli.psutil, "process_iter",
                        lambda attrs: [FakeProc(info=proc_info(1, "proc", 50.0))])
    assert len(cli.get_top_processes(120).columns) == 6
    assert len(cli.get_top_processes(70).columns) == 5


@pytest.mark.parametrize("name", [
    "Google Chrome Helper (Renderer) --type=renderer --enable-features=x",
    "/usr/local/bin/some very long command --with --flags " * 3,
    "x" * 200,
])
def test_a_long_process_name_is_ellipsised_not_wrapped(monkeypatch, name):
    """One row per process, whatever the name.

    ⚠️ A name of 200 unbroken characters is the wrong probe here - rich truncates that
    one anyway. Real process names contain SPACES, and those wrap the row onto five
    lines without no_wrap; the first two cases are what actually pins this.
    """
    monkeypatch.setattr(cli.psutil, "process_iter",
                        lambda attrs: [FakeProc(info=proc_info(1, name, 1.0))])
    rows = plain(cli.get_top_processes(100), width=100).rstrip("\n").split("\n")
    assert len(rows) == 2, f"the row wrapped onto {len(rows) - 1} lines"


# --- steal time (Linux) -----------------------------------------------------------------

def test_steal_is_zero_off_linux(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", False)
    assert cli._read_steal_pct() == 0.0


def test_first_linux_reading_has_no_baseline(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data="cpu 100 0 0 0 0 0 0 10\n")):
        assert cli._read_steal_pct() == 0


def test_second_linux_reading_is_a_delta(monkeypatch):
    """Fields 1..n sum to the total, field 8 is steal: 100 ticks pass, 10 of them stolen."""
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data="cpu 90 0 0 0 0 0 0 10\n")):
        cli._read_steal_pct()
    with patch("builtins.open", mock_open(read_data="cpu 180 0 0 0 0 0 0 20\n")):
        assert cli._read_steal_pct() == pytest.approx(10.0)


def test_a_standstill_reports_zero_not_a_division_error(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    for _ in range(2):
        with patch("builtins.open", mock_open(read_data="cpu 100 0 0 0 0 0 0 10\n")):
            result = cli._read_steal_pct()
    assert result == 0


@pytest.mark.parametrize("content", ["cpu\n", "cpu 1 2 3\n", "garbage\n", ""])
def test_malformed_proc_stat_returns_zero(monkeypatch, content):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", mock_open(read_data=content)):
        assert cli._read_steal_pct() == 0.0


def test_unreadable_proc_stat_returns_zero(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    with patch("builtins.open", side_effect=PermissionError):
        assert cli._read_steal_pct() == 0.0


# --- 0.3.0: the memory bar shows the cache as its own segment --------------------------

@pytest.fixture
def fake_memory_with_cache(monkeypatch):
    """16 GB: 5.2 used, 3.9 available -> 6.9 in the kernel's cache, percent 75.6."""
    G = 1024 ** 3
    monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=75.6, used=int(5.2 * G), total=16 * G, available=int(3.9 * G)))
    monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(
        percent=0.0, used=0, total=0))


def test_memory_prints_psutils_percent_not_the_used_share(fake_memory_with_cache):
    """75.6% is what "how full" means to everyone; 32.5% would be a surprise."""
    out = plain(cli.get_memory_section(70), width=70)
    assert "75.6%" in out and "32.5%" not in out


def test_memory_bar_splits_used_from_cache(fake_memory_with_cache):
    out = plain(cli.get_memory_section(70), width=70)
    assert cli.BAR_FULL in out and cli.BAR_SECONDARY in out
    assert out.index(cli.BAR_SECONDARY) > out.rindex(cli.BAR_FULL)


def test_memory_names_the_cache_when_there_is_room(fake_memory_with_cache):
    assert "6.9G cache" in plain(cli.get_memory_section(70), width=70)


def test_memory_keeps_the_short_note_on_a_narrow_panel(fake_memory_with_cache):
    """Below 44 columns the cache suffix is left off so that the used/total figure still
    fits - appending it regardless would make meter() drop the WHOLE note."""
    out = plain(cli.get_memory_section(40), width=40)
    assert "cache" not in out
    assert " 5.2G/ 16.0G" in out, "the short note must survive, not be dropped with the suffix"
    assert "75.6%" in out


def test_a_machine_without_cache_shows_no_secondary_segment(monkeypatch):
    G = 1024 ** 3
    monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=50.0, used=8 * G, total=16 * G, available=8 * G))
    monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(percent=0.0, used=0, total=0))
    out = plain(cli.get_memory_section(70), width=70)
    assert cli.BAR_SECONDARY not in out and "cache" not in out


def test_rss_column_scales_to_gigabytes(monkeypatch):
    monkeypatch.setattr(cli.psutil, "process_iter",
                        lambda attrs: [FakeProc(info=proc_info(1, "big", 1.0, rss_mb=2560))])
    assert "2.5G" in plain(cli.get_top_processes(100), width=100)
