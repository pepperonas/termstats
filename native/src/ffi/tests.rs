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
    unsafe { super::ts_draw(core, w, h, mono, out) }
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
    let state = unsafe { &mut (*core).state };
    let (sent_taken, recv_taken) = state.offset_net_baseline(3_600_000);
    assert!(sent_taken > 0 && recv_taken > 0, "a machine that never used its network");
    assert_eq!(ts_sample(core, t + 3601.0), 0);
    let state = unsafe { &(*core).state };
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
    assert_eq!(ts_abi_version(), 1);
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
                        ("DASHBOARD", Views::DASHBOARD)] {
        assert_eq!(define(&format!("TS_VIEW_{name}")), bit, "TS_VIEW_{name}");
    }
    for (name, bit) in [("HEADER", Chrome::HEADER), ("CLOCK", Chrome::CLOCK), ("VERSION", Chrome::VERSION),
                        ("FOOTER", Chrome::FOOTER), ("HINT", Chrome::HINT), ("BRAND", Chrome::BRAND),
                        ("ALL", Chrome::ALL)] {
        assert_eq!(define(&format!("TS_CHROME_{name}")), bit, "TS_CHROME_{name}");
    }
    for function in ["ts_abi_version", "ts_version", "ts_theme_count", "ts_theme_name", "ts_create", "ts_free",
                     "ts_set_options", "ts_sample", "ts_draw"] {
        assert!(header.contains(&format!("{function}(")), "{function} missing from the header");
    }
}
