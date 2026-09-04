"""Shared fixtures.

termstats keeps its rate state in module-level deques, module globals and function
attributes. Tests that touch a collector must start from a known state, or they pass or
fail depending on what ran before them.
"""

import pytest

from termstats import cli


@pytest.fixture(autouse=True)
def clean_module_state():
    """Reset every piece of cross-call state termstats carries between renders."""
    for dq in (cli.cpu_history, cli.steal_history, cli.net_sent_history, cli.net_recv_history):
        dq.clear()
    cli._steal_last_total = None
    cli._steal_last_steal = None
    # run_live()/run_once() write this; the chart titles read it, so a test that ran a
    # mode would otherwise relabel every later chart test's x-axis.
    cli.sample_interval = cli.DEFAULT_INTERVAL
    # Capability detection is global; a test that drops to ASCII must not leak that.
    cli.UNICODE = True
    for fn, attrs in (
        (cli.get_disk_section, ("_last_io", "_last_time")),
        (cli.get_network_section, ("_last", "_last_time")),
    ):
        for attr in attrs:
            if hasattr(fn, attr):
                delattr(fn, attr)
    yield
    for dq in (cli.cpu_history, cli.steal_history, cli.net_sent_history, cli.net_recv_history):
        dq.clear()
    cli.sample_interval = cli.DEFAULT_INTERVAL
    cli.UNICODE = True


@pytest.fixture
def ascii_mode():
    """Pretend stdout cannot carry the drawing glyphs."""
    cli.UNICODE = False
    yield
    cli.UNICODE = True


@pytest.fixture
def primed_history():
    """60 samples in every history deque, so the charts have something to draw."""
    for i in range(cli.HISTORY_LEN):
        cli.cpu_history.append(float(i % 100))
        cli.steal_history.append(0.0)
        cli.net_sent_history.append(1024.0 * i)
        cli.net_recv_history.append(2048.0 * i)


@pytest.fixture
def captured_series(monkeypatch):
    """Intercept _render_chart so a test can inspect what the chart was asked to draw."""
    calls = []

    def fake(series, ylim, width, height):
        calls.append({"series": series, "ylim": ylim, "width": width, "height": height})
        return "<chart>"

    monkeypatch.setattr(cli, "_render_chart", fake)
    return calls
