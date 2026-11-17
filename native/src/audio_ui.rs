//! The microphone screens in the core: equalizer, level and tempo - a port of the audio
//! bodies in termstats/cli.py (`eq_body`, `db_body`, `bpm_body`, the blocks they share and
//! the side-by-side rule of `mix_body`), drawn into the same cell grid as the dashboard.
//!
//! Every enabled audio view is its own titled panel in the dashboard's panel style. The
//! equalizer sits on top and stretches; level and tempo sit under it, side by side when the
//! width holds two columns of MIX_MIN_COLUMN_W (the `mix_body` rule), stacked otherwise.
//! A live display: the motion layer is always on, time comes only from `now`.

use crate::audio::{spl, Analyzer, BANDS, DB_FLOOR};
use crate::audio::bpm::{BPM_MAX, BPM_MIN};
use crate::motion::Motion;
use crate::screen::{Screen, NONE};
use crate::themes::{BIG_DIGIT_GAP, BIG_DIGIT_MIN_ROWS, BIG_DIGIT_ROWS, BIG_DIGIT_SCALE, BIG_FONT, METRO_W};
use crate::ui::{key, Meter, Rect, State, Views, AXIS_W_PCT};
use std::collections::HashMap;

pub const MIX_GAP: usize = 4;
pub const MIX_MIN_COLUMN_W: usize = 40;
pub const BEAT_DOT_ON: f64 = 0.15;
pub const AUDIO_READOUT_S: f64 = 0.2;
pub const EQ_BAR_W: usize = 2;
pub const EQ_GAP: usize = 1;
pub const EQ_MIN_COLUMNS: usize = 4;
pub const EQ_LABELS: [(f64, &str); 5] = [(40.0, "40"), (100.0, "100"), (1000.0, "1k"), (10000.0, "10k"), (16000.0, "16k")];
pub const AUDIO_CHART_MIN_H: usize = 6;
/// The rows of an equalizer body the core asks for before it stretches, and the fewest it
/// draws at all (hud, gap, three bar rows, the frequency axis).
pub const EQ_WANT_ROWS: usize = 14;
pub const EQ_MIN_ROWS: usize = 6;

const BAR_FULL: char = '█';
const BAR_EMPTY: char = '╌';
const BAR_SECONDARY: char = '▒';
const SPARK: [char; 8] = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
const VPEAK: char = '▔';
const BEAT_ON: char = '●';
const BEAT_OFF: char = '○';
const METRO_HEAD: char = '◆';
const SEP: &str = "·";

const K_AUDIO: u64 = 9;
const A_DB_READOUT: u64 = 1;
const A_BPM_READOUT: u64 = 2;
const A_DB_PEAK: u64 = 3;
const A_CONF: u64 = 4;
const A_BASS: u64 = 5;
const A_BASS_PEAK: u64 = 6;

/// Whether the microphone is there to be listened to (`ts_audio_state`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mic {
    Running,
    NoPermission,
    NoDevice,
}

/// Everything the audio screens keep between frames.
pub struct AudioState {
    pub analyzer: Analyzer,
    pub motion: Motion,
    pub mic: Mic,
    readouts: HashMap<u64, (f64, f64)>,
}

impl Default for AudioState {
    fn default() -> Self {
        Self::new()
    }
}

impl AudioState {
    pub fn new() -> Self {
        AudioState { analyzer: Analyzer::new(), motion: Motion::new(), mic: Mic::Running, readouts: HashMap::new() }
    }

    /// The value the big font shows: refreshed on its own clock, held in between.
    fn readout(&mut self, k: u64, value: f64, now: f64) -> f64 {
        match self.readouts.get(&k) {
            Some(&(shown, taken)) if now - taken < AUDIO_READOUT_S && now >= taken => shown,
            _ => {
                self.readouts.insert(k, (value, now));
                value
            }
        }
    }
}

/// What a screen draws from in one frame: the motion layer's quantities.
pub struct View {
    pub levels: Vec<f64>,
    pub trails: Vec<f64>,
    pub peaks: Vec<f64>,
    pub beat_env: f64,
    pub meter: f64,
    pub phase: Option<f64>,
}

/// ONE motion step per frame, shared by every panel drawn in it.
pub fn view(audio: &mut AudioState, now: f64) -> View {
    audio.motion.step(&audio.analyzer, now);
    let m = &audio.motion;
    View { levels: m.shown_levels(), trails: m.trails.clone(), peaks: m.peaks.clone(), beat_env: m.beat_env,
           meter: m.meter, phase: m.phase }
}

