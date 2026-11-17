//! The audio screens drawn from the demo synth into a Screen, without a microphone.

use super::*;
use crate::audio::{DemoAudio, BLOCK};
use crate::screen::BLANK;
use crate::ui::tests::machine;

/// An AudioState that has heard `seconds` of the demo's music.
pub(crate) fn heard(seconds: f64) -> AudioState {
    let mut audio = AudioState::new();
    let mut demo = DemoAudio::new(126.0, 44100.0);
    while demo.now() < seconds {
        let block = demo.read(BLOCK);
        audio.analyzer.feed(&block, 44100.0, demo.now());
    }
    audio
}

fn state(views: u32) -> State {
    let mut s = machine(8, 2, true);
    s.opts.views = Views(views);
    s
}

const SUBSETS: [u32; 7] = [Views::EQ, Views::LEVEL, Views::BPM, Views::EQ | Views::LEVEL, Views::EQ | Views::BPM,
                           Views::LEVEL | Views::BPM, Views::AUDIO];
const SIZES: [(usize, usize); 3] = [(80, 24), (120, 36), (200, 60)];

fn text(scr: &Screen, r: Rect) -> String {
    (r.y..r.y + r.h).map(|y| (r.x..r.x + r.w).map(|x| scr.cells[y * scr.w + x].ch).collect::<String>() + "\n").collect()
}

/// Draw `views` into a rectangle inset by a margin, a few frames apart.
fn frame(views: u32, w: usize, h: usize, audio: &mut AudioState) -> (Screen, Rect) {
    let mut s = state(views);
    let mut scr = Screen::new(w, h);
    let r = Rect { x: 3, y: 2, w: w - 6, h: h - 4 };
    let now = audio.analyzer.db_history.back().map_or(0.0, |p| p.0);
    for i in 0..3 {
        scr.reset(w, h);
        draw(&mut s, audio, &mut scr, r, now + i as f64 / 30.0);
    }
    (scr, r)
}

#[test]
fn every_subset_draws_inside_its_rect() {
    let mut audio = heard(10.0);
    for views in SUBSETS {
        for (w, h) in SIZES {
            let (scr, r) = frame(views, w, h, &mut audio);
            let mut drawn = 0;
            for y in 0..h {
                for x in 0..w {
                    if scr.cells[y * w + x] != BLANK {
                        assert!(r.contains(x, y), "views {views} at {w}x{h}: ({x},{y}) outside {r:?}\n{}",
                                text(&scr, Rect { x: 0, y: 0, w, h }));
                        drawn += 1;
                    }
                }
            }
            assert!(drawn > 50, "views {views} at {w}x{h} drew almost nothing");
            // every enabled view has its titled panel
            let all = text(&scr, r);
            for (bit, title) in [(Views::EQ, "equalizer"), (Views::LEVEL, "level"), (Views::BPM, "tempo")] {
                if views & bit != 0 {
                    assert!(all.contains(title), "views {views} at {w}x{h}: no {title}\n{all}");
                }
            }
        }
    }
}

#[test]
fn the_equalizer_has_its_frequency_axis() {
    let mut audio = heard(5.0);
    for (w, h) in SIZES {
        let (scr, r) = frame(Views::EQ, w, h, &mut audio);
        let all = text(&scr, r);
        let axis = all.lines().find(|l| l.contains("16k")).unwrap_or_else(|| panic!("no 16k at {w}x{h}\n{all}"));
        assert!(axis.split_whitespace().any(|t| t == "40"), "{axis}");
        assert!(all.contains('█'), "music lights bars");
    }
}

