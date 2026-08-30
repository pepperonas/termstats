"""Chart building and the plotext-6 guard.

Cover for the 1.1.1 defect: plotext 6.0.0 removed the 5.x top-level API, so the first
live render died with AttributeError. The library is pinned <6; these tests pin both the
happy path and the degradation.
"""

import pytest

from termstats import cli


# --- guard ------------------------------------------------------------------------

def test_installed_plotext_has_the_five_api_names():
    """If this fails, the environment resolved plotext 6.x despite the <6 pin."""
    assert cli._PLOTEXT_5, "plotext 5.x API missing - check the pin in pyproject.toml"


def test_guard_checks_every_name_the_renderer_calls():
    import plotext as plt
    for name in ("clear_figure", "plot", "ylim", "plotsize", "build"):
        assert hasattr(plt, name)


def test_missing_api_degrades_to_a_note_instead_of_raising(monkeypatch):
    monkeypatch.setattr(cli, "_PLOTEXT_5", False)
    out = cli._render_chart([([1, 2, 3], "x", "cyan")], "T", None, 40, 8)
    assert out == cli._CHART_NEEDS_PLOTEXT_5
    assert "plotext" in out


def test_the_note_tells_the_user_how_to_fix_it():
    assert "plotext<6" in cli._CHART_NEEDS_PLOTEXT_5


def test_a_raising_plotext_costs_a_chart_not_the_dashboard(monkeypatch):
    """House rule: a collector may return junk, it may not take the process down."""
    def boom(*_a, **_k):
        raise RuntimeError("plotext exploded")

    monkeypatch.setattr(cli.plt, "clear_figure", boom)
    assert cli._render_chart([([1, 2], "x", "cyan")], "T", None, 40, 8) == "  Chart unavailable"


def test_a_raising_plotext_does_not_escape_the_public_chart_helpers(monkeypatch, primed_history):
    monkeypatch.setattr(cli.plt, "plot", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    assert cli.get_cpu_chart(40, 8) == "  Chart unavailable"
    assert cli.get_net_chart(40, 8) == "  Chart unavailable"


# --- not enough data --------------------------------------------------------------

@pytest.mark.parametrize("samples", [0, 1])
def test_charts_wait_for_two_samples(samples):
    for _ in range(samples):
        cli.cpu_history.append(1.0)
        cli.net_sent_history.append(1.0)
        cli.net_recv_history.append(1.0)
    assert cli.get_cpu_chart(40, 8) == "  Collecting data..."
    assert cli.get_net_chart(40, 8) == "  Collecting data..."


def test_two_samples_are_enough(captured_series):
    for _ in range(2):
        cli.cpu_history.append(1.0)
        cli.net_sent_history.append(1.0)
        cli.net_recv_history.append(1.0)
    assert cli.get_cpu_chart(40, 8) == "<chart>"
    assert cli.get_net_chart(40, 8) == "<chart>"


# --- what gets drawn --------------------------------------------------------------

def test_cpu_chart_is_clamped_to_a_percentage_axis(captured_series, primed_history):
    cli.get_cpu_chart(40, 8)
    assert captured_series[0]["ylim"] == (0, 100)


def test_network_chart_has_no_fixed_axis(captured_series, primed_history):
    cli.get_net_chart(40, 8)
    assert captured_series[0]["ylim"] is None


def test_network_values_are_converted_to_kilobytes(captured_series):
    cli.net_sent_history.extend([1024.0, 2048.0])
    cli.net_recv_history.extend([4096.0, 8192.0])
    cli.get_net_chart(40, 8)
    series = captured_series[0]["series"]
    assert series[0][0] == [1.0, 2.0]
    assert series[1][0] == [4.0, 8.0]


def test_network_chart_labels_both_directions(captured_series, primed_history):
    cli.get_net_chart(40, 8)
    labels = [label for _, label, _ in captured_series[0]["series"]]
    assert labels == ["TX KB/s", "RX KB/s"]


def test_chart_size_is_passed_through(captured_series, primed_history):
    cli.get_cpu_chart(77, 13)
    assert (captured_series[0]["width"], captured_series[0]["height"]) == (77, 13)


def test_titles_name_the_history_window(captured_series, primed_history):
    cli.get_cpu_chart(40, 8)
    cli.get_net_chart(40, 8)
    titles = [c["title"] for c in captured_series]
    assert titles == ["CPU Usage (last 60s)", "Network (last 60s)"]


# --- steal series (Linux only) ----------------------------------------------------

def test_steal_series_is_omitted_off_linux(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", False)
    cli.steal_history.clear()
    cli.steal_history.extend([5.0] * 60)
    cli.get_cpu_chart(40, 8)
    assert len(captured_series[0]["series"]) == 1


def test_steal_series_is_omitted_on_linux_when_always_zero(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    cli.get_cpu_chart(40, 8)
    assert len(captured_series[0]["series"]) == 1


def test_steal_series_is_drawn_on_linux_when_nonzero(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    cli.steal_history.clear()
    cli.steal_history.extend([0.0] * 59 + [7.5])
    cli.get_cpu_chart(40, 8)
    series = captured_series[0]["series"]
    assert len(series) == 2
    assert series[1][1] == "Steal %"


# --- real rendering ---------------------------------------------------------------

def test_real_chart_renders_a_titled_block(primed_history):
    out = cli.get_cpu_chart(60, 12)
    assert "CPU Usage" in out
    assert len(out.splitlines()) > 1


def test_real_network_chart_renders(primed_history):
    assert "Network" in cli.get_net_chart(60, 12)