// ---------------------------------------------------------------------------------
// Pure helpers, as in cli.py
// ---------------------------------------------------------------------------------

/// How many bars fit (two cells each plus a gap, inside the panel chrome `cw`) - never more
/// than the bands; below that the bands fold so the whole range stays on screen.
pub fn eq_columns(width: usize, bands: usize, cw: usize) -> usize {
    let inner = width.saturating_sub(cw);
    let n = (inner + EQ_GAP) / (EQ_BAR_W + EQ_GAP);
    n.min(bands).max(EQ_MIN_COLUMNS)
}

/// Fold `values` into n groups, each the maximum of its members.
pub fn group_bands(values: &[f64], n: usize) -> Vec<f64> {
    let k = values.len();
    if n >= k {
        return values.to_vec();
    }
    (0..n)
        .map(|g| {
            let lo = g * k / n;
            let hi = ((g + 1) * k / n).max(lo + 1);
            values[lo..hi].iter().cloned().fold(f64::MIN, f64::max)
        })
        .collect()
}

/// A 0..1 level as (full cells, eighths of the next cell) over `rows` cells.
pub fn eighths(level: f64, rows: usize) -> (usize, usize) {
    let cells = level.clamp(0.0, 1.0) * rows as f64;
    let mut full = cells as usize;
    let mut part = ((cells - full as f64) * 8.0).round_ties_even() as usize;
    if part == 8 {
        full += 1;
        part = 0;
    }
    (full.min(rows), part)
}

pub fn db_pct(db: f64) -> f64 {
    ((db - DB_FLOOR) / -DB_FLOOR * 100.0).clamp(0.0, 100.0)
}

/// `text` in the five-row font, each row a string of the bar glyph and spaces.
pub fn big_digits(text: &str) -> Vec<String> {
    let glyph = |ch: char| {
        BIG_FONT.iter().find(|g| g.0 == ch).or_else(|| BIG_FONT.iter().find(|g| g.0 == '?')).map(|g| g.1).unwrap()
    };
    let glyphs: Vec<[&str; BIG_DIGIT_ROWS]> = text.chars().map(glyph).collect();
    let gap = " ".repeat(BIG_DIGIT_GAP);
    (0..BIG_DIGIT_ROWS)
        .map(|r| {
            let row = glyphs.iter().map(|g| g[r]).collect::<Vec<_>>().join(&gap);
            row.chars()
                .map(|c| match c {
                    '#' => BAR_FULL.to_string().repeat(BIG_DIGIT_SCALE),
                    '.' => " ".repeat(BIG_DIGIT_SCALE),
                    other => other.to_string(),
                })
                .collect()
        })
        .collect()
}

/// Which band a frequency belongs to, clamped at both ends (see `_band_of` in cli.py).
pub fn band_of(edges: &[f64], freq: f64, bands: usize) -> usize {
    if edges.is_empty() || freq <= edges[0] {
        return 0;
    }
    (0..bands).find(|&i| edges[i] <= freq && freq < edges[i + 1]).unwrap_or(bands - 1)
}

/// The column width of the side-by-side layout, or None when the two must stack.
pub fn mix_columns(width: usize) -> Option<usize> {
    let col = width.saturating_sub(MIX_GAP) / 2;
    (col >= MIX_MIN_COLUMN_W).then_some(col)
}

/// Bucket a (t, value) history over the last `window` seconds into `points` means.
pub fn history_series(history: &[(f64, f64)], now: f64, window: f64, points: usize) -> Option<(Vec<f64>, f64)> {
    let recent: Vec<(f64, f64)> = history.iter().cloned().filter(|&(t, _)| now - t <= window).collect();
    if recent.len() < 2 || points < 2 {
        return None;
    }
    let t0 = recent[0].0;
    let span = (recent[recent.len() - 1].0 - t0).max(1e-9);
    let mut sums = vec![(0.0, 0usize); points];
    for &(t, v) in &recent {
        let b = (((t - t0) / span * points as f64) as usize).min(points - 1);
        sums[b].0 += v;
        sums[b].1 += 1;
    }
    let mut last = recent[0].1;
    let out = sums.iter().map(|&(s, n)| { if n > 0 { last = s / n as f64; } last }).collect();
    Some((out, span))
}

