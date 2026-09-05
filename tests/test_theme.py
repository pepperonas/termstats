"""The design-token layer: OKLab, the ramps, the themes, and their selection.

The ramp is the one thing every panel shares. If it dips in lightness it stops being a
scale in greyscale; if quantisation folds it back on itself it stops being a scale on a
256-colour terminal. Both are pinned here for every theme, not just the default.
"""

import sys

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain


# --- OKLab -----------------------------------------------------------------------------

@pytest.mark.parametrize("hex_colour", ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff",
                                        "#5f7f80", "#c0922c", "#ff7b78", "#808080", "#123456"])
def test_oklab_round_trips_srgb_exactly(hex_colour):
    rgb = T.rgb_of(hex_colour)
    assert T.oklab_to_rgb(T.rgb_to_oklab(rgb)) == rgb


def test_oklab_lightness_orders_grey_correctly():
    greys = [T.lightness((v, v, v)) for v in (0, 64, 128, 192, 255)]
    assert greys == sorted(greys)
    assert greys[0] == pytest.approx(0.0, abs=1e-6) and greys[-1] == pytest.approx(1.0, abs=1e-6)


def test_oklab_interpolation_does_not_pass_through_grey():
    """The reason for OKLab at all: linear sRGB between teal and amber sinks into a
    grey-brown trough. Chroma at the midpoint must stay close to the endpoints'."""
    teal, amber = T.rgb_of("#3aa898"), T.rgb_of("#c0922c")
    ramp = T.Ramp([(0.0, teal), (1.0, amber)])
    def chroma(rgb):
        _, a, b = T.rgb_to_oklab(rgb)
        return (a * a + b * b) ** 0.5
    mid = chroma(ramp.rgb(0.5))
    assert mid >= 0.7 * min(chroma(teal), chroma(amber))


def test_gamut_mapping_shrinks_chroma_instead_of_clipping():
    """An out-of-gamut red pushed to high lightness must come back red, not orange."""
    L, a, b = T.rgb_to_oklab(T.rgb_of("#ff0000"))
    lifted = T.oklab_to_rgb_in_gamut((0.9, a, b))
    r, g, bl = lifted
    assert r >= g and r >= bl, "hue drifted"
    assert T.lightness(lifted) == pytest.approx(0.9, abs=0.02)


def test_in_gamut_colours_are_untouched_by_gamut_mapping():
    for hex_colour in ("#5f7f80", "#c0922c", "#ffffff", "#000000"):
        rgb = T.rgb_of(hex_colour)
        assert T.oklab_to_rgb_in_gamut(T.rgb_to_oklab(rgb)) == rgb


# --- monotone lightness -------------------------------------------------------------------

@pytest.mark.parametrize("name", T.theme_names())
def test_every_theme_ramp_is_lightness_monotone(name):
    """Sampled every hundredth: a ramp that gets darker anywhere reads backwards in
    greyscale and for colour-blind readers."""
    ramp = T.Ramp(T.resolve_theme(name).stops)
    Ls = [ramp.lightness(i / 100) for i in range(101)]
    for i, (a, b) in enumerate(zip(Ls, Ls[1:])):
        assert b >= a - 2e-3, f"{name}: lightness falls at t={i / 100:.2f} ({a:.3f} -> {b:.3f})"


@pytest.mark.parametrize("name", T.theme_names())
def test_no_shipped_theme_needs_a_lightness_repair(name):
    """The repair is a safety net, not a design tool: the shipped stops are monotone as
    designed, so what is on screen is what was chosen."""
    assert not T.Ramp(T.resolve_theme(name).stops).repaired


def test_a_ramp_that_dips_is_repaired_to_monotone():
    """The old 0.3.0 default dipped at the hot end (amber L 0.83 -> rose L 0.70)."""
    dipping = [(0.0, T.rgb_of("#5ad8c8")), (0.55, T.rgb_of("#f0be5a")), (1.0, T.rgb_of("#f06e78"))]
    ramp = T.Ramp(dipping)
    assert ramp.repaired
    Ls = [ramp.lightness(i / 50) for i in range(51)]
    assert all(b >= a - 2e-3 for a, b in zip(Ls, Ls[1:]))


def test_repair_keeps_the_hue_of_the_lifted_stop():
    """Lifting a rose to amber's lightness must leave it red-dominant, not turn it
    orange (channel clipping would) or grey (dropping chroma to zero would)."""
    dipping = [(0.0, T.rgb_of("#f0be5a")), (1.0, T.rgb_of("#f06e78"))]
    r, g, b = T.Ramp(dipping).stops[-1][1]
    assert r > g and r > b, "the lifted rose stop should still be red-dominant"
    assert max(r, g, b) - min(r, g, b) > 40, "the lifted stop collapsed to grey"


