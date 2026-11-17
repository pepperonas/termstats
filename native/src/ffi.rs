//! The C interface: the dashboard as a grid of cells for a program that is not a terminal.
//!
//! `include/termstats_core.h` is the contract and is written by hand; the `#[repr(C)]`
//! types here must agree with it field by field (the tests check sizes, offsets and the
//! numbers the header defines). Every `extern "C"` body runs inside `catch_unwind`: a
//! library built with `panic = "unwind"` turns a panic into -1 or NULL instead of
//! unwinding into C, which is undefined behaviour.

use crate::screen::{Cell, Screen, BLANK};
use crate::themes::{Theme, THEMES};
use crate::ui::{Chrome, Options, State, Views};
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::OnceLock;

/// Raised whenever a layout or a meaning in the header changes.
const ABI: u32 = 1;

const OK: i32 = 0;
const FAILED: i32 = -1;
const BAD_ARGUMENT: i32 = -2;

const MIN_W: u32 = 40;
const MAX_W: u32 = 1000;
const MIN_H: u32 = 8;
const MAX_H: u32 = 500;

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TsCell {
    pub ch: u32,
    pub fg: u32,
    pub bg: u32,
    pub bold: u8,
    pub _pad: [u8; 3],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct TsOptions {
    pub views: u32,
    pub chrome: u32,
    pub interval: f64,
    pub compact: u8,
    pub no_border: u8,
    pub _pad: [u8; 6],
    pub theme: [c_char; 32],
}

#[repr(C)]
pub struct TsFrame {
    pub cells: *const TsCell,
    pub w: u32,
    pub h: u32,
    pub dirty_rows: *const u32,
    pub dirty_len: u32,
    pub theme_bg: u32,
    pub theme_text: u32,
}

/// One dashboard: the sampled state, the grid it is drawn into, and the previous grid the
/// dirty rows are measured against. Opaque to C.
pub struct TsCore {
    pub(crate) state: State,
    screen: Screen,
    prev: Screen,
    out: Vec<TsCell>,
    dirty: Vec<u32>,
}

fn to_c(cell: &Cell) -> TsCell {
    TsCell { ch: cell.ch as u32, fg: cell.fg, bg: cell.bg, bold: u8::from(cell.bold), _pad: [0; 3] }
}

/// The Rust options for a C struct, or None when an argument is bad: a theme name without a
/// NUL inside its 32 bytes, not UTF-8 or unknown, or an interval that is not a positive number.
fn options(opts: &TsOptions) -> Option<Options> {
    let bytes: Vec<u8> = opts.theme.iter().map(|&c| c as u8).collect();
    let name = CStr::from_bytes_until_nul(&bytes).ok()?.to_str().ok()?;
    let theme: &'static Theme = THEMES.iter().find(|t| t.name == name)?;
    if !(opts.interval.is_finite() && opts.interval > 0.0) {
        return None;
    }
    Some(Options {
        compact: opts.compact != 0,
        no_border: opts.no_border != 0,
        live: true,
        switch_keys: false,
        views: Views(opts.views),
        chrome: Chrome(opts.chrome),
        ..Options::terminal(opts.interval, theme)
    })
}

/// Run `body`, turning a panic into `fallback`.
fn guarded<T>(fallback: T, body: impl FnOnce() -> T) -> T {
    catch_unwind(AssertUnwindSafe(body)).unwrap_or(fallback)
}

#[no_mangle]
pub extern "C" fn ts_abi_version() -> u32 {
    ABI
}

#[no_mangle]
pub extern "C" fn ts_version() -> *const c_char {
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr().cast()
}

#[no_mangle]
pub extern "C" fn ts_theme_count() -> u32 {
    THEMES.len() as u32
}

/// The theme names as C strings, made once and kept for the life of the program.
fn theme_names() -> &'static [CString] {
    static NAMES: OnceLock<Vec<CString>> = OnceLock::new();
    NAMES.get_or_init(|| THEMES.iter().map(|t| CString::new(t.name).expect("theme name without NUL")).collect())
}

#[no_mangle]
pub extern "C" fn ts_theme_name(index: u32) -> *const c_char {
    guarded(std::ptr::null(), || theme_names().get(index as usize).map_or(std::ptr::null(), |n| n.as_ptr()))
}