// ---------------------------------------------------------------------------------
// Rows: a body is built as a list of rows first, so blocks can be padded against each
// other (the side-by-side layout) and cut to the rows there are, then painted.
// ---------------------------------------------------------------------------------

type Span = (String, u32, bool);

enum Row {
    Blank,
    Text(Vec<Span>),
    Centred(String, u32, bool),
    Hud,
    Meter { label: &'static str, pct: f64, value: String, label_w: usize, value_w: usize, unit_w: usize, fill: f64,
            peak: f64 },
    Metronome,
    /// A history chart: its label row and `height` rows (plot and time axis).
    Chart { title: &'static str, values: Vec<f64>, span: f64, ylim: (f64, f64), fill: bool, height: usize },
}

impl Row {
    fn height(&self) -> usize {
        match self {
            Row::Chart { height, .. } => 1 + height,
            _ => 1,
        }
    }
}

struct Ctx<'a> {
    state: &'a mut State,
    audio: &'a mut AudioState,
    view: &'a View,
    now: f64,
}

impl Ctx<'_> {
    fn th(&self) -> &'static crate::themes::Theme {
        self.state.opts.theme
    }

    fn beat_dot(&self) -> (char, u32, bool) {
        let env = self.view.beat_env;
        if env > 0.6 {
            (BEAT_ON, self.state.ramp(1.0), true)
        } else if env > BEAT_DOT_ON {
            (BEAT_ON, self.state.ramp(0.8), false)
        } else {
            (BEAT_OFF, self.th().label, false)
        }
    }

    fn tempo_tone(&self) -> u32 {
        self.state.ramp(0.75 + 0.25 * self.view.beat_env)
    }

    fn big_number(&self, text: &str, tone: u32) -> Vec<Row> {
        big_digits(text).into_iter().map(|r| Row::Centred(r, tone, true)).collect()
    }

    /// The level's rows under the HUD: number (five rows when `big`), meter, extremes.
    fn level_block(&mut self, big: bool) -> Vec<Row> {
        let th = self.th();
        let an = &self.audio.analyzer;
        let (db, db_smooth, db_min, db_max, beats) = (an.db, an.db_smooth, an.db_min, an.db_max, an.beats);
        let pct = db_pct(db);
        let mut rows = Vec::new();
        if big {
            let eased = self.state.shown(key(K_AUDIO, A_DB_READOUT), db);
            let eased = self.audio.readout(A_DB_READOUT, eased, self.now);
            rows.extend(self.big_number(&format!("{:.1}", spl(eased)), self.state.ramp(db_pct(eased) / 100.0)));
            rows.push(Row::Centred(format!("dB   {SEP}   smoothed {:.1}", spl(db_smooth)), th.muted, false));
            rows.push(Row::Blank);
        } else {
            rows.push(Row::Text(vec![(format!("{:.1}", spl(db)), self.state.ramp(pct / 100.0), true),
                                     (" dB".into(), th.label, false),
                                     (format!("   {SEP}   smoothed {:.1}", spl(db_smooth)), th.muted, false)]));
        }
        let peak = self.state.peak(key(K_AUDIO, A_DB_PEAK), pct);
        rows.push(Row::Meter { label: "level", pct, value: format!("{:6.1}dB", spl(db)), label_w: 9, value_w: 8, unit_w: 2,
                               fill: self.view.meter, peak });
        // Before the first block there are no extremes (Python would print min 100.0 max 20.0,
        // its starting values - a deliberate difference).
        let (lo, hi) = if self.audio.analyzer.blocks == 0 { (" --".to_string(), " --".to_string()) }
                       else { (format!("{:.1}", spl(db_min)), format!("{:.1}", spl(db_max))) };
        rows.push(Row::Text(vec![("min ".into(), th.label, false), (lo, th.soft, false),
                                 ("   max ".into(), th.label, false), (hi, th.soft, false),
                                 (format!("   {SEP}   beats "), th.label, false), (format!("{beats}"), th.soft, false)]));
        rows
    }

    /// The tempo's rows under the HUD: number (flaring on the beat), metronome, meters.
    fn tempo_block(&mut self, big: bool) -> Vec<Row> {
        let th = self.th();
        let an = &self.audio.analyzer;
        let (bpm, conf, bass, beats, music, rate) = (an.bpm, an.confidence, an.bass, an.beats, an.music, an.onset_rate());
        let mut detail = format!("BPM   {SEP}   {rate:.1} beats/s   {SEP}   {beats} beats");
        if !music {
            detail += &format!("   {SEP}   waiting for music");
        }
        let mut rows = Vec::new();
        if big {
            let shown = match bpm {
                None => {
                    self.state.forget(key(K_AUDIO, A_BPM_READOUT));
                    0.0
                }
                Some(b) => self.state.shown(key(K_AUDIO, A_BPM_READOUT), b as f64),
            };
            let eased = self.audio.readout(A_BPM_READOUT, shown, self.now);
            let text = if eased != 0.0 { format!("{eased:.0}") } else { "---".to_string() };
            rows.extend(self.big_number(&text, self.tempo_tone()));
            rows.push(Row::Centred(detail, th.muted, false));
            if self.view.phase.is_some() && bpm.is_some() {
                rows.push(Row::Metronome);
            }
            rows.push(Row::Blank);
        } else {
            let lit = self.view.beat_env > BEAT_DOT_ON;
            let dot = if lit { (BEAT_ON.to_string(), self.state.ramp(1.0), true) } else { (BEAT_OFF.to_string(), th.label, false) };
            let number = match bpm {
                Some(b) => (format!("{b:.0}"), self.state.ramp(0.75), true),
                None => ("---".to_string(), th.muted, false),
            };
            rows.push(Row::Text(vec![dot, ("  ".into(), NONE, false), number, (" ".into(), th.label, false),
                                     (detail, th.muted, false)]));
        }
        let fill = self.state.shown(key(K_AUDIO, A_CONF), conf * 100.0);
        rows.push(Row::Meter { label: "confidence", pct: conf * 100.0, value: format!("{:5.0}%", conf * 100.0), label_w: 11,
                               value_w: 7, unit_w: 1, fill, peak: f64::NAN });
        let fill = self.state.shown(key(K_AUDIO, A_BASS), bass * 100.0);
        let peak = self.state.peak(key(K_AUDIO, A_BASS_PEAK), bass * 100.0);
        rows.push(Row::Meter { label: "kick band", pct: bass * 100.0, value: format!("{:5.0}%", bass * 100.0), label_w: 11,
                               value_w: 7, unit_w: 1, fill, peak });
        rows
    }

    fn chart(&self, title: &'static str, history: &[(f64, f64)], ylim: (f64, f64), fill: bool, width: usize,
             height: usize) -> Option<Row> {
        let window = history.first().map_or(0.0, |h| (self.now - h.0).clamp(1.0, 60.0));
        let (values, span) = history_series(history, self.now, window, (width.saturating_sub(AXIS_W_PCT + 2)).max(2))?;
        Some(Row::Chart { title, values, span, ylim, fill, height })
    }

    fn level_chart(&self, width: usize, height: usize) -> Option<Row> {
        // the history on the scale the chart is labelled in - the shift the numbers get
        let history: Vec<(f64, f64)> = self.audio.analyzer.db_history.iter().map(|&(t, v)| (t, spl(v))).collect();
        self.chart("level", &history, (spl(DB_FLOOR), spl(0.0)), true, width, height)
    }

    fn tempo_chart(&self, width: usize, height: usize) -> Option<Row> {
        let history: Vec<(f64, f64)> = self.audio.analyzer.bpm_history.iter().cloned().filter(|p| p.1 > 0.0).collect();
        if history.len() < 2 {
            return None;
        }
        self.chart("tempo", &history, (BPM_MIN, BPM_MAX), false, width, height)
    }
}

