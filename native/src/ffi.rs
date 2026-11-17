//! The C interface: the dashboard as a grid of cells for a program that is not a terminal.
//!
//! `include/termstats_core.h` is the contract and is written by hand; the `#[repr(C)]`
//! types here must agree with it field by field (the tests check sizes, offsets and the
//! numbers the header defines). Every `extern "C"` body runs inside `catch_unwind`: a
//! library built with `panic = "unwind"` turns a panic into -1 or NULL instead of
//! unwinding into C, which is undefined behaviour.
//!
//! One function may be called from another thread: `ts_feed_audio`, from the audio thread
//! of the caller. It only locks the feed ring, copies the samples in and returns; it never
//! touches what the drawing thread owns. Everything else of a core lives in an `UnsafeCell`
//! that only the one drawing thread reaches - so the two never alias.

use crate::audio::Analyzer;
use crate::audio_ui::{AudioState, Mic};
use crate::compose::{self, REGION_AUDIO, REGION_DASHBOARD};
use crate::screen::{Cell, Screen, BLANK};
use crate::themes::{Theme, THEMES};
use crate::ui::{Chrome, Options, State, Views};
use std::cell::UnsafeCell;
use std::collections::VecDeque;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Mutex, OnceLock};

/// Raised whenever a layout or a meaning in the header changes.
const ABI: u32 = 2;

/// Seconds of samples the feed ring holds; on overflow the oldest are dropped.
const FEED_SECONDS: f64 = 2.0;
const RATE_MIN: f64 = 4000.0;
const RATE_MAX: f64 = 768_000.0;

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
/// dirty rows are measured against, plus the ring the audio thread feeds. Opaque to C.
pub struct TsCore {
    feed: Mutex<Feed>,
    inner: UnsafeCell<Inner>,
}

/// What only the drawing thread touches.
pub(crate) struct Inner {
    pub(crate) state: State,
    pub(crate) audio: AudioState,
    screen: Screen,
    /// The last composed frame: the dirty rows are measured against it, and a region not
    /// asked for keeps its cells from it.
    prev: Screen,
    out: Vec<TsCell>,
    dirty: Vec<u32>,
    drained: Vec<f32>,
}

/// Samples handed over by the audio thread, not yet analysed.
struct Feed {
    ring: VecDeque<f32>,
    rate: f64,
    /// The rate changed since the last draw: the analysis starts afresh.
    reset: bool,
}

/// The drawing thread's part of a core. Null-safe.
///
/// # Safety
/// `core` is NULL or a live pointer from `ts_create`; only one thread calls this at a time.
unsafe fn inner<'a>(core: *mut TsCore) -> Option<&'a mut Inner> {
    // SAFETY: a shared reference to the core; `inner` is only reached from the one thread
    // that draws, `feed` is behind its mutex.
    unsafe { core.as_ref().map(|c| &mut *c.inner.get()) }
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
        let inner = Inner { state: State::new(opts), audio: AudioState::new(), screen: Screen::new(0, 0),
                            prev: Screen::new(0, 0), out: Vec::new(), dirty: Vec::new(), drained: Vec::new() };
        let feed = Feed { ring: VecDeque::new(), rate: 0.0, reset: false };
        let core = TsCore { feed: Mutex::new(feed), inner: UnsafeCell::new(inner) };
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
        let (Some(core), Some(opts)) = (unsafe { inner(core) }, unsafe { opts.as_ref() }) else {
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
        let Some(core) = (unsafe { inner(core) }) else {
            return FAILED;
        };
        if !mono.is_finite() {
            return BAD_ARGUMENT;
        }
        core.state.sample(mono);
        OK
    })
}