/// # Safety
/// `opts` is NULL or points to a readable `TsOptions`.
#[no_mangle]
pub unsafe extern "C" fn ts_create(opts: *const TsOptions) -> *mut TsCore {
    guarded(std::ptr::null_mut(), || {
        // SAFETY: the caller passes NULL or a pointer to a TsOptions it owns.
        let Some(opts) = (unsafe { opts.as_ref() }).and_then(options) else {
            return std::ptr::null_mut();
        };
        let core = TsCore { state: State::new(opts), screen: Screen::new(0, 0), prev: Screen::new(0, 0), out: Vec::new(),
                            dirty: Vec::new() };
        Box::into_raw(Box::new(core))
    })
}

/// # Safety
/// `core` is NULL or a pointer from `ts_create` that has not been freed; it is not used afterwards.
#[no_mangle]
pub unsafe extern "C" fn ts_free(core: *mut TsCore) {
    guarded((), || {
        if !core.is_null() {
            // SAFETY: a non-NULL core came from ts_create and is freed once.
            drop(unsafe { Box::from_raw(core) });
        }
    })
}

/// # Safety
/// `core` is NULL or a live pointer from `ts_create`, not used by another thread meanwhile;
/// `opts` is NULL or points to a readable `TsOptions`.
#[no_mangle]
pub unsafe extern "C" fn ts_set_options(core: *mut TsCore, opts: *const TsOptions) -> i32 {
    guarded(FAILED, || {
        // SAFETY: NULL or a core from ts_create, used from one thread at a time.
        let (Some(core), Some(opts)) = (unsafe { core.as_mut() }, unsafe { opts.as_ref() }) else {
            return FAILED;
        };
        let Some(opts) = options(opts) else {
            return BAD_ARGUMENT;
        };
        // The samples stay; only what is drawn changes - so every row is drawn anew.
        core.state.opts = opts;
        core.prev.reset(0, 0);
        OK
    })
}

/// # Safety
/// `core` is NULL or a live pointer from `ts_create`, not used by another thread meanwhile.
#[no_mangle]
pub unsafe extern "C" fn ts_sample(core: *mut TsCore, mono: f64) -> i32 {
    guarded(FAILED, || {
        // SAFETY: as in ts_set_options.
        let Some(core) = (unsafe { core.as_mut() }) else {
            return FAILED;
        };
        if !mono.is_finite() {
            return BAD_ARGUMENT;
        }
        core.state.sample(mono);
        OK
    })
}

/// # Safety
/// `core` as for `ts_sample`; `out` is NULL or points to a writable `TsFrame`.
#[no_mangle]
pub unsafe extern "C" fn ts_draw(core: *mut TsCore, w: u32, h: u32, mono: f64, out: *mut TsFrame) -> i32 {
    guarded(FAILED, || {
        // SAFETY: as in ts_set_options; `out` is NULL or a TsFrame the caller owns.
        let (Some(core), Some(out)) = (unsafe { core.as_mut() }, unsafe { out.as_mut() }) else {
            return FAILED;
        };
        if !mono.is_finite() {
            return BAD_ARGUMENT;
        }
        let (w, h) = (w.clamp(MIN_W, MAX_W), h.clamp(MIN_H, MAX_H));
        core.screen.reset(w as usize, h as usize);
        core.state.draw(&mut core.screen);
        core.screen.changed_rows(&core.prev, &mut core.dirty);

        let width = w as usize;
        core.out.resize(core.screen.cells.len(), to_c(&BLANK));
        for &y in &core.dirty {
            let span = y as usize * width..(y as usize + 1) * width;
            for (slot, cell) in core.out[span.clone()].iter_mut().zip(&core.screen.cells[span]) {
                *slot = to_c(cell);
            }
        }
        std::mem::swap(&mut core.prev, &mut core.screen);

        let theme = core.state.opts.theme;
        *out = TsFrame { cells: core.out.as_ptr(), w, h, dirty_rows: core.dirty.as_ptr(), dirty_len: core.dirty.len() as u32,
                         theme_bg: theme.bg, theme_text: theme.text };
        OK
    })
}

#[cfg(test)]
mod tests;