// ---------------------------------------------------------------------------------
// Painting
// ---------------------------------------------------------------------------------

fn paint_rows(ctx: &mut Ctx, scr: &mut Screen, r: Rect, rows: &[Row]) {
    let th = ctx.th();
    let limit = r.x + r.w;
    let mut y = r.y;
    let end = r.y + r.h;
    for row in rows {
        if y + row.height() > end {
            break;
        }
        match row {
            Row::Blank => {}
            Row::Text(spans) => {
                let mut col = r.x;
                for (text, fg, bold) in spans {
                    col = scr.put(col, y, limit, text, *fg, *bold);
                }
            }
            Row::Centred(text, fg, bold) => {
                let pad = r.w.saturating_sub(text.chars().count()) / 2;
                scr.put(r.x + pad, y, limit, text, *fg, *bold);
            }
            Row::Hud => paint_hud(ctx, scr, r.x, y, r.w),
            Row::Meter { label, pct, value, label_w, value_w, unit_w, fill, peak } => {
                let m = Meter { value: Some(value.clone()), label_w: *label_w, value_w: *value_w, unit_w: *unit_w,
                                ..Meter::new(label, *pct, *fill, *peak) };
                ctx.state.meter(scr, r.x, y, r.w, m);
            }
            Row::Metronome => {
                let mw = METRO_W.min(r.w.saturating_sub(4)).max(8);
                let head = ((ctx.view.phase.unwrap_or(0.0) * mw as f64) as usize).min(mw - 1);
                let pad = r.w.saturating_sub(mw) / 2;
                let mut col = r.x + pad;
                for i in 0..mw {
                    if col >= limit {
                        break;
                    }
                    if i == head {
                        scr.set(col, y, METRO_HEAD, ctx.tempo_tone(), true);
                    } else {
                        scr.set(col, y, BAR_EMPTY, th.track, false);
                    }
                    col += 1;
                }
            }
            Row::Chart { title, values, span, ylim, fill, height } => {
                let label = format!("{title} {SEP} last {}s", span.round_ties_even() as i64);
                scr.put(r.x, y, limit, &label, th.muted, false);
                let colour = ctx.state.ramp(0.5);
                let area = Rect { x: r.x, y: y + 1, w: r.w, h: *height };
                ctx.state.chart_span(scr, area, &[(values.clone(), colour, *fill)], Some(*ylim), AXIS_W_PCT, *span);
            }
        }
        y += row.height();
    }
}

