//! The C interface driven the way a C caller drives it: raw pointers, return codes.

use super::*;
use std::ffi::CStr;

// The C functions are `unsafe` (they take raw pointers); these shims keep the tests
// reading like the C caller they stand in for. Local items shadow the glob import.
fn ts_create(opts: *const TsOptions) -> *mut TsCore {
    unsafe { super::ts_create(opts) }
}
fn ts_free(core: *mut TsCore) {
    unsafe { super::ts_free(core) }
}
fn ts_set_options(core: *mut TsCore, opts: *const TsOptions) -> i32 {
    unsafe { super::ts_set_options(core, opts) }
}
fn ts_sample(core: *mut TsCore, mono: f64) -> i32 {
    unsafe { super::ts_sample(core, mono) }
}
fn ts_draw(core: *mut TsCore, w: u32, h: u32, mono: f64, out: *mut TsFrame) -> i32 {
    unsafe { super::ts_draw(core, w, h, mono, 3, out) }
}
fn ts_draw_regions(core: *mut TsCore, w: u32, h: u32, mono: f64, regions: u32, out: *mut TsFrame) -> i32 {
    unsafe { super::ts_draw(core, w, h, mono, regions, out) }
}
fn ts_feed_audio(core: *mut TsCore, mono: &[f32], rate: f64) -> i32 {
    unsafe { super::ts_feed_audio(core, mono.as_ptr(), mono.len(), rate) }
}
fn ts_audio_state(core: *mut TsCore, state: i32) -> i32 {
    unsafe { super::ts_audio_state(core, state) }
}
fn inner_of<'a>(core: *mut TsCore) -> &'a mut Inner {
    unsafe { inner(core) }.unwrap()
}

fn options(theme: &str) -> TsOptions {
    let mut name = [0 as c_char; 32];
    for (slot, byte) in name.iter_mut().zip(theme.bytes()) {
        *slot = byte as c_char;
    }
    TsOptions { views: Views::DASHBOARD, chrome: Chrome::ALL, interval: 0.5, compact: 0, no_border: 0,
                _pad: [0; 6], theme: name }
}

fn empty_frame() -> TsFrame {
    TsFrame { cells: std::ptr::null(), w: 0, h: 0, dirty_rows: std::ptr::null(), dirty_len: 0, theme_bg: 0, theme_text: 0 }
}

/// A core sampled twice, so every rate has its baseline.
fn primed() -> *mut TsCore {
    let core = ts_create(&options("default"));
    assert!(!core.is_null());
    assert_eq!(ts_sample(core, 100.0), 0);
    assert_eq!(ts_sample(core, 100.5), 0);
    core
}

fn draw(core: *mut TsCore, w: u32, h: u32) -> TsFrame {
    let mut frame = empty_frame();
    assert_eq!(ts_draw(core, w, h, 101.0, &mut frame), 0);
    frame
}

fn cells(frame: &TsFrame) -> &[TsCell] {
    unsafe { std::slice::from_raw_parts(frame.cells, (frame.w * frame.h) as usize) }
}

fn dirty(frame: &TsFrame) -> &[u32] {
    if frame.dirty_len == 0 {
        return &[];
    }
    unsafe { std::slice::from_raw_parts(frame.dirty_rows, frame.dirty_len as usize) }
}

fn row_text(frame: &TsFrame, y: u32) -> String {
    let w = frame.w as usize;
    cells(frame)[y as usize * w..(y as usize + 1) * w].iter().map(|c| char::from_u32(c.ch).unwrap()).collect()
}

#[test]
fn cell_is_sixteen_bytes() {
    assert_eq!(std::mem::size_of::<TsCell>(), 16);
    assert_eq!(std::mem::align_of::<TsCell>(), 4);
    // The other two structs the header spells out, field by field.
    assert_eq!(std::mem::size_of::<TsOptions>(), 56);
    assert_eq!(std::mem::offset_of!(TsOptions, interval), 8);
    assert_eq!(std::mem::offset_of!(TsOptions, theme), 24);
    assert_eq!(std::mem::offset_of!(TsFrame, dirty_rows), 16);
    assert_eq!(std::mem::offset_of!(TsFrame, theme_text), 32);
}

#[test]
fn round_trip() {
    let core = primed();
    let frame = draw(core, 120, 36);
    assert_eq!((frame.w, frame.h), (120, 36));
    assert_eq!(cells(&frame).len(), 120 * 36);
    assert!(row_text(&frame, 0).contains("TERMSTATS"), "{}", row_text(&frame, 0));
    let theme = &THEMES[0];
    assert_eq!((frame.theme_bg, frame.theme_text), (theme.bg, theme.text));
    // The first frame is all new.
    assert_eq!(dirty(&frame), (0..36).collect::<Vec<u32>>().as_slice());
    ts_free(core);
}