#[test]
fn without_microphone_access_every_panel_says_so_and_shows_no_bars() {
    for (mic, line) in [(Mic::NoPermission, "microphone access is off"), (Mic::NoDevice, "no input device")] {
        let mut audio = heard(5.0);
        audio.mic = mic;
        for (w, h) in SIZES {
            let (scr, r) = frame(Views::AUDIO, w, h, &mut audio);
            let all = text(&scr, r);
            assert_eq!(all.matches(line).count(), 3, "{w}x{h}\n{all}");
            if mic == Mic::NoPermission {
                assert_eq!(all.matches("System Settings > Privacy > Microphone").count(), 3, "{all}");
            }
            for glyph in SPARK.iter().take(7).chain([&BAR_SECONDARY, &VPEAK, &BAR_EMPTY]) {
                assert!(!all.contains(*glyph), "{glyph} at {w}x{h}\n{all}");
            }
            // no level reading: the only "dB" left is the one in the level panel's title
            assert_eq!(all.matches("dB").count(), all.matches("dB, uncalibrated").count(), "level digits\n{all}");
        }
    }
}

#[test]
fn never_fed_is_the_quiet_state() {
    let mut audio = AudioState::new();
    let (scr, r) = frame(Views::LEVEL | Views::BPM, 120, 36, &mut audio);
    let all = text(&scr, r);
    assert!(all.contains("waiting for"), "{all}");
    assert!(!all.contains("microphone"), "{all}");
    assert!(all.contains("min  --   max  --"), "no extremes before the first block\n{all}");
    assert!(!all.contains("100.0"), "{all}");
    // the tempo is the big "---": the minus glyph row of three squares, twice per digit
    assert!(all.contains("██████  ██████  ██████"), "{all}");
}

#[test]
fn level_and_tempo_sit_side_by_side_when_wide_and_stack_when_narrow() {
    let mut audio = heard(8.0);
    let row_of = |scr: &Screen, r: Rect, word: &str| {
        (r.y..r.y + r.h).find(|&y| text(scr, Rect { y, h: 1, ..r }).contains(word))
    };
    let (scr, r) = frame(Views::LEVEL | Views::BPM, 120, 36, &mut audio);
    assert_eq!(row_of(&scr, r, "level"), row_of(&scr, r, "tempo"), "wide: one row of titles");
    // the extremes face the kick band on the same row, the level meter the confidence
    assert_eq!(row_of(&scr, r, "min "), row_of(&scr, r, "kick band"));
    assert_eq!(row_of(&scr, r, "dB │"), row_of(&scr, r, "confidence"));
    let (scr, r) = frame(Views::LEVEL | Views::BPM, 80, 24, &mut audio);
    assert!(row_of(&scr, r, "level").unwrap() < row_of(&scr, r, "tempo").unwrap(), "narrow: stacked");
}

#[test]
fn helpers_agree_with_cli_py() {
    assert_eq!(eq_columns(200, 28, 4), 28);
    assert_eq!(eq_columns(30, 28, 4), 9);
    assert_eq!(eq_columns(5, 28, 4), EQ_MIN_COLUMNS);
    assert_eq!(group_bands(&[1.0, 5.0, 2.0, 3.0], 2), vec![5.0, 3.0]);
    assert_eq!(group_bands(&[1.0, 2.0], 4), vec![1.0, 2.0]);
    assert_eq!(eighths(0.5, 4), (2, 0));
    assert_eq!(eighths(0.999, 4), (4, 0));
    assert_eq!(eighths(1.7, 4), (4, 0));
    assert_eq!(mix_columns(84), Some(40));
    assert_eq!(mix_columns(83), None);
    let edges = crate::audio::spectrum::band_edges(44100.0);
    assert_eq!(band_of(&edges, 40.0, BANDS), 0);
    assert_eq!(band_of(&edges, 16000.0, BANDS), BANDS - 1);
    let rows = big_digits("1.0");
    assert_eq!(rows.len(), 5);
    assert_eq!(rows[0], "  ██            ██████");
    assert!(rows.iter().all(|r| r.chars().count() == 3 * 6 + 2 * 2));
    assert_eq!(db_pct(-40.0), 50.0);
}

#[test]
fn the_readout_holds_its_value_for_its_own_clock() {
    let mut audio = AudioState::new();
    assert_eq!(audio.readout(1, 10.0, 1.0), 10.0);
    assert_eq!(audio.readout(1, 20.0, 1.1), 10.0);
    assert_eq!(audio.readout(1, 30.0, 1.25), 30.0);
}