/// One line every microphone screen shares: beat dot, BPM, level, confidence.
fn paint_hud(ctx: &mut Ctx, scr: &mut Screen, x: usize, y: usize, w: usize) {
    let th = ctx.th();
    let limit = x + w;
    let (dot, tone, bold) = ctx.beat_dot();
    let an = &ctx.audio.analyzer;
    let mut col = scr.put(x, y, limit, &dot.to_string(), tone, bold);
    col = scr.put(col, y, limit, "  BPM ", th.label, false);
    col = match an.bpm {
        Some(b) => scr.put(col, y, limit, &format!("{:>3}", b as u32), ctx.state.ramp(0.75), true),
        None => scr.put(col, y, limit, "---", th.muted, false),
    };
    col = scr.put(col, y, limit, &format!("  {SEP}  "), th.label, false);
    col = scr.put(col, y, limit, &format!("{:6.1}", spl(an.db)), ctx.state.ramp(db_pct(an.db) / 100.0), true);
    col = scr.put(col, y, limit, " dB", th.label, false);
    col = scr.put(col, y, limit, &format!("  {SEP}  conf "), th.label, false);
    let (conf, music) = (an.confidence, an.music);
    ctx.state.bar(scr, col, y, 8.min(limit.saturating_sub(col)), conf * 100.0, 0.0, None);
    col += 8;
    if !music {
        scr.put(col, y, limit, "  quiet", th.muted, false);
    }
}

/// The analyser: HUD, a gap, the bars, the frequency axis - exactly `r.h` rows.
fn paint_eq(ctx: &mut Ctx, scr: &mut Screen, r: Rect) {
    if r.h == 0 || r.w == 0 {
        return;
    }
    let th = ctx.th();
    let (_, cw) = ctx.state.chrome();
    let n = eq_columns(r.w, BANDS, cw);
    let levels = group_bands(&ctx.view.levels, n);
    let peaks = group_bands(&ctx.view.peaks, n);
    let trails = group_bands(&ctx.view.trails, n);
    let plot_h = r.h.saturating_sub(3).max(1);
    let span = n * EQ_BAR_W + (n - 1) * EQ_GAP;
    let left = r.w.saturating_sub(span) / 2;
    let limit = r.x + r.w;
    let end = r.y + r.h;
    paint_hud(ctx, scr, r.x, r.y, r.w);
    for row in 0..plot_h {
        let y = r.y + 2 + row;
        if y >= end {
            return;
        }
        let h = plot_h - row; // 1 = the bottom row
        let tone = ctx.state.ramp((h as f64 - 0.5) / plot_h as f64);
        for c in 0..n {
            let (full, part) = eighths(levels.get(c).copied().unwrap_or(0.0), plot_h);
            let peak_cell = (peaks.get(c).copied().unwrap_or(0.0).clamp(0.0, 1.0) * plot_h as f64).ceil() as usize;
            let trail_cell = (trails.get(c).copied().unwrap_or(0.0).clamp(0.0, 1.0) * plot_h as f64).ceil() as usize;
            let (glyph, fg) = if full >= h {
                (BAR_FULL, tone)
            } else if full + 1 == h && part > 0 {
                (SPARK[part - 1], tone)
            } else if peak_cell == h {
                (VPEAK, tone) // only reached above the bar
            } else if h <= trail_cell && !ctx.view.trails.is_empty() {
                (BAR_SECONDARY, th.label) // the afterglow of where the bar was
            } else {
                continue;
            };
            let x = r.x + left + c * (EQ_BAR_W + EQ_GAP);
            for dx in 0..EQ_BAR_W {
                if x + dx < limit {
                    scr.set(x + dx, y, glyph, fg, false);
                }
            }
        }
    }
    // the frequency axis under the bars: a few round numbers at the column they fall in
    let y = r.y + 2 + plot_h;
    if y >= end {
        return;
    }
    let edges = ctx.audio.analyzer.spectrum.edges.clone();
    let mut line = vec![' '; r.w.max(1)];
    for (freq, label) in EQ_LABELS {
        let k = band_of(&edges, freq, BANDS);
        let col = ((k * n) / BANDS).min(n - 1);
        let x = left + col * (EQ_BAR_W + EQ_GAP);
        let len = label.len();
        if x + len > r.w || line[x.saturating_sub(1)..(x + len + 1).min(line.len())].iter().any(|&c| c != ' ') {
            continue;
        }
        for (i, ch) in label.chars().enumerate() {
            line[x + i] = ch;
        }
    }
    let text: String = line.into_iter().collect();
    scr.put(r.x, y, limit, text.trim_end(), th.muted, false);
}