#[test]
fn unknown_theme_gives_null() {
    assert!(ts_create(&options("no-such-theme")).is_null());
    // A name with no NUL inside the 32 bytes is not a name.
    let mut opts = options("default");
    opts.theme = [b'x' as c_char; 32];
    assert!(ts_create(&opts).is_null());
    // Nor is one that is not UTF-8.
    let mut opts = options("default");
    opts.theme[0] = 0xff_u8 as c_char;
    assert!(ts_create(&opts).is_null());
    // And an interval that is not a positive number is a bad argument too.
    let mut opts = options("default");
    opts.interval = f64::NAN;
    assert!(ts_create(&opts).is_null());
    assert!(ts_create(std::ptr::null()).is_null());
}

#[test]
fn null_core_returns_minus_one() {
    let null = std::ptr::null_mut();
    let mut frame = empty_frame();
    assert_eq!(ts_sample(null, 1.0), -1);
    assert_eq!(ts_draw(null, 120, 36, 1.0, &mut frame), -1);
    assert_eq!(ts_set_options(null, &options("default")), -1);
    ts_free(null); // must not crash
    let core = primed();
    assert_eq!(ts_draw(core, 120, 36, 1.0, std::ptr::null_mut()), -1);
    assert_eq!(ts_set_options(core, std::ptr::null()), -1);
    assert_eq!(ts_set_options(core, &options("nope")), -2);
    assert_eq!(ts_sample(core, f64::NAN), -2);
    ts_free(core);
}

#[test]
fn second_draw_without_sample_has_few_dirty_rows() {
    let core = primed();
    draw(core, 120, 36);
    let frame = draw(core, 120, 36);
    assert!(dirty(&frame).len() <= 2, "dirty rows: {:?}", dirty(&frame));
    ts_free(core);
}

#[test]
fn resize_marks_every_row_dirty() {
    let core = primed();
    draw(core, 120, 36);
    draw(core, 120, 36);
    let frame = draw(core, 100, 30);
    assert_eq!(dirty(&frame), (0..30).collect::<Vec<u32>>().as_slice());
    // The same number of cells in another shape is a new size too.
    draw(core, 120, 36);
    let frame = draw(core, 108, 40);
    assert_eq!(dirty(&frame), (0..40).collect::<Vec<u32>>().as_slice());
    draw(core, 100, 30);
    // Options change: every row again, even at the same size, and the samples are kept.
    draw(core, 100, 30);
    let mut opts = options("nord");
    opts.compact = 1;
    assert_eq!(ts_set_options(core, &opts), 0);
    let frame = draw(core, 100, 30);
    assert_eq!(dirty(&frame), (0..30).collect::<Vec<u32>>().as_slice());
    assert_eq!(frame.theme_bg, THEMES.iter().find(|t| t.name == "nord").unwrap().bg);
    assert!(!row_text(&frame, 0).contains("collecting"));
    ts_free(core);
}

#[test]
fn the_same_options_again_still_redraw_every_row() {
    let core = primed();
    draw(core, 120, 36);
    draw(core, 120, 36);
    assert_eq!(ts_set_options(core, &options("default")), 0);
    let frame = draw(core, 120, 36);
    assert_eq!(dirty(&frame), (0..36).collect::<Vec<u32>>().as_slice());
    ts_free(core);
}

#[test]
fn rows_of_another_shape_are_never_compared() {
    // Blank grids with the same number of cells: row 0 of each is blank, yet the shape differs.
    let (before, now) = (Screen::new(120, 36), Screen::new(108, 40));
    let mut rows = Vec::new();
    now.changed_rows(&before, &mut rows);
    assert_eq!(rows, (0..40).collect::<Vec<u32>>());
    let same = Screen::new(108, 40);
    now.changed_rows(&same, &mut rows);
    assert!(rows.is_empty());
}

#[test]
fn draw_clamps_the_size() {
    let core = primed();
    assert_eq!((draw(core, 1, 1).w, draw(core, 1, 1).h), (40, 8));
    let frame = draw(core, 5000, 5000);
    assert_eq!((frame.w, frame.h), (1000, 500));
    ts_free(core);
}