@pytest.mark.parametrize("name", T.theme_names())
def test_every_theme_ramp_yields_valid_colours_everywhere(name):
    ramp = T.Ramp(T.resolve_theme(name).stops)
    for i in range(101):
        rgb = ramp.rgb(i / 100)
        assert len(rgb) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)
        assert len(ramp.hex(i / 100)) == 7


@pytest.mark.parametrize("name", T.theme_names())
def test_every_theme_ramp_is_continuous(name):
    ramp = T.Ramp(T.resolve_theme(name).stops)
    prev = ramp.rgb(0.0)
    for i in range(1, 101):
        cur = ramp.rgb(i / 100)
        assert max(abs(cur[k] - prev[k]) for k in range(3)) < 24, f"{name} jumps at {i / 100}"
        prev = cur


@pytest.mark.parametrize("name", T.theme_names())
def test_every_theme_ramp_starts_desaturated(name):
    """Idle should look calm, not cold - so load stands out when it comes."""
    if name == "viridis":
        pytest.skip("viridis is a fixed scientific ramp, not a house design")
    ramp = T.Ramp(T.resolve_theme(name).stops)
    def chroma(t):
        _, a, b = T.rgb_to_oklab(ramp.rgb(t))
        return (a * a + b * b) ** 0.5
    assert chroma(0.0) < chroma(1.0)


def test_ramp_returns_its_stops_exactly_after_the_oklab_round_trip():
    ramp = T.Ramp(T.resolve_theme("default").stops)
    for pos, rgb in ramp.stops:
        assert ramp.rgb(pos) == rgb


def test_ramp_cache_serves_repeated_positions():
    ramp = T.Ramp(T.resolve_theme("default").stops)
    for _ in range(3):
        for i in range(40):
            ramp.rgb(i / 39)
    assert ramp._rgb.cache_info().hits >= 80


# --- quantisation -----------------------------------------------------------------------

def test_bands_counts_runs_and_rejects_a_return():
    assert T.bands([1, 1, 2, 2, 3]) == 3
    assert T.bands([1, 2, 1]) == 0
    assert T.bands([]) == 0


@pytest.mark.parametrize("name", T.theme_names())
def test_on_256_colours_the_ramp_never_returns_to_an_earlier_band(name):
    ramp = T.Ramp(T.resolve_theme(name).stops)
    banded = T.BandedRamp(ramp, "256")
    assert banded.band_count >= 6, f"{name}: only {banded.band_count} bands on 256 colours"


@pytest.mark.parametrize("name", T.theme_names())
def test_on_16_colours_every_theme_still_has_a_scale(name):
    theme = T.resolve_theme(name)
    banded = T.BandedRamp(T.Ramp(theme.stops), "16", theme.bands16)
    assert banded.band_count >= 3
    assert banded.name(0.0) == theme.bands16[0]
    assert banded.name(1.0) == theme.bands16[-1]


def test_nearest_colour_quantisation_alone_can_fold_a_ramp():
    """Counter-check for why BandedRamp exists. This ramp (an interim design of the
    default) makes rich's per-cell nearest-colour choice go 72 -> 73 -> 72: it comes back
    to an earlier palette index, and a meter drawn from it is no longer a scale. The
    shipped stops happen not to fold, but the mechanism is real and must stay caught."""
    folding = [(0.0, T.rgb_of("#5f7f80")), (0.3, T.rgb_of("#3fb0a0")),
               (0.6, T.rgb_of("#d2a13c")), (1.0, T.rgb_of("#ff7b78"))]
    ramp = T.Ramp(folding)
    assert T.bands(T.quantised(ramp, "256", 32)) == 0, "naive quantisation no longer folds"
    assert T.BandedRamp(ramp, "256").band_count >= 6


def test_the_cli_ramp_serves_palette_names_below_truecolor():
    cli.set_theme("default", color="16")
    assert cli.ramp(0.0) == "cyan" and cli.ramp(1.0) == "bright_red"
    cli.set_theme("default", color="256")
    assert cli.ramp(0.5).startswith("color(")
    cli.set_theme("default", color="truecolor")
    assert cli.ramp(0.5).startswith("#")


def test_meters_render_on_a_16_colour_palette(monkeypatch):
    cli.set_theme("default", color="16")
    out = plain(cli.meter("cpu0", 62.5, 50), width=50)
    assert "62.5%" in out and cli.BAR_FULL in out


# --- selection: flag, env, listing -------------------------------------------------------

@pytest.fixture
def run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_live", lambda interval=cli.DEFAULT_INTERVAL: seen.update(mode="live"))
    monkeypatch.setattr(cli, "run_once", lambda: seen.update(mode="once"))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: False)
    monkeypatch.delenv(T.THEME_ENV, raising=False)

    def _run(*args, env=None):
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(sys, "argv", ["termstats", *args])
        cli.main()
        return seen

    return _run