// ---------------------------------------------------------------------------------
// Bodies and panels
// ---------------------------------------------------------------------------------

/// The rows a body starts with: the status line and a gap, or nothing (see `hud_owner`).
fn hud_rows(hud: bool) -> Vec<Row> {
    if hud { vec![Row::Hud, Row::Blank] } else { Vec::new() }
}

/// Big digits when the body holds them: BIG_DIGIT_MIN_ROWS counts the status line and its
/// gap, which a body without the status line does not spend.
fn big_fits(rows: usize, hud: bool) -> bool {
    rows + if hud { 0 } else { 2 } >= BIG_DIGIT_MIN_ROWS
}

fn db_rows(ctx: &mut Ctx, width: usize, rows: usize, hud: bool) -> Vec<Row> {
    let mut out = hud_rows(hud);
    out.extend(ctx.level_block(big_fits(rows, hud)));
    let used: usize = out.iter().map(Row::height).sum();
    let remaining = rows as i64 - used as i64 - 2;
    if remaining >= AUDIO_CHART_MIN_H as i64 {
        if let Some(chart) = ctx.level_chart(width, remaining as usize) {
            out.push(Row::Blank);
            out.push(chart);
        }
    }
    out
}

fn bpm_rows(ctx: &mut Ctx, width: usize, rows: usize, hud: bool) -> Vec<Row> {
    let mut out = hud_rows(hud);
    out.extend(ctx.tempo_block(big_fits(rows, hud)));
    let used: usize = out.iter().map(Row::height).sum();
    let remaining = rows as i64 - used as i64 - 2;
    if remaining >= AUDIO_CHART_MIN_H as i64 {
        if let Some(chart) = ctx.tempo_chart(width, remaining as usize) {
            out.push(Row::Blank);
            out.push(chart);
        }
    }
    out
}

/// Level and tempo side by side (the `mix_body` rule): no HUD - everything it carried is
/// on screen at full size - and the heads padded to the same height BEFORE the meters, so
/// the level meter sits beside the confidence meter and the extremes beside the kick band.
fn mix_rows(ctx: &mut Ctx, col_w: usize, rows: usize) -> (Vec<Row>, Vec<Row>) {
    let big = rows >= side_by_side_rows(true);
    let mut left = ctx.level_block(big);
    let mut right = ctx.tempo_block(big);
    let head = left.len().max(right.len()) - 2;
    for block in [&mut left, &mut right] {
        let at = block.len() - 2;
        for _ in 0..head - at {
            block.insert(at, Row::Blank);
        }
    }
    let remaining = rows as i64 - left.len() as i64 - 2;
    if remaining >= AUDIO_CHART_MIN_H as i64 {
        let lc = ctx.level_chart(col_w, remaining as usize);
        let tc = ctx.tempo_chart(col_w, remaining as usize);
        if lc.is_some() || tc.is_some() {
            left.push(Row::Blank);
            right.push(Row::Blank);
            left.extend(lc);
            right.extend(tc);
        }
    }
    (left, right)
}