#[test]
fn a_gap_of_an_hour_gives_no_rate_spike() {
    let core = ts_create(&options("default"));
    let t = 1000.0;
    assert_eq!(ts_sample(core, t), 0);
    assert_eq!(ts_sample(core, t + 1.0), 0);
    // The machine slept for an hour and sent 3.6 MB meanwhile: one KB a second, not 3.6 MB.
    let state = &mut inner_of(core).state;
    let (sent_taken, recv_taken) = state.offset_net_baseline(3_600_000);
    assert!(sent_taken > 0 && recv_taken > 0, "a machine that never used its network");
    assert_eq!(ts_sample(core, t + 3601.0), 0);
    let state = &inner_of(core).state;
    let (sent, recv) = state.net_rates();
    assert!(sent.is_finite() && recv.is_finite());
    // Spread over the hour, plus whatever really went over the wire in these milliseconds.
    for (rate, taken, what) in [(sent, sent_taken, "sent"), (recv, recv_taken, "recv")] {
        let spread = taken as f64 / 3600.0;
        assert!(rate >= spread && rate < spread + 10_000.0, "{what} {rate} B/s for {taken} B in an hour");
    }
    let (total, cores) = state.cpu_percentages();
    assert!((0.0..=100.0).contains(&total), "{total}");
    assert!(cores.iter().all(|c| (0.0..=100.0).contains(c)), "{cores:?}");
    ts_free(core);
}

#[test]
fn version_and_themes() {
    assert_eq!(ts_abi_version(), 2);
    let version = unsafe { CStr::from_ptr(ts_version()) }.to_str().unwrap();
    assert_eq!(version, env!("CARGO_PKG_VERSION"));
    assert_eq!(ts_theme_count() as usize, THEMES.len());
    for (i, theme) in THEMES.iter().enumerate() {
        let name = unsafe { CStr::from_ptr(ts_theme_name(i as u32)) }.to_str().unwrap();
        assert_eq!(name, theme.name);
    }
    assert!(ts_theme_name(THEMES.len() as u32).is_null());
}

/// The header is copied verbatim into the app: its numbers must be this file's numbers.
#[test]
fn header_matches_the_rust_side() {
    let header = include_str!("../../include/termstats_core.h");
    let define = |name: &str| -> u32 {
        let line = header.lines().find(|l| l.split_whitespace().nth(1) == Some(name))
            .unwrap_or_else(|| panic!("{name} not in the header"));
        line.split_whitespace().nth(2).unwrap().trim_end_matches('u').parse()
            .or_else(|_| u32::from_str_radix(line.split_whitespace().nth(2).unwrap().trim_start_matches("0x").trim_end_matches('u'), 16))
            .unwrap()
    };
    assert_eq!(define("TS_ABI"), ts_abi_version());
    assert_eq!(define("TS_COLOR_DEFAULT"), crate::screen::NONE);
    for (name, bit) in [("CPU", Views::CPU), ("MEMORY", Views::MEMORY), ("NETWORK", Views::NETWORK),
                        ("DISK", Views::DISK), ("CHARTS", Views::CHARTS), ("PROCESSES", Views::PROCESSES),
                        ("DASHBOARD", Views::DASHBOARD), ("EQ", Views::EQ), ("BPM", Views::BPM),
                        ("LEVEL", Views::LEVEL), ("AUDIO", Views::AUDIO)] {
        assert_eq!(define(&format!("TS_VIEW_{name}")), bit, "TS_VIEW_{name}");
    }
    for (name, bit) in [("HEADER", Chrome::HEADER), ("CLOCK", Chrome::CLOCK), ("VERSION", Chrome::VERSION),
                        ("FOOTER", Chrome::FOOTER), ("HINT", Chrome::HINT), ("BRAND", Chrome::BRAND),
                        ("ALL", Chrome::ALL)] {
        assert_eq!(define(&format!("TS_CHROME_{name}")), bit, "TS_CHROME_{name}");
    }
    assert_eq!(define("TS_REGION_DASHBOARD"), REGION_DASHBOARD);
    assert_eq!(define("TS_REGION_AUDIO"), REGION_AUDIO);
    for (name, value) in [("RUNNING", 0), ("NO_PERMISSION", 1), ("NO_DEVICE", 2)] {
        assert_eq!(define(&format!("TS_AUDIO_{name}")), value, "TS_AUDIO_{name}");
    }
    assert!(header.contains("int32_t ts_draw(TsCore *core, uint32_t w, uint32_t h, double mono, uint32_t regions, TsFrame *out);"));
    assert!(header.contains("int32_t ts_feed_audio(TsCore *core, const float *mono, size_t n, double sample_rate);"));
    assert!(header.contains("#include <stddef.h>"), "size_t needs stddef.h");
    for function in ["ts_abi_version", "ts_version", "ts_theme_count", "ts_theme_name", "ts_create", "ts_free",
                     "ts_set_options", "ts_sample", "ts_draw", "ts_feed_audio", "ts_audio_state"] {
        assert!(header.contains(&format!("{function}(")), "{function} missing from the header");
    }
}

