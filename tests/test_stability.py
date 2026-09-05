"""Layout stillness: nothing on screen changes width because a value changed.

The single biggest enemy of a live dashboard is width jitter - "9.8G" becoming "10.2G"
and pushing a bar one cell shorter, a chart axis growing a digit and shifting the whole
plot, a title breathing as its rates change. Every number renders in a field whose width
depends only on the terminal, and this file drives the dashboard through wildly
different values to prove it.
"""

import random
from types import SimpleNamespace

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain

G = 1024 ** 3


# --- fixed-width formats ---------------------------------------------------------------

@pytest.mark.parametrize("x", [0, 0.04, 5, 9.96, 10, 50, 99.94, 100, 250, 999.9, 5000, float("nan")])
def test_percent_is_always_six_cells(x):
    assert len(T.fmt_pct(x)) == 6


@pytest.mark.parametrize("n", [0, 1.5 * G, 9.8 * G, 10.2 * G, 99.9 * G, 100 * G, 460.4 * G, 999.9 * G, 2000 * G, 12000 * G])
def test_gigabytes_are_always_six_cells(n):
    """'  6.2G' .. '999.9G' .. ' 11.7T'. (Five was the first draft, and the test caught
    the constants disagreeing with the format - the note fields were sized to five.)"""
    assert len(T.fmt_gb(n)) == 6, T.fmt_gb(n)


def test_gigabyte_pair_is_always_thirteen_cells():
    for used, total in ((0, 1 * G), (9.8 * G, 16 * G), (418.7 * G, 460.4 * G), (999 * G, 999 * G)):
        assert len(T.fmt_gb_pair(used, total)) == T.NOTE_GB_PAIR_W


@pytest.mark.parametrize("b", [0, 1, 999, 1023, 1024, 45.2 * 1024, 999.9 * 1024, 1024 ** 2,
                               1.5 * 1024 ** 2, 999 * 1024 ** 2, 2 * 1024 ** 3, 500 * 1024 ** 3, float("nan")])
def test_rates_are_always_eight_cells(b):
    assert len(T.fmt_rate(b)) == T.RATE_W - 1, T.fmt_rate(b)


def test_rate_units_are_single_letters_so_the_field_never_grows():
    assert T.fmt_rate(500).endswith("B/s")
    assert T.fmt_rate(500 * 1024).endswith("K/s")
    assert T.fmt_rate(500 * 1024 ** 2).endswith("M/s")
    assert T.fmt_rate(5 * 1024 ** 3).endswith("G/s")


@pytest.mark.parametrize("x", [0, 0.5, 9.92, 10.02, 99.99, 123.4, 2500])
def test_load_is_always_six_cells(x):
    assert len(T.fmt_load(x)) == 6


@pytest.mark.parametrize("s", [0, 59, 3600, 7 * 3600 + 120, 86400, 3 * 86400 + 19 * 3600, 400 * 86400, 5000 * 86400])
def test_uptime_is_always_eight_cells(s):
    """Days never vanish: '  0d 07h' rather than '7h 02m', or the field would jump a
    day after boot."""
    assert len(T.fmt_uptime(s)) == 8


def test_count_is_clamped_to_its_field():
    assert len(T.fmt_count(7)) == 5 and len(T.fmt_count(123456789)) == 5


def test_axis_labels_share_one_width():
    labels = [T.fmt_axis(v, T.AXIS_W_RATE) for v in (0, 300, 600, 1000, 25000)]
    assert len({len(l) for l in labels}) == 1


# --- meters keep their bar length whatever the value says --------------------------------

def cells(text):
    return plain(text, width=200).rstrip("\n")


def bar_cells(text):
    drawn = cells(text)
    glyphs = (cli.BAR_FULL, cli.BAR_EMPTY, cli.BAR_SECONDARY, cli.GLYPHS.peak) + tuple(cli.BAR_PARTIALS)
    return sum(drawn.count(ch) for ch in glyphs)


def test_a_note_gaining_a_digit_does_not_shorten_the_bar():
    """The old failure: 9.8G -> 10.2G cost the bar a cell."""
    short = cli.meter("ram", 40.0, 60, note=T.fmt_gb_pair(9.8 * G, 16 * G), note_w=T.NOTE_GB_PAIR_W)
    longer = cli.meter("ram", 40.0, 60, note=T.fmt_gb_pair(10.2 * G, 16 * G), note_w=T.NOTE_GB_PAIR_W)
    assert bar_cells(short) == bar_cells(longer)
    assert len(cells(short)) == len(cells(longer)) == 60