/// Hand over microphone samples (mono, -1..1) - from ANY thread, the audio thread too. Only
/// copies them into a ring of FEED_SECONDS; they are analysed on the next `ts_draw`. A new
/// sample rate drops what is in the ring and starts the analysis afresh.
///
/// # Safety
/// `core` is NULL or a live pointer from `ts_create`; `mono` is NULL or points to `n`
/// readable floats.
#[no_mangle]
pub unsafe extern "C" fn ts_feed_audio(core: *mut TsCore, mono: *const f32, n: usize, sample_rate: f64) -> i32 {
    guarded(FAILED, || {
        // SAFETY: only the mutex-guarded feed is touched through this shared reference.
        let Some(core) = (unsafe { core.as_ref() }) else {
            return FAILED;
        };
        if !(sample_rate.is_finite() && (RATE_MIN..=RATE_MAX).contains(&sample_rate)) {
            return BAD_ARGUMENT;
        }
        if n == 0 {
            return OK;
        }
        if mono.is_null() {
            return FAILED;
        }
        // SAFETY: the caller passes n readable floats.
        let samples = unsafe { std::slice::from_raw_parts(mono, n) };
        let cap = (sample_rate * FEED_SECONDS) as usize;
        let samples = &samples[samples.len().saturating_sub(cap)..];
        let mut feed = core.feed.lock().unwrap_or_else(|e| e.into_inner());
        if feed.rate != sample_rate {
            feed.ring.clear();
            feed.rate = sample_rate;
            feed.reset = true;
        }
        let overflow = ((feed.ring.len() + samples.len()).saturating_sub(cap)).min(feed.ring.len());
        feed.ring.drain(..overflow);
        feed.ring.extend(samples.iter().copied());
        OK
    })
}

/// Whether there is a microphone to listen to: 0 running, 1 no permission, 2 no device.
/// Drawn as text in the audio panels.
///
/// # Safety
/// `core` as for `ts_sample`.
#[no_mangle]
pub unsafe extern "C" fn ts_audio_state(core: *mut TsCore, state: i32) -> i32 {
    guarded(FAILED, || {
        // SAFETY: as in ts_set_options.
        let Some(core) = (unsafe { inner(core) }) else {
            return FAILED;
        };
        core.audio.mic = match state {
            0 => Mic::Running,
            1 => Mic::NoPermission,
            2 => Mic::NoDevice,
            _ => return BAD_ARGUMENT,
        };
        OK
    })
}

/// # Safety
/// `core` as for `ts_sample`; `out` is NULL or points to a writable `TsFrame`.
#[no_mangle]
pub unsafe extern "C" fn ts_draw(core: *mut TsCore, w: u32, h: u32, mono: f64, regions: u32, out: *mut TsFrame) -> i32 {
    guarded(FAILED, || {
        // The audio thread's samples first: taken under the lock, analysed after it.
        let (rate, reset) = {
            // SAFETY: as in ts_feed_audio.
            let Some(shared) = (unsafe { core.as_ref() }) else {
                return FAILED;
            };
            let mut feed = shared.feed.lock().unwrap_or_else(|e| e.into_inner());
            // SAFETY: as in ts_set_options - the drawing thread's own part.
            let Some(inner) = (unsafe { inner(core) }) else {
                return FAILED;
            };
            inner.drained.clear();
            inner.drained.extend(feed.ring.drain(..));
            (feed.rate, std::mem::replace(&mut feed.reset, false))
        };
        // SAFETY: as in ts_set_options; `out` is NULL or a TsFrame the caller owns.
        let (Some(core), Some(out)) = (unsafe { inner(core) }, unsafe { out.as_mut() }) else {
            return FAILED;
        };
        if !mono.is_finite() {
            return BAD_ARGUMENT;
        }
        let views = core.state.opts.views;
        if reset {
            core.audio.analyzer = Analyzer::with_rate(rate);
            core.audio.motion.reset();
        }
        core.audio.analyzer.spectrum_on = views.has(Views::EQ);
        if !core.drained.is_empty() {
            core.audio.analyzer.feed(&core.drained, rate as f32, mono);
        }

        let (w, h) = (w.clamp(MIN_W, MAX_W), h.clamp(MIN_H, MAX_H));
        let (wu, hu) = (w as usize, h as usize);
        let all = REGION_DASHBOARD | REGION_AUDIO;
        let mut regions = if regions & all == 0 { all } else { regions & all };
        if core.prev.w != wu || core.prev.h != hu {
            regions = all; // a new size or new options: nothing old is worth keeping
        }
        if regions == all {
            core.screen.reset(wu, hu);
        } else {
            // A region not asked for keeps its cells; the asked-for one is drawn afresh.
            let lay = compose::layout(&core.state, wu, hu);
            core.screen.reset(wu, hu);
            core.screen.cells.copy_from_slice(&core.prev.cells);
            let audio = lay.audio;
            for y in 0..hu {
                for x in 0..wu {
                    let in_audio = audio.is_some_and(|a| a.contains(x, y));
                    if in_audio == (regions == REGION_AUDIO) {
                        core.screen.cells[y * wu + x] = BLANK;
                    }
                }
            }
        }
        compose::draw(&mut core.state, &mut core.audio, &mut core.screen, mono, regions);
        core.screen.changed_rows(&core.prev, &mut core.dirty);

        let width = wu;
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