// --- audio ---------------------------------------------------------------------------------

use crate::audio::{DemoAudio, BLOCK};

fn audio_core(views: u32) -> *mut TsCore {
    let mut opts = options("default");
    opts.views = views;
    let core = ts_create(&opts);
    assert_eq!(ts_sample(core, 100.0), 0);
    assert_eq!(ts_sample(core, 100.5), 0);
    core
}

fn screen_text(frame: &TsFrame) -> String {
    (0..frame.h).map(|y| row_text(frame, y) + "\n").collect()
}

#[test]
fn audio_fed_through_the_ring_reaches_the_screens() {
    let core = audio_core(Views::DASHBOARD | Views::AUDIO);
    let mut demo = DemoAudio::new(126.0, 48000.0);
    let mut frame = empty_frame();
    let mut t = 0.0;
    while demo.now() < 8.0 {
        assert_eq!(ts_feed_audio(core, &demo.read(1600), 48000.0), 0); // not a multiple of the block
        t += 1600.0 / 48000.0;
        assert_eq!(ts_draw_regions(core, 200, 60, 100.0 + t, 3, &mut frame), 0);
    }
    let an = &inner_of(core).audio.analyzer;
    assert_eq!(an.sample_rate(), 48000.0);
    let bpm = an.bpm.unwrap_or(0.0);
    assert!((124.0..=128.0).contains(&bpm), "{bpm}");
    let text = screen_text(&frame);
    for title in ["cpu", "equalizer", "level", "tempo", "processes"] {
        assert!(text.contains(title), "{title}\n{text}");
    }
    ts_free(core);
}

#[test]
fn feeding_from_a_second_thread_while_the_first_draws() {
    struct Shared(*mut TsCore);
    unsafe impl Send for Shared {}
    let core = audio_core(Views::AUDIO);
    let shared = Shared(core);
    let feeder = std::thread::spawn(move || {
        let shared = shared;
        let mut demo = DemoAudio::new(126.0, 44100.0);
        for i in 0..1000 {
            let rate = if i % 250 == 249 { 48000.0 } else { 44100.0 }; // rate changes on the way
            assert_eq!(ts_feed_audio(shared.0, &demo.read(512), rate), 0);
        }
    });
    let mut frame = empty_frame();
    for i in 0..1000 {
        assert_eq!(ts_draw_regions(core, 120, 36, 1.0 + i as f64 * 0.01, 3, &mut frame), 0);
    }
    feeder.join().unwrap();
    assert_eq!(ts_draw(core, 120, 36, 20.0, &mut frame), 0);
    assert!(inner_of(core).audio.analyzer.db.is_finite());
    ts_free(core);
}

#[test]
fn the_ring_keeps_two_seconds_and_a_new_rate_starts_afresh() {
    let core = audio_core(Views::AUDIO);
    assert_eq!(ts_feed_audio(core, &vec![0.25; 44100 * 3], 44100.0), 0);
    let ring = unsafe { &(*core).feed }.lock().unwrap().ring.len();
    assert_eq!(ring, 88200);
    assert_eq!(ts_feed_audio(core, &[0.25; 100], 44100.0), 0);
    assert_eq!(unsafe { &(*core).feed }.lock().unwrap().ring.len(), 88200, "the oldest are dropped");
    let mut frame = empty_frame();
    assert_eq!(ts_draw(core, 120, 36, 5.0, &mut frame), 0);
    assert_eq!(inner_of(core).audio.analyzer.blocks, 88200 / BLOCK as u64);
    assert_eq!(ts_feed_audio(core, &[0.25; 100], 48000.0), 0);
    assert_eq!(unsafe { &(*core).feed }.lock().unwrap().ring.len(), 100, "a new rate drops the old samples");
    assert_eq!(ts_draw(core, 120, 36, 6.0, &mut frame), 0);
    assert_eq!((inner_of(core).audio.analyzer.sample_rate(), inner_of(core).audio.analyzer.blocks), (48000.0, 0));
    ts_free(core);
}