def test_a_value_going_from_five_to_a_hundred_percent_keeps_the_bar():
    a, b = cli.meter("cpu0", 5.0, 50), cli.meter("cpu0", 100.0, 50)
    assert bar_cells(a) == bar_cells(b)


def test_a_rate_value_from_bytes_to_gigabytes_keeps_the_bar():
    widths = set()
    for rate in (0, 512, 45.2 * 1024, 999.9 * 1024, 1.5 * 1024 ** 2, 5 * 1024 ** 3):
        m = cli.meter("tx", 50.0, 60, value=T.fmt_rate(rate), value_w=T.RATE_W,
                      note=f"{cli.GLYPHS.sigma} {T.fmt_gb(1.92 * G)}", note_w=T.NOTE_TOTAL_W)
        widths.add((bar_cells(m), len(cells(m))))
    assert len(widths) == 1


def test_an_empty_note_still_reserves_its_field():
    """A cache note that comes and goes must not move the bar with it."""
    with_note = cli.meter("ram", 30.0, 60, note="x" * 10, note_w=10)
    without = cli.meter("ram", 30.0, 60, note="", note_w=10)
    assert bar_cells(with_note) == bar_cells(without)


# --- the header -----------------------------------------------------------------------------

@pytest.fixture
def fake_host(monkeypatch):
    def install(load, pids, uptime):
        monkeypatch.setattr(cli.psutil, "getloadavg", lambda: load)
        monkeypatch.setattr(cli.psutil, "pids", lambda: list(range(pids)))
        monkeypatch.setattr(cli.psutil, "boot_time", lambda: cli.time.time() - uptime)
        monkeypatch.setattr(cli.psutil, "cpu_count", lambda: 10)
    return install


def test_header_fields_do_not_move_when_the_numbers_grow(fake_host):
    fake_host((0.5, 0.4, 0.3), 42, 90)
    quiet = plain(cli.header_line(140), width=140).rstrip("\n")
    fake_host((123.45, 99.9, 88.8), 65535, 400 * 86400 + 5 * 3600)
    loud = plain(cli.header_line(140), width=140).rstrip("\n")
    assert len(quiet) == len(loud)
    for word in ("load", "cpu", "up", "proc"):
        assert quiet.index(word) == loud.index(word), f"'{word}' moved"


def test_header_tail_stays_put_while_the_history_fills(fake_host):
    """The sparkline used to grow from one cell to fifteen over the first 30 s, walking
    the clock left frame by frame."""
    fake_host((1.0, 1.0, 1.0), 100, 1000)
    positions = set()
    for n in (0, 1, 4, 15, 30, 60):
        cli.cpu_history.clear()
        cli.cpu_history.extend([50.0] * n)
        head = plain(cli.header_line(140), width=140).rstrip("\n")
        positions.add(head.index(f"v{cli.__version__}"))
    assert len(positions) == 1, f"the version tag moved: {positions}"


def test_header_tail_degrades_by_width_only(fake_host):
    """Full tail, then without the sparkline, then none - chosen by width, never by value."""
    fake_host((1.0, 1.0, 1.0), 100, 1000)
    cli.cpu_history.extend([50.0] * 60)
    wide = plain(cli.header_line(160), width=160)
    mid = plain(cli.header_line(120), width=120)
    assert any(g in wide for g in cli.SPARK) and f"v{cli.__version__}" in wide
    assert not any(g in mid for g in cli.SPARK) and f"v{cli.__version__}" in mid


# --- the charts -----------------------------------------------------------------------------

def test_the_network_unit_has_hysteresis():
    """A single threshold flipped the axis and legend back and forth near 2 MB/s."""
    cli.net_sent_history.extend([1.9 * 1024 ** 2] * 3)
    assert cli.net_scale()[1] == "KB/s"
    cli.net_sent_history.append(2.1 * 1024 ** 2)
    assert cli.net_scale()[1] == "MB/s"
    cli.net_sent_history.clear(); cli.net_sent_history.extend([1.5 * 1024 ** 2] * 3)
    assert cli.net_scale()[1] == "MB/s", "must not drop back while still above 1 MB/s"
    cli.net_sent_history.clear(); cli.net_sent_history.extend([0.5 * 1024 ** 2] * 3)
    assert cli.net_scale()[1] == "KB/s"


