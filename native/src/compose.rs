//! Where the dashboard and the microphone screens go when both are on.
//!
//! The header and footer rows stay the dashboard's chrome. In between: dashboard views only
//! - the dashboard as before; audio views only - the audio panels take the whole body;
//! both - from AUDIO_COLUMN_FROM columns the audio is a column on the right (40 % of the
//! width, AUDIO_COLUMN_MIN..AUDIO_COLUMN_MAX) and the dashboard lays itself out as if the
//! rest were its terminal; narrower, the audio panels are full-width rows at the bottom of
//! the body, at most half of it, and the dashboard's own budget lets its charts and
//! processes give way first.

use crate::audio_ui::{self, AudioState};
use crate::screen::Screen;
use crate::ui::{Chrome, Rect, State, Views};

pub const AUDIO_COLUMN_FROM: usize = 190;
pub const AUDIO_COLUMN_MIN: usize = 60;
pub const AUDIO_COLUMN_MAX: usize = 120;
pub const AUDIO_COLUMN_SHARE: f64 = 0.4;

/// The regions of a frame (`ts_draw`'s `regions` bits).
pub const REGION_DASHBOARD: u32 = 1;
pub const REGION_AUDIO: u32 = 2;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Layout {
    /// The dashboard's sections (header and footer rows are the dashboard's too).
    pub dashboard: Option<Rect>,
    pub audio: Option<Rect>,
}

pub fn layout(state: &State, w: usize, h: usize) -> Layout {
    let (views, chrome) = (state.opts.views, state.opts.chrome);
    let top = usize::from(chrome.has(Chrome::HEADER));
    let bottom = h.saturating_sub(usize::from(chrome.has(Chrome::FOOTER)));
    let body = Rect { x: 0, y: top, w, h: bottom.saturating_sub(top) };
    let (dash_on, audio_on) = (views.0 & Views::DASHBOARD != 0, views.has(Views::AUDIO));
    if !audio_on {
        return Layout { dashboard: Some(body), audio: None };
    }
    if !dash_on {
        return Layout { dashboard: None, audio: Some(body) };
    }
    if w >= AUDIO_COLUMN_FROM {
        let aw = ((w as f64 * AUDIO_COLUMN_SHARE).round() as usize).clamp(AUDIO_COLUMN_MIN, AUDIO_COLUMN_MAX);
        return Layout { dashboard: Some(Rect { w: w - aw, ..body }), audio: Some(Rect { x: w - aw, w: aw, ..body }) };
    }
    let ah = audio_ui::wanted_rows(state, w).min(body.h / 2);
    if ah == 0 {
        return Layout { dashboard: Some(body), audio: None };
    }
    Layout { dashboard: Some(Rect { h: body.h - ah, ..body }),
             audio: Some(Rect { y: body.y + body.h - ah, h: ah, ..body }) }
}

/// Draw the asked-for regions into `scr` (which the caller has cleared or filled with the
/// previous frame). Returns the layout that was used.
pub fn draw(state: &mut State, audio: &mut AudioState, scr: &mut Screen, now: f64, regions: u32) -> Layout {
    let (w, h) = (scr.w, scr.h);
    let lay = layout(state, w, h);
    if !state.opts.views.has(Views::AUDIO) {
        if regions & REGION_DASHBOARD != 0 {
            state.draw(scr);
        }
        return lay;
    }
    if regions & REGION_DASHBOARD != 0 {
        let chrome = state.opts.chrome;
        if chrome.has(Chrome::HEADER) {
            state.header(scr, w);
        }
        if let Some(d) = lay.dashboard {
            // The dashboard lays out as if its rectangle were the whole terminal.
            let mut part = Screen::new(d.w, d.h);
            state.opts.chrome = Chrome(chrome.0 & !(Chrome::HEADER | Chrome::FOOTER));
            state.draw(&mut part);
            state.opts.chrome = chrome;
            for y in 0..d.h {
                let row = &part.cells[y * d.w..(y + 1) * d.w];
                let at = (d.y + y) * w + d.x;
                scr.cells[at..at + d.w].copy_from_slice(row);
            }
        }
        if chrome.has(Chrome::FOOTER) && h > 0 {
            state.footer(scr, h - 1, w);
        }
    }
    if regions & REGION_AUDIO != 0 {
        if let Some(a) = lay.audio {
            audio_ui::draw(state, audio, scr, a, now);
        }
    }
    lay
}

#[cfg(test)]
mod tests;