#[test]
fn feed_arguments_are_checked() {
    let core = audio_core(Views::AUDIO);
    for rate in [0.0, -1.0, f64::NAN, f64::INFINITY, 1e9] {
        assert_eq!(ts_feed_audio(core, &[0.0; 4], rate), -2, "{rate}");
    }
    assert_eq!(unsafe { super::ts_feed_audio(core, std::ptr::null(), 4, 44100.0) }, -1);
    assert_eq!(unsafe { super::ts_feed_audio(core, std::ptr::null(), 0, 44100.0) }, 0);
    assert_eq!(unsafe { super::ts_feed_audio(std::ptr::null_mut(), [0.0f32].as_ptr(), 1, 44100.0) }, -1);
    assert_eq!(ts_audio_state(core, 3), -2);
    assert_eq!(ts_audio_state(core, -1), -2);
    assert_eq!(ts_audio_state(std::ptr::null_mut(), 0), -1);
    ts_free(core);
}

#[test]
fn without_microphone_access_each_audio_panel_says_so() {
    let core = audio_core(Views::AUDIO);
    assert_eq!(ts_audio_state(core, 1), 0);
    let frame = draw(core, 120, 36);
    let text = screen_text(&frame);
    assert_eq!(text.matches("microphone access is off").count(), 3, "{text}");
    assert!(!text.contains('▁') && !text.contains('▔'), "no bars\n{text}");
    assert_eq!(ts_audio_state(core, 2), 0);
    let text = screen_text(&draw(core, 120, 36));
    assert_eq!(text.matches("no input device").count(), 3, "{text}");
    assert_eq!(ts_audio_state(core, 0), 0);
    assert!(!screen_text(&draw(core, 120, 36)).contains("no input device"));
    ts_free(core);
}

#[test]
fn a_region_not_asked_for_keeps_its_cells() {
    let core = audio_core(Views::DASHBOARD | Views::AUDIO);
    let mut demo = DemoAudio::new(126.0, 44100.0);
    let mut frame = empty_frame();
    assert_eq!(ts_feed_audio(core, &demo.read(44100 * 2), 44100.0), 0);
    assert_eq!(ts_draw_regions(core, 200, 60, 102.0, 3, &mut frame), 0);
    let audio = compose::layout(&inner_of(core).state, 200, 60).audio.unwrap();
    let before: Vec<TsCell> = cells(&frame).to_vec();
    let split = |cells: &[TsCell], inside: bool| -> Vec<TsCell> {
        cells.iter().enumerate().filter(|(i, _)| audio.contains(i % 200, i / 200) == inside).map(|p| *p.1).collect()
    };
    // more music, a new sample of the machine - then only the audio is drawn
    assert_eq!(ts_feed_audio(core, &demo.read(44100), 44100.0), 0);
    assert_eq!(ts_sample(core, 103.0), 0);
    assert_eq!(ts_draw_regions(core, 200, 60, 103.0, REGION_AUDIO, &mut frame), 0);
    let after = cells(&frame).to_vec();
    assert_eq!(split(&before, false), split(&after, false), "the dashboard kept its cells");
    assert_ne!(split(&before, true), split(&after, true), "the audio moved on");
    // and the other way round
    let before = after;
    assert_eq!(ts_feed_audio(core, &demo.read(44100), 44100.0), 0);
    assert_eq!(ts_draw_regions(core, 200, 60, 104.0, REGION_DASHBOARD, &mut frame), 0);
    assert_eq!(split(&before, true), split(cells(&frame), true), "the audio kept its cells");
    // regions 0 is both; a new size draws both whatever is asked for
    assert_eq!(ts_draw_regions(core, 200, 60, 105.0, 0, &mut frame), 0);
    assert_ne!(split(&before, true), split(cells(&frame), true));
    assert_eq!(ts_draw_regions(core, 190, 60, 105.5, REGION_DASHBOARD, &mut frame), 0);
    assert!(screen_text(&frame).contains("equalizer"), "a resize redraws the audio too");
    ts_free(core);
}

#[test]
fn the_fft_runs_only_while_the_equalizer_is_drawn() {
    for (views, fft) in [(Views::LEVEL | Views::BPM, false), (Views::EQ, true)] {
        let core = audio_core(views);
        let mut demo = DemoAudio::new(126.0, 44100.0);
        assert_eq!(ts_feed_audio(core, &demo.read(44100), 44100.0), 0);
        draw(core, 120, 36);
        let an = &inner_of(core).audio.analyzer;
        assert_eq!(an.spectrum_on, fft);
        assert_eq!(an.bands.iter().any(|&b| b > 0.0), fft, "views {views}");
        assert!(an.bass > 0.0, "the kick band is measured either way");
        ts_free(core);
    }
}