def test_all_six_themes_are_registered():
    assert set(T.theme_names()) == {"default", "mono", "nord", "gruvbox", "catppuccin-mocha", "viridis"}


@pytest.mark.parametrize("flag", ["-t", "--theme", "-theme"])
def test_theme_flag_selects_a_theme(run, flag):
    run(flag, "nord")
    assert cli.THEME.name == "nord"


def test_theme_env_selects_a_theme(run):
    run(env={T.THEME_ENV: "gruvbox"})
    assert cli.THEME.name == "gruvbox"


def test_the_flag_beats_the_environment(run):
    run("--theme", "viridis", env={T.THEME_ENV: "gruvbox"})
    assert cli.THEME.name == "viridis"


def test_no_theme_anywhere_means_default(run):
    run()
    assert cli.THEME.name == "default"


def test_an_unknown_theme_is_an_error_that_names_the_choices(run, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run("--theme", "solarized")
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "solarized" in err and "nord" in err and "viridis" in err


def test_theme_flag_without_a_value_is_rejected(run, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run("--theme")
    assert excinfo.value.code == 2
    assert "theme name" in capsys.readouterr().err


def test_list_themes_prints_every_theme_and_exits_zero(run, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run("--list-themes")
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for name in T.theme_names():
        assert name in out


def test_switching_theme_refreshes_the_network_series_colours():
    cli.set_theme("viridis", color="truecolor")
    assert cli.NET_RX_RGB == cli.RAMP_OBJ.rgb(0.0)
    assert cli.NET_TX_RGB == cli.RAMP_OBJ.rgb(0.55)
    assert cli.NET_RX_RGB != T.Ramp(T.resolve_theme("default").stops).rgb(0.0)


@pytest.mark.parametrize("name", T.theme_names())
def test_every_theme_renders_a_dashboard(name, primed_history):
    cli.set_theme(name, color="truecolor")
    out = plain(cli.render_dashboard(120, 40), width=120, height=40)
    assert "cpu" in out and "processes" in out


# --- capabilities -----------------------------------------------------------------------

@pytest.mark.parametrize("env,expected", [
    ({"NO_COLOR": "1", "COLORTERM": "truecolor"}, "mono"),
    ({"TERM": "dumb", "COLORTERM": "truecolor"}, "mono"),
    ({"COLORTERM": "truecolor", "TERM": "xterm"}, "truecolor"),
    ({"COLORTERM": "24bit"}, "truecolor"),
    ({"TERM": "xterm-256color", "COLORTERM": ""}, "256"),
    ({"TERM": "screen", "COLORTERM": ""}, "16"),
    ({"TERM": "xterm", "COLORTERM": ""}, "16"),
    ({"TERM": "", "COLORTERM": ""}, "16"),
])
def test_colour_depth_follows_the_environment(env, expected):
    assert T.detect(env, stream=sys.__stdout__).color == expected


def test_no_color_wins_over_force_color():
    env = {"NO_COLOR": "1", "FORCE_COLOR": "1", "COLORTERM": "truecolor"}
    assert T.detect(env, stream=sys.__stdout__).color == "mono"


class _Stream:
    def __init__(self, encoding):
        self.encoding = encoding


@pytest.mark.parametrize("env,encoding,expected", [
    ({}, "utf-8", "braille"),
    ({"TERMSTATS_GLYPHS": "block"}, "utf-8", "block"),
    ({"TERMSTATS_GLYPHS": "ascii"}, "utf-8", "ascii"),
    ({"TERMSTATS_GLYPHS": "nonsense"}, "utf-8", "braille"),
    ({"TERM": "dumb"}, "utf-8", "ascii"),
    ({}, "cp1252", "ascii"),
    ({}, "ascii", "ascii"),
])
def test_glyph_level_follows_env_then_stream(env, encoding, expected):
    assert T.detect(env, stream=_Stream(encoding)).glyphs == expected


def test_nerd_font_is_opt_in():
    assert T.detect({}, stream=_Stream("utf-8")).nerd is False
    assert T.detect({"TERMSTATS_NERD_FONT": "1"}, stream=_Stream("utf-8")).nerd is True


def test_every_glyph_set_is_complete():
    """A level is a whole vocabulary; a missing field would mix two levels at runtime."""
    for gs in T.GLYPH_SETS.values():
        for field in T.GlyphSet._fields:
            assert getattr(gs, field) is not None or field == "chart_marker"


def test_the_ascii_vocabulary_is_ascii():
    gs = T.GLYPH_SETS["ascii"]
    for field in T.GlyphSet._fields:
        value = getattr(gs, field)
        if isinstance(value, str):
            assert value.isascii(), f"ascii glyph set field {field} = {value!r}"