/// Rows of the side-by-side bodies without charts: the padded head and two meter rows.
fn side_by_side_rows(big: bool) -> usize {
    if big { BIG_DIGIT_ROWS + 3 + 2 } else { 3 }
}

/// Rows of a stacked level body without a chart ([hud, gap,] block).
fn level_rows(big: bool, hud: bool) -> usize {
    2 * usize::from(hud) + if big { BIG_DIGIT_ROWS + 4 } else { 3 }
}

/// Rows of a stacked tempo body without a chart ([hud, gap,] block with the metronome).
fn tempo_rows(big: bool, hud: bool) -> usize {
    2 * usize::from(hud) + if big { BIG_DIGIT_ROWS + 5 } else { 3 }
}

/// The status line (beat dot, BPM, dB, confidence) is drawn at most ONCE in the region:
/// in the equalizer when it is on; else in the one level or tempo panel; with level and
/// tempo both on and no equalizer, nowhere - each shows its own number, and the tempo
/// panel's title carries the beat dot (as Python's wide mix does).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum HudOwner {
    Eq,
    Lower,
    Nobody,
}

fn hud_owner(views: Views) -> HudOwner {
    match (views.has(Views::EQ), views.has(Views::LEVEL), views.has(Views::BPM)) {
        (true, _, _) => HudOwner::Eq,
        (false, true, true) => HudOwner::Nobody,
        _ => HudOwner::Lower,
    }
}

fn subtitle(ctx: &Ctx, text: &str) -> Vec<(String, u32)> {
    vec![(format!(" {SEP} {text}"), ctx.th().muted)]
}

/// Without a microphone (`ts_audio_state` 1/2): the message, no bars. The level and tempo
/// panels ALWAYS show `---` where their number would be - in big digits when they fit
/// under the message, else on one line (first, when even that does not fit under it).
/// The equalizer panel shows the message only.
fn message(ctx: &mut Ctx, scr: &mut Screen, body: Rect, number: bool) {
    let th = ctx.th();
    let lines: &[(&str, u32)] = match ctx.audio.mic {
        Mic::NoPermission => &[("microphone access is off", th.text), ("System Settings > Privacy > Microphone", th.muted)],
        _ => &[("no input device", th.text)],
    };
    let mut rows: Vec<Row> = lines.iter().map(|&(t, c)| Row::Text(vec![(t.to_string(), c, false)])).collect();
    let dashes = || Row::Text(vec![("---".to_string(), th.muted, true)]);
    if number {
        if body.h >= rows.len() + 1 + BIG_DIGIT_ROWS {
            rows.push(Row::Blank);
            rows.extend(ctx.big_number("---", th.muted));
        } else if body.h > rows.len() {
            rows.push(dashes());
        } else {
            rows.insert(0, dashes());
        }
    }
    paint_rows(ctx, scr, body, &rows);
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    Eq,
    Level,
    Tempo,
}

fn panel(ctx: &mut Ctx, scr: &mut Screen, r: Rect, kind: Kind, side: Option<&[Row]>, hud: bool) {
    let title = match kind {
        Kind::Eq => "equalizer",
        Kind::Level => "level",
        Kind::Tempo => "tempo",
    };
    let mut sub = match kind {
        Kind::Eq => subtitle(ctx, &format!("{BANDS} bands from 40 Hz to 16 kHz")),
        Kind::Level => subtitle(ctx, "dB, uncalibrated estimate"),
        Kind::Tempo => subtitle(ctx, "beats per minute"),
    };
    if kind == Kind::Tempo && hud_owner(ctx.state.opts.views) == HudOwner::Nobody && ctx.audio.mic == Mic::Running {
        // no status line anywhere: the beat dot moves into the tempo title
        let (dot, tone, _) = ctx.beat_dot();
        sub.insert(0, (format!(" {dot}"), tone));
    }
    let body = ctx.state.panel(scr, r, title, &sub);
    if body.w == 0 || body.h == 0 {
        return;
    }
    if ctx.audio.mic != Mic::Running {
        message(ctx, scr, body, kind != Kind::Eq);
        return;
    }
    match (kind, side) {
        (_, Some(rows)) => paint_rows(ctx, scr, body, rows),
        (Kind::Eq, None) => paint_eq(ctx, scr, body),
        (Kind::Level, None) => {
            let rows = db_rows(ctx, body.w, body.h, hud);
            paint_rows(ctx, scr, body, &rows)
        }
        (Kind::Tempo, None) => {
            let rows = bpm_rows(ctx, body.w, body.h, hud);
            paint_rows(ctx, scr, body, &rows)
        }
    }
}

