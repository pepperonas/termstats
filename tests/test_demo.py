"""S9 - `--demo`: a scripted, repeatable machine that carries the whole psutil surface
cli.py uses, says DEMO on every frame, and never touches the real system.
"""

import re
import sys
from pathlib import Path

import pytest

from termstats import cli, demo
from termstats.demo import DemoSource
from helpers import plain

CLI_SRC = (Path(__file__).resolve().parent.parent / "termstats" / "cli.py").read_text(encoding="utf-8")


def play(source, frames):
    """Drive a source through `frames` frames the way the collectors do, return the series."""
    out = []
    for _ in range(frames):
        cores = source.cpu_percent(percpu=True)
        procs = [p.info["cpu_percent"] for p in source.process_iter()]
        out.append((tuple(round(c, 3) for c in cores), source.cpu_percent(),
                    source.net_io_counters(), source.disk_io_counters(),
                    source.disk_usage("/").percent, tuple(procs)))
    return out


# --- the contract with cli.py --------------------------------------------------------------

def test_the_demo_carries_every_psutil_name_the_cli_uses():
    """If a collector starts using a new psutil call, this fails here - not at runtime
    with an AttributeError in the middle of a screenshot."""
    used = set(re.findall(r"\bpsutil\.([A-Za-z_]+)", CLI_SRC))
    source = DemoSource()
    missing = sorted(name for name in used if not hasattr(source, name))
    assert not missing, missing


def test_the_demo_process_carries_what_the_process_table_reads():
    p = next(DemoSource().process_iter())
    for key in ("pid", "name", "cpu_percent", "memory_percent", "memory_info"):
        assert key in p.info
    assert p.info["memory_info"].rss > 0
    assert p.cpu_percent() == p.info["cpu_percent"]


# --- deterministic ----------------------------------------------------------------------------

def test_the_same_seed_tells_the_same_story():
    assert play(DemoSource(7), 200) == play(DemoSource(7), 200)


def test_a_different_seed_tells_a_different_one():
    assert play(DemoSource(7), 40) != play(DemoSource(8), 40)


def test_the_demo_clock_advances_one_interval_per_frame():
    source = DemoSource(interval=0.5)
    t0 = source.now()
    source.cpu_percent(percpu=True)
    source.cpu_percent(percpu=True)
    assert source.now() - t0 == pytest.approx(1.0)


def test_only_the_per_core_read_advances_the_frame():
    """One frame = one cpu_percent(percpu=True); the other reads must not move time."""
    source = DemoSource()
    f = source.frame
    source.cpu_percent()
    source.virtual_memory()
    source.net_io_counters()
    list(source.process_iter())
    assert source.frame == f


# --- the story is worth looking at -------------------------------------------------------------

def test_the_story_has_a_load_spike_and_quiet_between():
    totals = [row[1] for row in play(DemoSource(), demo.PERIOD)]
    assert max(totals) >= 75 and min(totals) <= 30