#[test]
fn history_series_buckets_means() {
    let h: Vec<(f64, f64)> = (0..10).map(|i| (i as f64, i as f64)).collect();
    let (v, span) = history_series(&h, 9.0, 60.0, 5).unwrap();
    assert_eq!(span, 9.0);
    assert_eq!(v.len(), 5);
    assert!(v.windows(2).all(|p| p[1] >= p[0]));
    assert!(history_series(&h[..1], 9.0, 60.0, 5).is_none());
}

#[test]
fn a_falling_bar_leaves_an_afterglow() {
    let mut audio = heard(6.0);
    let mut s = state(Views::EQ);
    let mut scr = Screen::new(120, 36);
    let r = Rect { x: 0, y: 0, w: 120, h: 36 };
    let mut now = 6.0;
    draw(&mut s, &mut audio, &mut scr, r, now);
    let mut seen = false;
    for _ in 0..8 {
        // silence: the bars fall, their trails follow more slowly
        audio.analyzer.feed(&[0.0; 1470], 44100.0, now);
        now += 1.0 / 30.0;
        scr.reset(120, 36);
        draw(&mut s, &mut audio, &mut scr, r, now);
        seen |= text(&scr, r).contains(BAR_SECONDARY);
    }
    assert!(seen, "{}", text(&scr, r));
}

#[test]
fn tiny_rects_neither_panic_nor_spill() {
    let mut audio = heard(5.0);
    for views in SUBSETS {
        for w in [12, 30, 45, 90] {
            for h in 4..=30 {
                let mut s = state(views);
                let mut scr = Screen::new(w + 4, h + 4);
                let r = Rect { x: 2, y: 2, w, h };
                draw(&mut s, &mut audio, &mut scr, r, 5.0);
                for y in 0..h + 4 {
                    for x in 0..w + 4 {
                        assert!(scr.cells[y * (w + 4) + x] == BLANK || r.contains(x, y), "views {views} {w}x{h} at ({x},{y})");
                    }
                }
            }
        }
    }
}

/// How many status lines (`●  BPM …  ·  conf ▉…`) the frame carries.
fn huds(all: &str) -> usize {
    all.matches("·  conf ").count()
}

#[test]
fn the_status_line_is_drawn_at_most_once() {
    let mut audio = heard(8.0);
    for views in SUBSETS {
        let eq = views & Views::EQ != 0;
        let both = views & Views::LEVEL != 0 && views & Views::BPM != 0;
        let want = if eq || !both { 1 } else { 0 };
        for (w, h) in SIZES {
            let (scr, r) = frame(views, w, h, &mut audio);
            let all = text(&scr, r);
            assert_eq!(huds(&all), want, "views {views} at {w}x{h}\n{all}");
            if both && !eq {
                let title = all.lines().find(|l| l.contains("tempo")).unwrap();
                assert!(title.contains(BEAT_ON) || title.contains(BEAT_OFF), "beat dot in the tempo title\n{all}");
            }
        }
    }
}

#[test]
fn with_the_microphone_off_level_and_tempo_always_show_dashes() {
    let big_dash = "██████  ██████  ██████";
    for mic in [Mic::NoPermission, Mic::NoDevice] {
        let mut audio = heard(5.0);
        audio.mic = mic;
        for (w, h) in [(80, 24), (200, 60)] {
            for views in SUBSETS {
                let (scr, r) = frame(views, w, h, &mut audio);
                let all = text(&scr, r);
                let numbers = (views & Views::LEVEL != 0) as usize + (views & Views::BPM != 0) as usize;
                // big dashes (their middle row) or a one-line "---", per panel body
                let dashes: usize = all.lines()
                    .map(|l| l.matches(big_dash).count() + l.split('│').filter(|seg| seg.trim() == "---").count())
                    .sum();
                assert_eq!(dashes, numbers, "views {views} {mic:?} at {w}x{h}\n{all}");
                // no bars anywhere: only the dashes' own squares may be drawn
                let squares = all.matches('█').count();
                assert_eq!(squares % 9, 0, "{all}");
                if views == Views::EQ {
                    assert_eq!(squares, 0, "no bars in the equalizer\n{all}");
                }
            }
        }
    }
}