/// The rows the lower part (level and tempo) asks for, frame included; None without either.
fn lower_want(views: Views, width: usize, ch: usize, cw: usize, big: bool) -> usize {
    let (level, tempo) = (views.has(Views::LEVEL), views.has(Views::BPM));
    let hud = hud_owner(views) == HudOwner::Lower;
    match (level, tempo) {
        (true, true) if mix_columns(width.saturating_sub(cw)).is_some() => ch + side_by_side_rows(big),
        (true, true) => 2 * ch + level_rows(big, hud) + tempo_rows(big, hud),
        (true, false) => ch + level_rows(big, hud),
        (false, true) => ch + tempo_rows(big, hud),
        _ => 0,
    }
}

/// The rows the audio region would like at this width, before anything stretches.
pub fn wanted_rows(state: &State, width: usize) -> usize {
    let (ch, cw) = state.chrome();
    let views = state.opts.views;
    let eq = if views.has(Views::EQ) { ch + EQ_WANT_ROWS } else { 0 };
    eq + lower_want(views, width, ch, cw, true)
}

/// Draw the enabled audio views into `rect`. The analyzer must already hold this frame's
/// samples; the motion layer takes its one step here.
pub fn draw(state: &mut State, audio: &mut AudioState, scr: &mut Screen, rect: Rect, now: f64) {
    let views = state.opts.views;
    if !views.has(Views::AUDIO) || rect.w < 4 || rect.h < 2 {
        return;
    }
    let v = view(audio, now);
    let mut ctx = Ctx { state, audio, view: &v, now };
    let (ch, cw) = ctx.state.chrome();
    let eq_on = views.has(Views::EQ);
    let (level_on, tempo_on) = (views.has(Views::LEVEL), views.has(Views::BPM));
    let lower_on = level_on || tempo_on;

    // How the height is shared: the lower part takes what it asks for (big digits if the
    // equalizer keeps EQ_MIN_ROWS, else one-line numbers), the equalizer stretches.
    let lower_h = if !lower_on {
        0
    } else if !eq_on {
        rect.h
    } else {
        let eq_min = ch + EQ_MIN_ROWS;
        let big = lower_want(views, rect.w, ch, cw, true);
        let small = lower_want(views, rect.w, ch, cw, false);
        if rect.h >= big + eq_min { big } else { small.min(rect.h.saturating_sub(ch + 3)) }
    };
    let eq_h = if eq_on { rect.h - lower_h } else { 0 };
    if eq_on && eq_h > 0 {
        panel(&mut ctx, scr, Rect { h: eq_h, ..rect }, Kind::Eq, None, true);
    }
    if lower_h == 0 {
        return;
    }
    let lower = Rect { y: rect.y + eq_h, h: lower_h, ..rect };
    let hud = hud_owner(views) == HudOwner::Lower;
    match (level_on, tempo_on) {
        (true, true) => {
            if let Some(_col) = mix_columns(lower.w.saturating_sub(cw)) {
                let left_w = lower.w / 2;
                let rows = lower.h.saturating_sub(ch);
                let body_w = left_w.saturating_sub(cw);
                let (l, r) = if ctx.audio.mic == Mic::Running { mix_rows(&mut ctx, body_w, rows) } else { (vec![], vec![]) };
                panel(&mut ctx, scr, Rect { w: left_w, ..lower }, Kind::Level, Some(&l), false);
                panel(&mut ctx, scr, Rect { x: lower.x + left_w, w: lower.w - left_w, ..lower }, Kind::Tempo, Some(&r), false);
            } else {
                let top = lower.h / 2;
                panel(&mut ctx, scr, Rect { h: top, ..lower }, Kind::Level, None, hud);
                panel(&mut ctx, scr, Rect { y: lower.y + top, h: lower.h - top, ..lower }, Kind::Tempo, None, hud);
            }
        }
        (true, false) => panel(&mut ctx, scr, lower, Kind::Level, None, hud),
        _ => panel(&mut ctx, scr, lower, Kind::Tempo, None, hud),
    }
}

#[cfg(test)]
pub(crate) mod tests;