def test_the_story_has_a_network_burst_over_a_low_baseline():
    source = DemoSource(interval=0.5)
    rates = []
    for _ in range(demo.PERIOD):
        before = source.net_io_counters().bytes_recv
        source.cpu_percent(percpu=True)
        rates.append((source.net_io_counters().bytes_recv - before) / source.interval)
    assert max(rates) >= 20 * demo.MiB
    assert sorted(rates)[len(rates) // 2] < 1 * demo.MiB, "the baseline must stay quiet"


def test_the_root_disk_only_ever_fills():
    pct = [row[4] for row in play(DemoSource(), 300)]
    assert all(b >= a for a, b in zip(pct, pct[1:])) and pct[-1] > pct[0]


def test_the_process_table_reacts_to_the_spike():
    source = DemoSource()
    by_phase = {}
    for _ in range(demo.PERIOD):
        source.cpu_percent(percpu=True)
        by_phase[source.phase()] = {p.info["name"]: p.info["cpu_percent"] for p in source.process_iter()}
    mid = (demo.SPIKE[0] + demo.SPIKE[1]) // 2
    assert by_phase[mid]["python3"] > by_phase[5]["python3"] + 20


def test_the_story_repeats_so_a_live_demo_keeps_happening():
    source = DemoSource()
    rows = play(source, 2 * demo.PERIOD + 5)
    totals = [r[1] for r in rows]
    late = totals[demo.PERIOD:2 * demo.PERIOD]
    assert max(late) >= 75, "the second period has no spike"


# --- plugged into the dashboard ----------------------------------------------------------------

def test_set_demo_swaps_the_source_and_puts_psutil_back():
    source = DemoSource()
    cli.set_demo(source)
    assert cli.psutil is source and cli.DEMO is source
    cli.set_demo(None)
    assert cli.psutil is cli._REAL_PSUTIL and cli.DEMO is None


def test_rate_collectors_use_the_demo_clock_not_the_wall_clock():
    """Prefill plays sixty frames in a tight loop; on the wall clock that is microseconds
    apart and the rates would be absurd. On the demo clock they are the designed ones."""
    source = DemoSource(interval=0.5)
    cli.set_demo(source)
    cli.get_network_section(80)
    source.cpu_percent(percpu=True)        # the frame step the cpu section takes in between
    _, sent_s, recv_s = cli.get_network_section(80)
    assert recv_s == pytest.approx(source._rx, rel=0.01)
    assert sent_s == pytest.approx(source._tx, rel=0.01)


def test_a_demo_dashboard_says_demo_on_every_frame_and_names_no_real_host():
    import platform
    cli.set_demo(DemoSource())
    for width in (80, 120, 160):
        head = plain(cli.header_line(width), width=width)
        assert "DEMO" in head, width
        assert DemoSource.node[:12] in head
        assert platform.node()[:12] not in head or platform.node()[:12] == DemoSource.node[:12]


def test_a_demo_snapshot_opens_with_full_charts_in_the_middle_of_the_spike():
    """The frame a screenshot shows must be worth showing: charts full, the burst inside
    the window, the CPU on its way up - not the quiet after the story."""
    cli.set_demo(DemoSource())
    cli._prime_measurements()
    cli._prefill_history()
    out = plain(cli.render_dashboard(120, 40), width=120, height=40)
    assert len(cli.cpu_history) == cli.HISTORY_LEN
    assert "collecting" not in out
    assert "postgres" in out and "DEMO" in out and "celox.io" in out
    assert demo.SPIKE[0] <= cli.DEMO.phase() < demo.SPIKE[1], cli.DEMO.phase()
    assert cli.cpu_history[-1] >= 60, "the visible frame is not on the spike"
    assert max(cli.net_recv_history) >= 20 * demo.MiB, "the burst is not in the window"


def test_a_demo_snapshot_is_reproducible_to_the_character():
    def shot():
        cli.set_demo(DemoSource(7))
        cli.cpu_history.clear(); cli.steal_history.clear()
        cli.net_sent_history.clear(); cli.net_recv_history.clear()
        cli._smoother.reset(); cli._peaks.reset()
        for f in (cli.get_disk_section, cli.get_network_section):
            for attr in ("_last", "_last_io", "_last_time"):
                if hasattr(f, attr):
                    delattr(f, attr)
        cli._prefill_history()
        out = plain(cli.render_dashboard(120, 40), width=120, height=40)
        return re.sub(r"\d\d:\d\d:\d\d", "hh:mm:ss", out)     # the wall clock is real

    assert shot() == shot()


def test_run_once_in_demo_mode_does_not_wait_for_a_sample(monkeypatch):
    cli.set_demo(DemoSource())
    assert cli.DEMO.interval != cli.SNAPSHOT_SAMPLE_S
    monkeypatch.setattr(cli.time, "sleep", lambda s: pytest.fail("slept in demo mode"))
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda r, *a, **k: printed.append(r))
    cli.run_once()
    assert printed and len(cli.cpu_history) == cli.HISTORY_LEN
    assert cli.DEMO.interval == cli.SNAPSHOT_SAMPLE_S, "one demo frame = one second in a snapshot"


# --- the flag ---------------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_live", lambda interval=cli.DEFAULT_INTERVAL: seen.update(mode="live"))
    monkeypatch.setattr(cli, "run_once", lambda: seen.update(mode="once"))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: True)

    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["termstats", *args])
        cli.main()
        return seen

    return _run


@pytest.mark.parametrize("flag", ["--demo", "-demo"])
def test_demo_flag_installs_the_source(run, flag):
    assert run(flag)["mode"] == "live"
    assert isinstance(cli.DEMO, DemoSource) and cli.psutil is cli.DEMO


def test_demo_uses_the_requested_interval_for_its_clock(run):
    run("--demo", "-i", "2")
    assert cli.DEMO.interval == 2.0


def test_demo_combines_with_once(run):
    assert run("--demo", "--once")["mode"] == "once"
    assert cli.DEMO is not None


def test_without_the_flag_the_real_psutil_stays(run):
    run()
    assert cli.DEMO is None and cli.psutil is cli._REAL_PSUTIL


def test_demo_is_documented(run, capsys):
    with pytest.raises(SystemExit):
        run("--help")
    assert "--demo" in capsys.readouterr().out


def test_the_tape_records_the_demo_and_says_so():
    tape = (Path(__file__).resolve().parent.parent / "tools" / "demo.tape").read_text(encoding="utf-8")
    assert 'Type "termstats --demo"' in tape
    assert "Nothing real is measured" in tape


# --- the header clock belongs to the demo clock too --------------------------------------

def test_header_clock_follows_the_demo_clock_not_the_wall():
    import time
    source = demo.DemoSource(demo.DEFAULT_SEED, 1.0)
    source.T0 = 1_000_000_000.0          # 2001-09-09, an instant no wall clock will hit
    cli.set_demo(source)
    cli._prime_measurements()
    expected = time.strftime("%H:%M:%S", time.localtime(source.now()))
    text = cli.header_line(160).plain
    assert expected in text, f"header shows the wall clock, expected the demo's {expected}: {text!r}"


def test_two_demo_renders_of_the_same_frame_are_identical():
    from rich.console import Console
    import io
    frames = []
    for _ in range(2):
        source = demo.DemoSource(demo.DEFAULT_SEED, 1.0)
        source.T0 = 1_000_000_000.0
        cli.set_demo(source)
        cli._prime_measurements()
        con = Console(file=io.StringIO(), width=160, height=40, force_terminal=True, color_system="truecolor",
                      no_color=False, legacy_windows=False)
        con.print(cli.header_line(160))
        frames.append(con.file.getvalue())
    assert frames[0] == frames[1]
