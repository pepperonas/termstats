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


# --- output encoding --------------------------------------------------------------

class FakeStream:
    """Stands in for a redirected Windows stdout."""

    def __init__(self, encoding, reconfigurable=True):
        self.encoding = encoding
        self.reconfigured = None
        self._reconfigurable = reconfigurable

    def reconfigure(self, **kwargs):
        if not self._reconfigurable:
            raise OSError("cannot reconfigure")
        self.reconfigured = kwargs
        self.encoding = kwargs.get("encoding", self.encoding)


def test_a_cp1252_stream_is_widened_to_utf8(monkeypatch):
    """`termstats > out.txt` on Windows: cp1252 cannot encode the block glyphs."""
    out, err = FakeStream("cp1252"), FakeStream("cp1252")
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._ensure_console_encoding()
    assert out.reconfigured == {"encoding": "utf-8", "errors": "replace"}
    assert err.reconfigured == {"encoding": "utf-8", "errors": "replace"}


def test_a_utf8_stream_is_left_alone(monkeypatch):
    stream = FakeStream("utf-8")
    monkeypatch.setattr(cli.sys, "stdout", stream)
    monkeypatch.setattr(cli.sys, "stderr", stream)
    cli._ensure_console_encoding()
    assert stream.reconfigured is None


def test_an_unreconfigurable_stream_does_not_raise(monkeypatch):
    stream = FakeStream("cp1252", reconfigurable=False)
    monkeypatch.setattr(cli.sys, "stdout", stream)
    monkeypatch.setattr(cli.sys, "stderr", stream)
    cli._ensure_console_encoding()          # must not raise


def test_a_stream_without_reconfigure_does_not_raise(monkeypatch):
    class Bare:
        encoding = "ascii"

    monkeypatch.setattr(cli.sys, "stdout", Bare())
    monkeypatch.setattr(cli.sys, "stderr", Bare())
    cli._ensure_console_encoding()          # pytest's capture objects look like this


def test_a_stream_with_no_encoding_attribute_does_not_raise(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", object())
    monkeypatch.setattr(cli.sys, "stderr", object())
    cli._ensure_console_encoding()


def test_every_glyph_the_dashboard_draws_is_in_the_probe():
    """If a new glyph is added to the output, the probe must learn about it."""
    for glyph in ("█", "░", "╭", "\U0001f37b"):
        assert glyph in cli._GLYPH_PROBE
