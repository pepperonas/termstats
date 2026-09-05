"""S7 - header and footer: identity left, history centred, liveness right; a footer that
names the author with the year the clock says, and offers the exit only to a human.
"""

import re
import sys
import time

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain

YEAR = str(time.localtime().tm_year)


def bottom(width=120, height=40):
    rows = plain(cli.render_dashboard(width, height), width=width, height=height).rstrip("\n").split("\n")
    return rows[-1]


# --- footer ------------------------------------------------------------------------------

def test_footer_names_the_author_and_the_current_year(primed_history):
    row = bottom()
    assert f"© {YEAR} Martin Pfeffer | celox.io" in row


def test_footer_brand_is_right_aligned(primed_history):
    row = bottom(120, 40)
    assert len(row) == 120 and row.rstrip().endswith("celox.io")


def test_footer_year_comes_from_the_clock(monkeypatch):
    """A literal year is right for one release and wrong for every one after."""
    monkeypatch.setattr(cli, "_current_year", lambda: 2031)
    assert "© 2031 " in plain(cli.footer_line(80), width=80)
    monkeypatch.setattr(cli, "_current_year", lambda: 2040)
    assert "© 2040 " in plain(cli.footer_line(80), width=80)


def test_live_footer_offers_the_exit(monkeypatch):
    # 0.5.0 named Esc first: it is the key a full-screen program is expected to answer to,
    # and Ctrl+C stays listed because it always worked. The hint still opens the line.
    monkeypatch.setattr(cli, "LIVE", True)
    assert plain(cli.footer_line(80), width=80).lstrip().startswith("Esc or Ctrl+C to exit")


def test_snapshot_footer_has_no_exit_hint():
    """A report in a file or a cron mail has nobody to press Ctrl+C."""
    assert "Ctrl+C" not in plain(cli.footer_line(80), width=80)


def test_footer_is_one_fixed_width_row():
    for width in (40, 60, 80, 120, 200):
        row = plain(cli.footer_line(width), width=width).rstrip("\n")
        assert "\n" not in row and len(row) == width


def test_footer_drops_the_hint_before_the_brand(monkeypatch):
    monkeypatch.setattr(cli, "LIVE", True)
    wide = plain(cli.footer_line(80), width=80)
    tight = plain(cli.footer_line(40), width=40)
    assert "Ctrl+C" in wide and "celox.io" in wide
    assert "Ctrl+C" not in tight and f"© {YEAR} Martin Pfeffer | celox.io" in tight


def test_footer_is_ascii_safe(ascii_mode):
    row = plain(cli.footer_line(80), width=80)
    assert row.isascii() and f"(c) {YEAR}" in row


def test_the_copyright_sign_is_in_the_glyph_probe():
    """cp1252 has no ©; the probe is what decides whether stdout gets widened."""
    assert "©" in T.GLYPH_PROBE


@pytest.mark.parametrize("mode", ["default", "compact", "no_border"])
@pytest.mark.parametrize("width,height", [(80, 24), (100, 30), (140, 50), (60, 20)])
def test_footer_is_the_last_row_in_every_frame_mode(mode, width, height, primed_history):
    cli.set_frame(compact=(mode == "compact"), no_border=(mode == "no_border"))
    assert "celox.io" in bottom(width, height)


@pytest.mark.parametrize("mode", ["default", "no_border"])
def test_every_process_row_the_budget_ordered_is_on_screen(mode, monkeypatch, primed_history):
    """A budget that forgets the footer's row over-plans by one: rich squeezes the ratio
    section and the process list silently loses its last row. Nothing else notices -
    the picture still fits, fills and has no blank line - so this pins the count."""
    cli.set_frame(no_border=(mode == "no_border"))
    ordered = {}
    real = cli.get_top_processes

    def spy(width, n=8):
        ordered["n"] = n
        return real(width, n)

    monkeypatch.setattr(cli, "get_top_processes", spy)
    rows = plain(cli.render_dashboard(120, 40), width=120, height=40).rstrip("\n").split("\n")
    assert len(rows) == 40 and rows[0].lstrip().startswith("TERMSTATS") and "celox.io" in rows[-1]
    # Only rows below the panel title count - the chart's y-axis ticks (100, 50) would
    # otherwise pass for process rows.
    start = next(i for i, r in enumerate(rows) if "processes" in r)
    shown = [r for r in rows[start + 2:-1] if re.match(r"^[│ ]\s*\d+\s+\S", r)]
    assert ordered and len(shown) == ordered["n"], (ordered, len(shown))


# --- the run modes own the flag ------------------------------------------------------------

def test_run_once_is_not_live(monkeypatch):
    monkeypatch.setattr(cli, "LIVE", True)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: None)
    cli.run_once()
    assert cli.LIVE is False


def test_run_live_is_live(monkeypatch):
    class Abort:
        def __init__(self, *a, **k): pass
        def __enter__(self): raise KeyboardInterrupt
        def __exit__(self, *a): return False
    monkeypatch.setattr(cli, "Live", Abort)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli.run_live(0.5)
    assert cli.LIVE is True


# --- header: identity left, history centred, liveness right -------------------------------

def spark_gaps(line):
    """(spaces before the sparkline, spaces after it) on a rendered header line."""
    idx = [i for i, ch in enumerate(line) if ch in cli.SPARK]
    assert idx, "no sparkline on the line"
    a, b = idx[0], idx[-1]
    before = len(line[:a]) - len(line[:a].rstrip())
    after = len(line[b + 1:]) - len(line[b + 1:].lstrip())
    return before, after


def test_header_sparkline_sits_in_the_middle_of_the_free_space(primed_history):
    # (At 120 columns the width-only tiering already drops the sparkline - that is S3's
    # contract, pinned in test_stability; the centring is measured where it exists.)
    for width in (150, 160, 200):
        before, after = spark_gaps(plain(cli.header_line(width), width=width))
        assert abs(before - after) <= 1, (width, before, after)


def test_header_sparkline_position_does_not_depend_on_the_values(primed_history):
    """Fixed fields left, fixed tail right: the middle is a function of the width only."""
    first = spark_gaps(plain(cli.header_line(160), width=160))
    for i in range(60):
        cli.cpu_history.append(100.0 if i % 2 else 0.0)
    assert spark_gaps(plain(cli.header_line(160), width=160)) == first


def test_header_keeps_identity_left_and_clock_right(primed_history):
    line = plain(cli.header_line(160), width=160)
    assert line.lstrip().startswith("TERMSTATS")
    assert re.search(r"\d\d:\d\d:\d\d\s+[\d.]+s\s+v[\d.]+\s*$", line)