def test_chart_subtitles_keep_their_width_across_values():
    widths = set()
    for now in (0.0, 5.0, 42.4, 100.0):
        cli.cpu_history.append(now)
        widths.add(len(plain(cli.cpu_chart_subtitle(), width=200).rstrip("\n")))
    assert len(widths) == 1
    widths = set()
    for rate in (0.0, 512.0, 45.2 * 1024, 3.0 * 1024 ** 2, 500 * 1024 ** 2):
        cli.net_recv_history.append(rate)
        cli.net_sent_history.append(rate / 2)
        widths.add(len(plain(cli.net_chart_subtitle(), width=200).rstrip("\n")))
    assert len(widths) == 1


# --- the process list -----------------------------------------------------------------------

class FakeProc:
    def __init__(self, info):
        self.info = info


def _proc(pid, name, cpu):
    return FakeProc({"pid": pid, "name": name, "cpu_percent": cpu, "memory_percent": 1.0,
                     "memory_info": SimpleNamespace(rss=50 * 1024 ** 2)})


def test_equal_cpu_processes_keep_their_order_between_frames(monkeypatch):
    """Without a tiebreaker, rows at equal CPU swap places from frame to frame."""
    procs = [_proc(p, f"p{p}", 3.0) for p in (30, 10, 20)]
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: procs)
    first = plain(cli.get_top_processes(100), width=100)
    random.shuffle(procs)
    second = plain(cli.get_top_processes(100), width=100)
    assert first == second
    assert first.index("p10") < first.index("p20") < first.index("p30")


# --- smoothing is display-only ----------------------------------------------------------------

def test_snapshot_mode_never_smooths():
    cli.SMOOTHING = False
    assert cli.shown("x", 80.0) == 80.0
    assert cli.shown("x", 20.0) == 20.0


def test_live_mode_eases_the_fill_but_prints_the_raw_value():
    cli.SMOOTHING = True
    cli._smoother.reset()
    cli.shown("k", 0.0)
    m = cli.meter("cpu0", 80.0, 50, fill=cli.shown("k", 80.0))
    drawn = cells(m)
    assert " 80.0%" in drawn, "the number must be the raw sample"
    filled = drawn.count(cli.BAR_FULL)
    assert 0 < filled < 0.6 * bar_cells(m), "the fill should still be on its way to 80%"


def test_smoothing_converges():
    cli.SMOOTHING = True
    cli._smoother.reset()
    for _ in range(12):
        v = cli.shown("k", 100.0)
    assert v > 99.9


def test_smoothing_forgets_keys_that_stopped_being_drawn():
    cli.SMOOTHING = True
    cli._smoother.reset()
    cli.shown("proc.1", 50.0); cli.shown("proc.2", 50.0)
    cli._smoother.end_frame()
    cli.shown("proc.1", 50.0)
    cli._smoother.end_frame()
    assert "proc.2" not in cli._smoother._state


def test_run_once_switches_smoothing_off(monkeypatch):
    cli.SMOOTHING = True
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", lambda: "x")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: None)
    cli.run_once()
    assert cli.SMOOTHING is False


# --- the whole dashboard: N frames, identical geometry ---------------------------------------

@pytest.fixture
def stormy_machine(monkeypatch):
    """A psutil whose every number swings wildly from call to call."""
    rng = random.Random(7)
    state = {"tick": 0}

    def cpu_percent(percpu=False):
        state["tick"] += 1
        vals = [rng.choice([0.0, 5.5, 50.0, 99.9, 100.0]) for _ in range(8)]
        return vals if percpu else rng.choice([0.0, 9.9, 10.0, 100.0])

    monkeypatch.setattr(cli.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(cli.psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=rng.choice([5.0, 50.0, 99.9]), used=int(rng.choice([0.5, 9.8, 10.2, 15.9]) * G),
        total=16 * G, available=int(rng.choice([0.1, 3.0, 8.0]) * G)))
    monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(
        percent=rng.choice([0.0, 50.0, 100.0]), used=int(rng.choice([0, 7, 14]) * G), total=14 * G))
    monkeypatch.setattr(cli.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="/dev/x", mountpoint="/", fstype="ext4", opts="rw")])
    monkeypatch.setattr(cli.psutil, "disk_usage", lambda m: SimpleNamespace(
        percent=rng.choice([1.0, 50.0, 99.9]), used=int(rng.choice([0.4, 99.9, 418.7]) * G), total=460 * G))
    io = {"r": 0, "w": 0}

    def disk_io():
        io["r"] += rng.choice([0, 1024, 900 * 1024 ** 2]); io["w"] += rng.choice([0, 1024 ** 2, 5 * 1024 ** 3])
        return SimpleNamespace(read_bytes=io["r"], write_bytes=io["w"])

    monkeypatch.setattr(cli.psutil, "disk_io_counters", disk_io)
    net = {"s": 0, "r": 0}

    def net_io():
        net["s"] += rng.choice([0, 512, 900 * 1024, 300 * 1024 ** 2]); net["r"] += rng.choice([0, 1024, 50 * 1024 ** 2, 2 * 1024 ** 3])
        return SimpleNamespace(bytes_sent=net["s"], bytes_recv=net["r"])

    monkeypatch.setattr(cli.psutil, "net_io_counters", net_io)
    monkeypatch.setattr(cli.psutil, "net_connections", lambda: [None] * rng.choice([3, 128, 4321]))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda attrs: [
        _proc(p, rng.choice(["a", "some process name", "x" * 40]), rng.choice([0.0, 9.9, 155.5]))
        for p in range(30)])
    monkeypatch.setattr(cli.psutil, "getloadavg", lambda: (rng.choice([0.1, 9.99, 123.4]),) * 3)
    monkeypatch.setattr(cli.psutil, "pids", lambda: list(range(rng.choice([9, 999, 65000]))))
    monkeypatch.setattr(cli.psutil, "boot_time", lambda: cli.time.time() - rng.choice([60, 86400 * 300]))
    return rng


