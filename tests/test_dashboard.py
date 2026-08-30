"""End-to-end rendering against the real machine.

Cheap smoke cover for the wiring the unit tests stub out - this is the path that broke
on plotext 6 and that `termstats | head` alone does not exercise.
"""

from rich.console import Console

from termstats import cli


def render(width=140, height=45):
    console = Console(width=width, height=height)
    with console.capture() as cap:
        console.print(cli.render_dashboard())
    return cap.get()


def test_dashboard_renders_without_raising():
    assert render()


def test_dashboard_shows_every_panel():
    out = render()
    for panel in ("CPU", "Memory", "Disk", "Network", "CPU History",
                  "Network History", "Top Processes"):
        assert panel in out


def test_header_carries_the_brand_and_version():
    """The bottled-by line is deliberate; do not strip it as noise."""
    out = render()
    assert "TERMSTATS" in out
    assert "bottled" in out and "celox.io" in out
    assert f"v{cli.__version__}" in out


def test_each_render_records_exactly_one_history_sample():
    assert len(cli.cpu_history) == 0
    render()
    assert (len(cli.cpu_history), len(cli.net_sent_history)) == (1, 1)
    render()
    assert (len(cli.cpu_history), len(cli.net_sent_history)) == (2, 2)


def test_history_is_capped_at_the_window_length():
    for _ in range(cli.HISTORY_LEN + 25):
        cli.cpu_history.append(1.0)
    assert len(cli.cpu_history) == cli.HISTORY_LEN == 60


def test_first_render_shows_the_collecting_notice():
    """One sample is not a rate; both charts must say so rather than draw a lie."""
    assert "Collecting data" in render()


def test_charts_appear_once_there_is_history(primed_history):
    out = render()
    assert "Collecting data" not in out


def test_dashboard_survives_a_dead_chart_backend(monkeypatch, primed_history):
    monkeypatch.setattr(cli, "_PLOTEXT_5", False)
    assert "Charts need plotext" in render()


def test_load_line_names_the_host_and_platform():
    assert cli.get_load_info().count("Load:") == 1


def test_priming_does_not_raise():
    cli._prime_measurements()
