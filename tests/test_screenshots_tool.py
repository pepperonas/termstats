"""tools/screenshots.py — the README pictures come from here, so it must be reproducible,
chrome-free, and cover the views the README shows."""
import html
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from termstats import __version__
from termstats import theme as T

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def svg_text(svg):
    """The characters a viewer sees. rich writes spaces as &#160; and escapes <>&, so a
    literal search for "last 30s" in the raw SVG matches nothing - a test doing that is blind."""
    return html.unescape("".join(re.findall(r">([^<]*)</text>", svg))).replace("\xa0", " ")


def read_svg(path):
    return path.read_text(encoding="utf-8")


def load_tool():
    spec = importlib.util.spec_from_file_location("screenshots_tool", ROOT / "tools" / "screenshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load_tool()


def test_importing_the_tool_renders_nothing(tmp_path, monkeypatch):
    # It used to run at import (sys.argv[1] as OUT); a test suite must be able to load it.
    monkeypatch.chdir(tmp_path)
    load_tool()
    assert not list(tmp_path.iterdir())


def test_every_view_has_a_renderer(tool):
    for name, view in tool.VIEWS.items():
        assert callable(view), name


EXPECTED_VIEWS = {"hero", "compact", "help", "no-border", "narrow", "snapshot", "list-themes",
                  "glyph-braille", "glyph-block", "glyph-ascii",
                  "color-truecolor", "color-256", "color-16", "color-mono",
                  "eq", "bpm", "db"}


def test_the_view_set_covers_the_readme(tool):
    assert EXPECTED_VIEWS <= set(tool.VIEWS)
    for theme in T.theme_names():
        assert f"theme-{theme}" in tool.VIEWS


def test_render_writes_one_svg_per_requested_view(tool, tmp_path):
    written = tool.render(tmp_path, ["hero", "glyph-ascii"])
    assert sorted(written) == ["glyph-ascii.svg", "hero.svg"]
    assert (tmp_path / "hero.svg").stat().st_size > 10_000


def test_render_refuses_an_unknown_view(tool, tmp_path):
    with pytest.raises(KeyError):
        tool.render(tmp_path, ["not-a-view"])


def test_hero_carries_the_current_version(tool, tmp_path):
    tool.render(tmp_path, ["hero"])
    assert f"v{__version__}" in (tmp_path / "hero.svg").read_text(encoding="utf-8")


def test_frames_have_no_window_chrome(tool, tmp_path):
    tool.render(tmp_path, ["compact"])
    svg = (tmp_path / "compact.svg").read_text(encoding="utf-8")
    assert "<circle" not in svg          # rich's three traffic-light buttons
    assert 'translate(0, 0)' in svg      # the terminal fills the viewBox


def test_rendering_is_reproducible(tool, tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    tool.render(a, ["hero"]); tool.render(b, ["hero"])
    assert (a / "hero.svg").read_bytes() == (b / "hero.svg").read_bytes()


def test_ascii_tile_draws_nothing_outside_seven_bit(tool, tmp_path):
    tool.render(tmp_path, ["glyph-ascii"])
    text = svg_text(read_svg(tmp_path / "glyph-ascii.svg"))
    bad = {ch for ch in text if ord(ch) > 127}
    assert not bad, f"ASCII tile draws {sorted(bad)}"
    assert "last 30s" in text              # the tile is tall enough to hold the charts at all


def test_glyph_tiles_hold_the_charts(tool, tmp_path):
    # The glyph levels differ in the charts; a tile without charts would show three identical pictures.
    tool.render(tmp_path, ["glyph-braille", "glyph-block"])
    for name in ("glyph-braille", "glyph-block"):
        assert "last 30s" in svg_text(read_svg(tmp_path / f"{name}.svg")), name


def test_braille_tile_draws_braille(tool, tmp_path):
    tool.render(tmp_path, ["glyph-braille"])
    svg = (tmp_path / "glyph-braille.svg").read_text(encoding="utf-8")
    assert re.search(r"[⠀-⣿]", svg)


def test_block_tile_draws_no_braille(tool, tmp_path):
    tool.render(tmp_path, ["glyph-block"])
    svg = (tmp_path / "glyph-block.svg").read_text(encoding="utf-8")
    assert not re.search(r"[⠀-⣿]", svg)


def fills(svg):
    return set(re.findall(r"fill:\s*(#[0-9a-fA-F]{6})", svg)) | set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', svg))


def test_colour_tiles_degrade_in_distinct_colours(tool, tmp_path):
    tool.render(tmp_path, ["color-truecolor", "color-16", "color-mono"])
    tc = fills((tmp_path / "color-truecolor.svg").read_text(encoding="utf-8"))
    c16 = fills((tmp_path / "color-16.svg").read_text(encoding="utf-8"))
    mono = fills((tmp_path / "color-mono.svg").read_text(encoding="utf-8"))
    assert len(mono) < len(c16) < len(tc), (len(mono), len(c16), len(tc))
    assert len(mono) <= 3   # background, foreground, maybe one bold/dim variant


def test_snapshot_view_shows_the_collecting_skeleton(tool, tmp_path):
    tool.render(tmp_path, ["snapshot"])
    svg = (tmp_path / "snapshot.svg").read_text(encoding="utf-8")
    assert "collecting" in svg
    assert "Ctrl+C" not in svg           # a snapshot has no exit hint
    text = svg_text(svg)
    assert "last 60s" in text            # a snapshot samples at 1 s, like run_once
    assert "K/s" in text and "n/a" not in text, "a snapshot has rates: priming seeds the collectors"


def test_hero_has_full_charts_not_the_skeleton(tool, tmp_path):
    tool.render(tmp_path, ["hero"])
    assert "collecting" not in (tmp_path / "hero.svg").read_text(encoding="utf-8")


def test_no_border_view_has_no_box_corners(tool, tmp_path):
    tool.render(tmp_path, ["no-border"])
    svg = (tmp_path / "no-border.svg").read_text(encoding="utf-8")
    assert "╭" not in svg and "╰" not in svg


def test_narrow_view_drops_the_charts_whole(tool, tmp_path):
    tool.render(tmp_path, ["narrow", "hero"])
    narrow = svg_text(read_svg(tmp_path / "narrow.svg"))
    hero = svg_text(read_svg(tmp_path / "hero.svg"))
    assert "last 30s" in hero and "processes" in hero
    assert "last 30s" not in narrow, "at 100x26 the charts must be dropped whole"
    assert "processes" in narrow and "TOTAL" in narrow   # what fits stays complete
    assert "collecting" not in narrow                      # dropped, not reduced to a skeleton


def test_svg_text_sees_through_rich_escaping(tool, tmp_path):
    tool.render(tmp_path, ["hero"])
    raw = read_svg(tmp_path / "hero.svg")
    assert "last 30s" not in raw           # the blind check
    assert "last 30s" in svg_text(raw)     # the seeing one


def test_list_themes_view_names_every_theme(tool, tmp_path):
    tool.render(tmp_path, ["list-themes"])
    svg = (tmp_path / "list-themes.svg").read_text(encoding="utf-8")
    for name in T.theme_names():
        assert name in svg


def test_index_page_has_a_section_per_readme_figure(tool, tmp_path):
    tool.write_index(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    for sid in ("hero", "grid", "compact", "help", "no-border", "narrow", "snapshot",
                "list-themes", "glyphs", "colours", "eq", "bpm", "db"):
        assert f'id="{sid}"' in html, sid
    assert __version__ in html


def test_script_entry_point_renders_a_selection(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "screenshots.py"), str(tmp_path), "--only", "compact"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "compact.svg").is_file()
    assert (tmp_path / "index.html").is_file()
    assert not (tmp_path / "hero.svg").exists()


# --- the microphone screens, from the demo synth ---------------------------------------------

def test_audio_views_come_from_the_demo_synth_with_a_tempo(tool, tmp_path):
    pytest.importorskip("numpy")
    tool.render(tmp_path, ["eq", "bpm", "db"])
    eq = svg_text(read_svg(tmp_path / "eq.svg"))
    assert "equalizer" in eq and "16k" in eq and " EQ " in eq
    bpm = svg_text(read_svg(tmp_path / "bpm.svg"))
    assert "BPM" in bpm and "---" not in bpm, "eight seconds of scripted music must yield a tempo"
    db = svg_text(read_svg(tmp_path / "db.svg"))
    assert "dB" in db and "min" in db          # 0.5.0: the shown scale is positive, not dBFS


def test_audio_views_are_reproducible(tool, tmp_path):
    pytest.importorskip("numpy")
    a = tmp_path / "a"; b = tmp_path / "b"
    tool.render(a, ["eq"]); tool.render(b, ["eq"])
    assert (a / "eq.svg").read_bytes() == (b / "eq.svg").read_bytes()