@pytest.mark.parametrize("width,height", [(140, 50), (120, 40), (100, 30), (80, 24)])
def test_twenty_stormy_frames_share_one_geometry(stormy_machine, width, height):
    """The headline S3 guarantee: whatever the values do, every panel border, every
    bar and every column sits in the same place in every frame."""
    def geometry(frame):
        rows = frame.rstrip("\n").split("\n")
        return [(len(row), tuple(i for i, ch in enumerate(row) if ch in "╭╰│╮╯"))
                for row in rows]
    # Two warm-up frames: the charts need two samples, and the one-time change from
    # "Collecting data" to a plot - whose own frame is drawn in │ - is expected. From
    # the third frame on, nothing may move.
    for _ in range(2):
        cli.render_dashboard(width, height)
    frames = [plain(cli.render_dashboard(width, height), width=width, height=height) for _ in range(20)]
    first = geometry(frames[0])
    for n, frame in enumerate(frames[1:], 2):
        current = geometry(frame)
        if current != first:
            row = next(i for i, (a, b) in enumerate(zip(first, current)) if a != b)
            ref_line = frames[0].split("\n")[row]
            new_line = frame.split("\n")[row]
            pytest.fail(f"frame {n} moved at row {row}:\n  ref: {ref_line!r}\n  new: {new_line!r}")


def test_stormy_frames_keep_every_bar_the_same_length(stormy_machine):
    """Bars are measured per row: same number of bar cells in every frame."""
    def bars(frame):
        out = []
        glyphs = (cli.BAR_FULL, cli.BAR_EMPTY, cli.BAR_SECONDARY, cli.GLYPHS.peak) + tuple(cli.BAR_PARTIALS)
        for row in frame.rstrip("\n").split("\n"):
            out.append(sum(row.count(ch) for ch in glyphs))
        return out
    frames = [plain(cli.render_dashboard(130, 40), width=130, height=40) for _ in range(12)]
    reference = bars(frames[0])
    for n, frame in enumerate(frames[1:], 2):
        assert bars(frame) == reference, f"a bar changed length in frame {n}"


# --- the memory note is never lost to its own long variant ----------------------------------

@pytest.mark.parametrize("width", list(range(37, 80, 3)))
def test_the_memory_note_survives_at_every_width_that_can_hold_the_short_form(monkeypatch, width):
    """The long variant (with the cache suffix) is chosen only when it fits beside a
    usable bar. A threshold of 44 once picked it on a 46-cell panel, where meter() had
    to drop the note entirely - the used/total figure vanished on a 130-column terminal."""
    monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=75.6, used=int(5.2 * G), total=16 * G, available=int(3.9 * G)))
    monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(percent=0.0, used=0, total=0))
    out = plain(cli.get_memory_section(width), width=width)
    assert " 5.2G/ 16.0G" in out, f"note lost at width {width}"


def test_the_cache_suffix_appears_exactly_when_it_fits(monkeypatch):
    monkeypatch.setattr(cli.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=75.6, used=int(5.2 * G), total=16 * G, available=int(3.9 * G)))
    monkeypatch.setattr(cli.psutil, "swap_memory", lambda: SimpleNamespace(percent=0.0, used=0, total=0))
    threshold = T.LABEL_W + T.VALUE_W + T.NOTE_MEM_W + 2 + T.MIN_BAR_W
    assert "cache" not in plain(cli.get_memory_section(threshold - 1), width=threshold - 1)
    assert "cache" in plain(cli.get_memory_section(threshold), width=threshold)
