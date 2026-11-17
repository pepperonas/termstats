use super::*;
use crate::audio_ui::tests::heard;
use crate::screen::BLANK;
use crate::ui::tests::machine;

fn both(w: usize, h: usize) -> (State, Layout) {
    let mut s = machine(8, 2, true);
    s.opts.views = Views(Views::DASHBOARD | Views::AUDIO);
    let lay = layout(&s, w, h);
    (s, lay)
}

#[test]
fn wide_puts_the_audio_in_a_right_column() {
    let (_, lay) = both(200, 60);
    let (d, a) = (lay.dashboard.unwrap(), lay.audio.unwrap());
    assert_eq!(a.w, 80);
    assert_eq!((a.x, d.w), (120, 120));
    assert_eq!((a.y, a.h), (1, 58));
    let (_, lay) = both(400, 60);
    assert_eq!(lay.audio.unwrap().w, AUDIO_COLUMN_MAX);
}

#[test]
fn narrow_puts_the_audio_under_the_dashboard_taking_at_most_half() {
    let (_, lay) = both(150, 50);
    let (d, a) = (lay.dashboard.unwrap(), lay.audio.unwrap());
    assert_eq!((a.x, a.w), (0, 150));
    assert!(a.h <= 48 / 2 && a.h > 0);
    assert_eq!(d.y + d.h, a.y);
    assert_eq!(a.y + a.h, 49);
}

#[test]
fn audio_only_takes_the_whole_body_and_dashboard_only_is_unchanged() {
    let mut s = machine(8, 2, true);
    s.opts.views = Views(Views::AUDIO);
    let lay = layout(&s, 120, 36);
    assert_eq!((lay.dashboard, lay.audio), (None, Some(Rect { x: 0, y: 1, w: 120, h: 34 })));
    s.opts.views = Views(Views::DASHBOARD);
    assert_eq!(layout(&s, 120, 36).audio, None);
    // dashboard only: compose draws exactly what State::draw draws
    let mut a = Screen::new(120, 36);
    let mut b = Screen::new(120, 36);
    let mut audio = heard(1.0);
    draw(&mut machine(8, 2, false), &mut audio, &mut a, 1.0, 3);
    machine(8, 2, false).draw(&mut b);
    assert!(a.cells == b.cells);
}

#[test]
fn no_dashboard_cell_lies_inside_the_audio_rect() {
    for (w, h) in [(200, 60), (120, 40)] {
        let (mut s, lay) = both(w, h);
        let mut audio = heard(5.0);
        let mut scr = Screen::new(w, h);
        draw(&mut s, &mut audio, &mut scr, 5.0, REGION_DASHBOARD);
        let a = lay.audio.unwrap();
        for y in a.y..a.y + a.h {
            for x in a.x..a.x + a.w {
                assert!(scr.cells[y * w + x] == BLANK, "{w}x{h}: dashboard at ({x},{y}) inside {a:?}");
            }
        }
        // and the audio stays inside its rect
        let mut scr = Screen::new(w, h);
        draw(&mut s, &mut audio, &mut scr, 5.1, REGION_AUDIO);
        for y in 0..h {
            for x in 0..w {
                assert!(scr.cells[y * w + x] == BLANK || a.contains(x, y), "{w}x{h}: audio at ({x},{y})");
            }
        }
    }
}

/// `cargo test --lib dump_frames -- --ignored --nocapture` prints two composed frames.
#[test]
#[ignore]
fn dump_frames() {
    for (views, w, h) in [(Views::DASHBOARD | Views::AUDIO, 200, 60), (Views::AUDIO, 120, 36)] {
        let mut s = machine(8, 2, true);
        s.opts.views = Views(views);
        let mut audio = heard(10.0);
        let mut scr = Screen::new(w, h);
        for i in 0..5 {
            scr.reset(w, h);
            draw(&mut s, &mut audio, &mut scr, 10.0 + i as f64 / 30.0, 3);
        }
        println!("--- {w}x{h} views {views}");
        for y in 0..h {
            println!("{}", scr.cells[y * w..(y + 1) * w].iter().map(|c| c.ch).collect::<String>().trim_end());
        }
    }
}
