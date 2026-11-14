//! The dashboard: what is sampled, what is remembered between frames, and how it is laid
//! out and drawn. A port of termstats/cli.py's render_dashboard() - same budget, same
//! widths, same number formats - onto a cell grid instead of rich renderables.

use crate::color::mix;
use crate::screen::{Screen, NONE};
use crate::sys::{self, CpuTimes, Disk, Mem, Net, ProcSample};
use crate::themes::Theme;
use std::collections::{HashMap, VecDeque};

pub const HISTORY_LEN: usize = 60;
const LABEL_W: usize = 9;
const VALUE_W: usize = 7;
const RATE_W: usize = 9;
const DISK_LABEL_W: usize = 12;
const MIN_BAR_W: i64 = 6;
const NOTE_GB_PAIR_W: usize = 13;
const NOTE_MEM_W: usize = 26;
const NOTE_TOTAL_W: usize = 10;
const SPARK_W: usize = 16;
const PEAK_WINDOW: usize = 30;
const SMOOTH_ALPHA: f64 = 0.5;
const RIGHT_COL_MIN: usize = 36;
const RIGHT_COL_MAX: usize = 52;
const RIGHT_COL_SHARE: f64 = 0.4;
const NARROW_BELOW: usize = 92;
const CHART_MIN_H: i64 = 13;
const CHART_MAX_H: i64 = 12;
const AXIS_W_PCT: usize = 3;
const AXIS_W_RATE: usize = 5;
const CHART_FADE_TOP: f64 = 0.55;
/// The process table is re-read at most this often; partitions likewise, more rarely.
const PROC_SCAN_S: f64 = 1.0;
const DISK_SCAN_S: f64 = 5.0;

const BAR_FULL: char = '█';
const BAR_PARTIALS: [char; 7] = ['▏', '▎', '▍', '▌', '▋', '▊', '▉'];
const BAR_EMPTY: char = '╌';
const BAR_SECONDARY: char = '▒';
const PEAK_MARK: char = '╵';
const SPARK: [char; 8] = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
/// Braille dot bits, row 0 at the top of the cell: (left column, right column).
const BRAILLE_BITS: [[u32; 2]; 4] = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]];
const NET_UNITS: [(f64, &str); 3] = [(1024.0, "KB/s"), (1048576.0, "MB/s"), (1073741824.0, "GB/s")];

pub struct Options {
    pub interval: f64,
    pub theme: &'static Theme,
    pub compact: bool,
    pub no_border: bool,
    pub live: bool,
    /// The microphone screens are one key away (the Python program is there to show them).
    pub switch_keys: bool,
}

struct ProcRow {
    pid: i32,
    cpu: f64,
    rss: u64,
}

#[derive(Clone, Copy)]
struct Rect {
    x: usize,
    y: usize,
    w: usize,
    h: usize,
}

struct Meter<'a> {
    label: &'a str,
    pct: f64,
    value: Option<String>,
    note: &'a str,
    label_w: usize,
    value_w: usize,
    secondary: f64,
    note_w: Option<usize>,
    fill: f64,
    peak: f64,
    unit_w: usize,
}

impl<'a> Meter<'a> {
    fn new(label: &'a str, pct: f64, fill: f64, peak: f64) -> Self {
        Meter { label, pct, value: None, note: "", label_w: LABEL_W, value_w: VALUE_W, secondary: 0.0,
                note_w: None, fill, peak, unit_w: 1 }
    }
}

pub struct State {
    pub opts: Options,
    host: String,
    primed: bool,
    // cpu
    prev_cores: Vec<CpuTimes>,
    cores: Vec<CpuTimes>,
    prev_all: CpuTimes,
    prev_steal: u64,
    core_pct: Vec<f64>,
    total_pct: f64,
    steal_pct: f64,
    // memory, disks, network
    mem: Mem,
    disks: Vec<Disk>,
    disks_at: f64,
    net: Net,
    net_prev: Option<(Net, f64)>,
    net_buf: Vec<u8>,
    sent_s: f64,
    recv_s: f64,
    io_prev: Option<((u64, u64), f64)>,
    io_rate: Option<(f64, f64)>,
    has_io: bool,
    // processes
    pids: Vec<i32>,
    samples: Vec<ProcSample>,
    proc_prev: Vec<(i32, u64)>,
    proc_prev_at: f64,
    ranked: Vec<ProcRow>,
    names: HashMap<i32, String>,
    nproc: usize,
    // history
    cpu_hist: VecDeque<f64>,
    steal_hist: VecDeque<f64>,
    sent_hist: VecDeque<f64>,
    recv_hist: VecDeque<f64>,
    net_unit: usize,
    // what a meter remembers between frames
    eased: HashMap<u64, (f64, bool)>,
    peaks: HashMap<u64, (VecDeque<f64>, bool)>,
}

fn key(kind: u64, id: u64) -> u64 {
    (kind << 40) | (id & 0xFF_FFFF_FFFF)
}

fn hash_str(s: &str) -> u64 {
    s.bytes().fold(0xcbf29ce484222325u64, |h, b| (h ^ b as u64).wrapping_mul(0x100000001b3))
}

const K_CORE: u64 = 1;
const K_MISC: u64 = 2;
const K_DISK: u64 = 3;
const K_PROC: u64 = 4;
const M_TOTAL: u64 = 1;
const M_STEAL: u64 = 2;
const M_RAM: u64 = 3;
const M_SWAP: u64 = 4;
const M_TX: u64 = 5;
const M_RX: u64 = 6;

// ---------------------------------------------------------------------------------
// Fixed-width number formats: a value never changes the width of its field.
// ---------------------------------------------------------------------------------

fn clean(x: f64) -> f64 {
    if x.is_nan() { 0.0 } else { x }
}

pub fn fmt_pct(x: f64) -> String {
    format!("{:5.1}%", clean(x).clamp(0.0, 999.9))
}

pub fn fmt_gb(n: f64) -> String {
    let gb = (clean(n) / 1073741824.0).max(0.0);
    if gb >= 1000.0 { format!("{:5.1}T", (gb / 1024.0).min(999.9)) } else { format!("{:5.1}G", gb) }
}

fn fmt_gb_pair(used: f64, total: f64) -> String {
    format!("{}/{}", fmt_gb(used), fmt_gb(total))
}

pub fn fmt_rate(bytes_per_s: f64) -> String {
    let mut v = clean(bytes_per_s).max(0.0);
    for unit in ["B", "K", "M", "G"] {
        if v < 999.95 || unit == "G" {
            return format!("{:5.1}{}/s", v, unit);
        }
        v /= 1024.0;
    }
    format!("{:5.1}G/s", v)
}

fn fmt_mem(b: f64) -> String {
    if b >= 1073741824.0 { format!("{:4.1}G", b / 1073741824.0) } else { format!("{:4.0}M", b / 1048576.0) }
}

fn fmt_axis(v: f64, width: usize, top: f64) -> String {
    if top < 10.0 {
        format!("{:w$.1}", v, w = width)
    } else {
        format!("{:w$.0}", v.min(10f64.powi(width as i32) - 1.0), w = width)
    }
}

/// Python's `{:g}` for an interval: `0.5`, `1`, `2.5`.
fn fmt_g(x: f64) -> String {
    let s = format!("{:.6}", x);
    let s = s.trim_end_matches('0').trim_end_matches('.');
    s.to_string()
}

pub fn nice_ceiling(x: f64) -> f64 {
    if x <= 0.0 {
        return 1.0;
    }
    let base = 10f64.powf(x.log10().floor());
    for m in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0] {
        if m * base >= x {
            return m * base;
        }
    }
    10.0 * base
}

/// The narrowest column a core meter is still a meter in: label, three cells of bar, value.
const CORE_COL_MIN_W: usize = LABEL_W + 3 + VALUE_W;

/// How many columns the per-core meters are laid out in, so they fit `max_rows` AND each
/// column is wide enough to be read. 0 = neither fits: one heat strip instead of a list.
pub fn core_columns(ncores: usize, max_rows: usize, width: usize) -> usize {
    if ncores <= max_rows {
        return 1;
    }
    (2..=4).find(|&cols| ncores.div_ceil(cols) <= max_rows && width.saturating_sub(2 * (cols - 1)) / cols >= CORE_COL_MIN_W)
        .unwrap_or(0)
}

fn cpu_section_rows(ncores: usize, max_rows: usize, width: usize) -> usize {
    let cols = core_columns(ncores, max_rows, width);
    let core_rows = match cols {
        0 => 1,
        1 => ncores,
        _ => ncores.div_ceil(cols),
    };
    core_rows + 1 + usize::from(sys::HAS_STEAL)
}

/// Shorten a mountpoint from the left, keeping the part that identifies it.
pub fn short_mount(label: &str, width: usize) -> String {
    let n = label.chars().count();
    if n <= width {
        return label.to_string();
    }
    let tail = label.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    if !tail.is_empty() && tail.chars().count() + 1 <= width {
        return format!("…{tail}");
    }
    let keep: String = label.chars().skip(n - (width - 1)).collect();
    format!("…{keep}")
}

fn window_label(interval: f64) -> String {
    let seconds = HISTORY_LEN as f64 * interval;
    if seconds < 90.0 {
        return format!("last {:.0}s", seconds);
    }
    let minutes = format!("{:.1}", seconds / 60.0);
    format!("last {}m", minutes.trim_end_matches('0').trim_end_matches('.'))
}

fn push(hist: &mut VecDeque<f64>, v: f64) {
    if hist.len() == HISTORY_LEN {
        hist.pop_front();
    }
    hist.push_back(v);
}

/// The CPU share of every sampled process since the previous scan, as (pid, percent).
/// Sorts `samples` by pid and walks it beside `before` (sorted by pid as well): the earlier
/// CPU time of each process is found without hashing seven hundred keys every second.
fn cpu_shares(samples: &mut [ProcSample], before: &[(i32, u64)], dt_ns: f64) -> Vec<(i32, f64)> {
    let usable = dt_ns > 0.0 && dt_ns.is_finite();
    samples.sort_unstable_by_key(|s| s.pid);
    let mut earlier = before.iter().peekable();
    samples.iter().map(|s| {
        while earlier.peek().is_some_and(|p| p.0 < s.pid) {
            earlier.next();
        }
        let cpu = match earlier.peek() {
            Some(&&(pid, ns)) if pid == s.pid && usable => s.cpu_ns.saturating_sub(ns) as f64 * 100.0 / dt_ns,
            _ => 0.0,
        };
        (s.pid, cpu)
    }).collect()
}

/// Descending CPU with the PID as tiebreaker, or rows at equal CPU swap every frame.
fn rank(rows: &mut [ProcRow]) {
    rows.sort_unstable_by(|a, b| b.cpu.partial_cmp(&a.cpu).unwrap_or(std::cmp::Ordering::Equal).then(a.pid.cmp(&b.pid)));
}

fn pct_of(now: CpuTimes, before: CpuTimes) -> f64 {
    let total = now.total.saturating_sub(before.total);
    if total == 0 { 0.0 } else { (now.busy.saturating_sub(before.busy) as f64 * 100.0 / total as f64).clamp(0.0, 100.0) }
}

/// A name drawn in single cells: anything that might be double-width becomes "?".
fn cell_safe(name: &str) -> String {
    name.chars().map(|c| if (c as u32) < 0x1100 && !c.is_control() { c } else { '?' }).collect()
}

impl State {
    pub fn new(opts: Options) -> Self {
        State {
            opts, host: cell_safe(&sys::hostname()), primed: false,
            prev_cores: Vec::new(), cores: Vec::new(), prev_all: CpuTimes::default(), prev_steal: 0,
            core_pct: Vec::new(), total_pct: 0.0, steal_pct: 0.0,
            mem: Mem::default(), disks: Vec::new(), disks_at: f64::NEG_INFINITY,
            net: Net::default(), net_prev: None, net_buf: Vec::new(), sent_s: 0.0, recv_s: 0.0,
            io_prev: None, io_rate: None, has_io: false,
            pids: Vec::new(), samples: Vec::new(), proc_prev: Vec::new(), proc_prev_at: f64::NEG_INFINITY,
            ranked: Vec::new(), names: HashMap::new(), nproc: 0,
            cpu_hist: VecDeque::with_capacity(HISTORY_LEN), steal_hist: VecDeque::with_capacity(HISTORY_LEN),
            sent_hist: VecDeque::with_capacity(HISTORY_LEN), recv_hist: VecDeque::with_capacity(HISTORY_LEN),
            net_unit: 0, eased: HashMap::new(), peaks: HashMap::new(),
        }
    }

    // --- sampling ----------------------------------------------------------------------

    /// Read the machine. The first call only sets the baselines every rate needs; from the
    /// second on, each call is one sample in the histories. `mono` is a monotonic clock.
    pub fn sample(&mut self, mono: f64) {
        std::mem::swap(&mut self.prev_cores, &mut self.cores);
        let (all, steal) = sys::cpu_times(&mut self.cores);
        self.core_pct.clear();
        for (i, now) in self.cores.iter().enumerate() {
            let before = self.prev_cores.get(i).copied().unwrap_or(*now);
            self.core_pct.push(pct_of(*now, before));
        }
        let span = all.total.saturating_sub(self.prev_all.total);
        if self.primed {
            self.total_pct = pct_of(all, self.prev_all);
            self.steal_pct = if span == 0 { 0.0 } else { steal.saturating_sub(self.prev_steal) as f64 * 100.0 / span as f64 };
        }
        self.prev_all = all;
        self.prev_steal = steal;

        self.mem = sys::memory();
        if mono - self.disks_at >= DISK_SCAN_S || !self.opts.live {
            self.disks = sys::disks();
            self.disks_at = mono;
        }

        let net = sys::net(&mut self.net_buf);
        if let Some((before, at)) = self.net_prev {
            let dt = mono - at;
            if dt > 0.0 {
                self.sent_s = net.sent.saturating_sub(before.sent) as f64 / dt;
                self.recv_s = net.recv.saturating_sub(before.recv) as f64 / dt;
            }
        }
        self.net_prev = Some((net, mono));
        self.net = net;

        let io = sys::disk_io();
        self.has_io = io.is_some();
        if let Some(now) = io {
            if let Some((before, at)) = self.io_prev {
                let dt = mono - at;
                if dt > 0.0 {
                    self.io_rate = Some((now.0.saturating_sub(before.0) as f64 / dt, now.1.saturating_sub(before.1) as f64 / dt));
                }
            }
            self.io_prev = Some((now, mono));
        }

        // One system call per process: the one collector whose cost grows with the machine.
        // The 50 ms of slack keeps a 1 s interval scanning on every frame despite jitter.
        if mono - self.proc_prev_at >= PROC_SCAN_S - 0.05 || !self.opts.live {
            self.scan_processes(mono);
        }

        if self.primed {
            push(&mut self.cpu_hist, self.total_pct);
            push(&mut self.steal_hist, self.steal_pct);
            push(&mut self.sent_hist, self.sent_s);
            push(&mut self.recv_hist, self.recv_s);
        }
        self.primed = true;
    }

    fn scan_processes(&mut self, mono: f64) {
        self.nproc = sys::proc_scan(&mut self.pids, &mut self.samples);
        let dt_ns = (mono - self.proc_prev_at) * 1e9;
        let shares = cpu_shares(&mut self.samples, &self.proc_prev, dt_ns);
        self.ranked.clear();
        self.ranked.extend(self.samples.iter().zip(shares).map(|(s, (pid, cpu))| ProcRow { pid, cpu, rss: s.rss }));
        self.proc_prev.clear();
        self.proc_prev.extend(self.samples.iter().map(|s| (s.pid, s.cpu_ns)));
        rank(&mut self.ranked);
        self.proc_prev_at = mono;
        self.names.clear();
    }

    // --- what a meter remembers ----------------------------------------------------------

    /// The fill to draw: eased in a live session, raw in a snapshot. Numbers are never eased.
    fn shown(&mut self, k: u64, raw: f64) -> f64 {
        if !self.opts.live {
            return raw;
        }
        let entry = self.eased.entry(k).or_insert((raw, true));
        entry.0 += SMOOTH_ALPHA * (raw - entry.0);
        entry.1 = true;
        entry.0
    }

    /// The high-water mark of the last PEAK_WINDOW samples.
    fn peak(&mut self, k: u64, raw: f64) -> f64 {
        let entry = self.peaks.entry(k).or_insert_with(|| (VecDeque::with_capacity(PEAK_WINDOW), true));
        if entry.0.len() == PEAK_WINDOW {
            entry.0.pop_front();
        }
        entry.0.push_back(raw);
        entry.1 = true;
        entry.0.iter().cloned().fold(f64::MIN, f64::max)
    }

    fn end_frame(&mut self) {
        self.eased.retain(|_, v| std::mem::replace(&mut v.1, false));
        self.peaks.retain(|_, v| std::mem::replace(&mut v.1, false));
    }

    // --- colour --------------------------------------------------------------------------

    fn ramp(&self, t: f64) -> u32 {
        self.opts.theme.ramp[(clean(t).clamp(0.0, 1.0) * 255.0).round() as usize]
    }

    fn ramp_dim(&self, t: f64) -> u32 {
        self.opts.theme.dim[(clean(t).clamp(0.0, 1.0) * 255.0).round() as usize]
    }

    // --- meters --------------------------------------------------------------------------

    fn bar(&self, scr: &mut Screen, x: usize, y: usize, width: usize, pct: f64, secondary: f64, peak: Option<f64>) {
        if width == 0 {
            return;
        }
        let th = self.opts.theme;
        let pct = clean(pct).clamp(0.0, 100.0);
        let secondary = clean(secondary).clamp(0.0, 100.0 - pct);
        let span = (width.max(2) - 1) as f64;
        let cells = width as f64 * pct / 100.0;
        let mut filled = (cells as usize).min(width);
        for i in 0..filled {
            scr.set(x + i, y, BAR_FULL, self.ramp(i as f64 / span), false);
        }
        if filled < width {
            let eighths = ((cells - filled as f64) * 8.0) as usize;
            if eighths > 0 {
                scr.set(x + filled, y, BAR_PARTIALS[eighths.min(7) - 1], self.ramp(filled as f64 / span), false);
                filled += 1;
            }
        }
        let second = ((width as f64 * secondary / 100.0) as usize).min(width - filled);
        for i in 0..second {
            scr.set(x + filled + i, y, BAR_SECONDARY, self.ramp_dim((filled + i) as f64 / span), false);
        }
        filled += second;
        // Only strictly beyond the fill: a hairline in the cell right after it says nothing.
        let peak_cell = peak.filter(|p| !p.is_nan())
            .map(|p| ((width as f64 * p.clamp(0.0, 100.0) / 100.0) as usize).min(width - 1))
            .filter(|&c| c > filled);
        for i in filled..width {
            if Some(i) == peak_cell {
                scr.set(x + i, y, PEAK_MARK, self.ramp(i as f64 / span), false);
            } else {
                scr.set(x + i, y, BAR_EMPTY, th.track, false);
            }
        }
    }

    /// `label  ▉▉▉▉╌╌╌  62.5%  note` on exactly one line.
    fn meter(&self, scr: &mut Screen, x: usize, y: usize, total: usize, m: Meter) {
        let th = self.opts.theme;
        let occupied = (m.pct + m.secondary.max(0.0)).min(100.0);
        let value = m.value.unwrap_or_else(|| fmt_pct(occupied));
        let mut note = m.note;
        let mut field_w = m.note_w.unwrap_or_else(|| note.chars().count());
        let room = total as i64 - m.label_w as i64 - m.value_w as i64;
        // Too narrow for everything: the note is dropped, never sliced.
        if field_w > 0 && room - (field_w as i64 + 2) < MIN_BAR_W {
            note = "";
            field_w = 0;
        }
        let bar_w = (room - if field_w > 0 { field_w as i64 + 2 } else { 0 }).max(3) as usize;
        let limit = x + total;
        let label: String = m.label.chars().take(m.label_w - 1).collect();
        let mut col = scr.put(x, y, limit, &format!("{:>w$} ", label, w = m.label_w - 1), th.label, false);
        self.bar(scr, col, y, bar_w.min(limit.saturating_sub(col)), m.fill, m.secondary, Some(m.peak));
        col += bar_w;
        let padded = format!("{:>w$}", value, w = m.value_w);
        let split = padded.chars().count().saturating_sub(m.unit_w);
        let digits: String = padded.chars().take(split).collect();
        let unit: String = padded.chars().skip(split).collect();
        col = scr.put(col, y, limit, &digits, self.ramp(occupied / 100.0), true);
        col = scr.put(col, y, limit, &unit, th.label, false);
        if field_w > 0 {
            scr.put(col, y, limit, &format!("  {:>w$}", note, w = field_w), th.muted, false);
        }
    }

    // --- sections ------------------------------------------------------------------------

    fn cpu_section(&mut self, scr: &mut Screen, r: Rect, max_rows: usize) {
        let n = self.core_pct.len();
        let cols = core_columns(n, max_rows, r.w);
        let mut y = r.y;
        if cols == 0 {
            // Too many cores to list: one cell per core, coloured by its load.
            let x = scr.put(r.x, y, r.x + r.w, "      cores ", self.opts.theme.label, false);
            let width = r.w.saturating_sub(14).max(1);
            let step = n.div_ceil(width).max(1);
            for (i, chunk) in self.core_pct.chunks(step).enumerate() {
                let avg = chunk.iter().sum::<f64>() / chunk.len() as f64;
                scr.set(x + i, y, BAR_FULL, self.ramp(avg / 100.0), false);
            }
            y += 1;
        } else {
            let col_w = if cols == 1 { r.w } else { (r.w - 2 * (cols - 1)) / cols };
            let per_col = n.div_ceil(cols);
            for i in 0..n {
                let (c, row) = (i / per_col, i % per_col);
                let p = self.core_pct[i];
                let (fill, peak) = (self.shown(key(K_CORE, i as u64), p), self.peak(key(K_CORE, i as u64), p));
                let label = format!("cpu{i}");
                self.meter(scr, r.x + c * (col_w + 2), y + row, col_w, Meter::new(&label, p, fill, peak));
            }
            y += per_col;
        }
        let total = self.total_pct;
        let (fill, peak) = (self.shown(key(K_MISC, M_TOTAL), total), self.peak(key(K_MISC, M_TOTAL), total));
        self.meter(scr, r.x, y, r.w, Meter::new("TOTAL", total, fill, peak));
        if sys::HAS_STEAL {
            let steal = self.steal_pct;
            let (fill, peak) = (self.shown(key(K_MISC, M_STEAL), steal), self.peak(key(K_MISC, M_STEAL), steal));
            self.meter(scr, r.x, y + 1, r.w, Meter::new("steal", steal, fill, peak));
        }
    }

    fn memory_rows(&self) -> usize {
        1 + usize::from(self.mem.swap_total > 0)
    }

    fn memory_section(&mut self, scr: &mut Screen, r: Rect) {
        let mem = self.mem;
        let total = mem.total.max(1) as f64;
        // What is neither in use nor available is the kernel's cache: its own dimmed segment.
        let cache = mem.total.saturating_sub(mem.used).saturating_sub(mem.available);
        let used_pct = 100.0 * mem.used as f64 / total;
        let percent = 100.0 * mem.total.saturating_sub(mem.available) as f64 / total;
        let cache_pct = (percent - used_pct).max(0.0);
        let wide = r.w >= LABEL_W + VALUE_W + NOTE_MEM_W + 2 + MIN_BAR_W as usize;
        let mut note = fmt_gb_pair(mem.used as f64, mem.total as f64);
        if wide && cache > 0 {
            note.push_str(&format!(" +{:4.1}G cache", (cache as f64 / 1073741824.0).min(99.9)));
        }
        let note_w = Some(if wide { NOTE_MEM_W } else { NOTE_GB_PAIR_W });
        let (fill, peak) = (self.shown(key(K_MISC, M_RAM), used_pct), self.peak(key(K_MISC, M_RAM), percent));
        self.meter(scr, r.x, r.y, r.w, Meter { value: Some(fmt_pct(percent)), note: &note, note_w, secondary: cache_pct,
                                              ..Meter::new("ram", used_pct, fill, peak) });
        if mem.swap_total > 0 {
            let pct = 100.0 * mem.swap_used as f64 / mem.swap_total as f64;
            let note = fmt_gb_pair(mem.swap_used as f64, mem.swap_total as f64);
            let (fill, peak) = (self.shown(key(K_MISC, M_SWAP), pct), self.peak(key(K_MISC, M_SWAP), pct));
            self.meter(scr, r.x, r.y + 1, r.w, Meter { note: &note, note_w, ..Meter::new("swap", pct, fill, peak) });
        }
    }

    fn disk_rows(&self) -> usize {
        self.disks.len().max(1) + usize::from(self.has_io)
    }

    fn disk_section(&mut self, scr: &mut Screen, r: Rect) {
        let th = self.opts.theme;
        let mut y = r.y;
        for i in 0..self.disks.len() {
            let (label, pct) = (short_mount(&cell_safe(&self.disks[i].mount), DISK_LABEL_W), self.disks[i].percent);
            let note = fmt_gb_pair(self.disks[i].used as f64, self.disks[i].total as f64);
            let k = key(K_DISK, hash_str(&self.disks[i].mount));
            let (fill, peak) = (self.shown(k, pct), self.peak(k, pct));
            self.meter(scr, r.x, y, r.w, Meter { label_w: DISK_LABEL_W + 1, note: &note, note_w: Some(NOTE_GB_PAIR_W),
                                                ..Meter::new(&label, pct, fill, peak) });
            y += 1;
        }
        if self.disks.is_empty() {
            scr.put(r.x, y, r.x + r.w, "  No disks found", th.muted, false);
            y += 1;
        }
        if self.has_io {
            // Present from the first frame, with placeholders until the second sample exists.
            let limit = r.x + r.w;
            let placeholder = format!("{:>w$}", "n/a", w = RATE_W - 1);
            let (read, write) = match self.io_rate {
                Some((rd, wr)) => (fmt_rate(rd), fmt_rate(wr)),
                None => (placeholder.clone(), placeholder),
            };
            let mut col = scr.put(r.x, y, limit, &format!("{:>w$} ", "io", w = DISK_LABEL_W), th.label, false);
            col = scr.put(col, y, limit, "read ", th.muted, false);
            col = scr.put(col, y, limit, &read, th.soft, false);
            col = scr.put(col, y, limit, "   write ", th.muted, false);
            scr.put(col, y, limit, &write, th.soft, false);
        }
    }

    fn network_section(&mut self, scr: &mut Screen, r: Rect) {
        let window = self.sent_hist.iter().chain(self.recv_hist.iter()).cloned().fold(1.0f64, f64::max);
        let top = window.max(self.sent_s).max(self.recv_s);
        for (row, (label, rate, total, id)) in [("tx", self.sent_s, self.net.sent, M_TX), ("rx", self.recv_s, self.net.recv, M_RX)]
            .into_iter().enumerate() {
            let pct = 100.0 * rate / top;
            let note = format!("Σ {}", fmt_gb(total as f64));
            let (fill, peak) = (self.shown(key(K_MISC, id), pct), self.peak(key(K_MISC, id), pct));
            self.meter(scr, r.x, r.y + row, r.w, Meter { value: Some(fmt_rate(rate)), value_w: RATE_W, unit_w: 3, note: &note,
                                                        note_w: Some(NOTE_TOTAL_W), ..Meter::new(label, pct, fill, peak) });
        }
    }

    fn process_section(&mut self, scr: &mut Screen, r: Rect) {
        let th = self.opts.theme;
        if r.h == 0 || r.w < 34 {
            return;
        }
        // The inline bar earns the space the name column used to leave empty.
        let bar_w = if r.w >= 86 { 14 } else { 0 };
        // Six cells hold every pid macOS hands out; Linux counts to 4194304. The column
        // takes the seventh cell from the name rather than pushing the row out of line.
        let rows = (r.h - 1).min(self.ranked.len());
        let pid_w = if self.ranked[..rows].iter().any(|p| p.pid > 999_999) { 7 } else { 6 };
        let fixed = pid_w + bar_w + 6 + 6 + 7 + 2 * (if bar_w > 0 { 5 } else { 4 });
        let name_w = r.w.saturating_sub(fixed).max(1);
        let x_name = r.x + pid_w + 2;
        let x_bar = x_name + name_w + 2;
        let x_cpu = if bar_w > 0 { x_bar + bar_w + 2 } else { x_bar };
        let (x_mem, x_rss, limit) = (x_cpu + 6 + 2, x_cpu + 6 + 2 + 6 + 2, r.x + r.w);
        scr.put(r.x, r.y, limit, &format!("{:>w$}", "pid", w = pid_w), th.muted, false);
        scr.put(x_name, r.y, limit, "process", th.muted, false);
        scr.put(x_cpu, r.y, limit, &format!("{:>6}", "cpu%"), th.muted, false);
        scr.put(x_mem, r.y, limit, &format!("{:>6}", "mem%"), th.muted, false);
        scr.put(x_rss, r.y, limit, &format!("{:>7}", "rss"), th.muted, false);
        let total_mem = self.mem.total.max(1) as f64;
        for row in 0..rows {
            let (pid, cpu, rss) = (self.ranked[row].pid, self.ranked[row].cpu, self.ranked[row].rss);
            let y = r.y + 1 + row;
            let name = self.names.entry(pid).or_insert_with(|| cell_safe(&sys::proc_name(pid))).clone();
            scr.put(r.x, y, limit, &format!("{:>w$}", pid, w = pid_w), th.muted, false);
            if name.chars().count() > name_w {
                let cut: String = name.chars().take(name_w - 1).collect();
                scr.put(x_name, y, limit, &format!("{cut}…"), NONE, false);
            } else {
                scr.put(x_name, y, limit, &name, NONE, false);
            }
            if bar_w > 0 {
                let k = key(K_PROC, pid as u64);
                let (fill, peak) = (self.shown(k, cpu), self.peak(k, cpu));
                self.bar(scr, x_bar, y, bar_w - 1, fill, 0.0, Some(peak));
            }
            let mem_pct = 100.0 * rss as f64 / total_mem;
            scr.put(x_cpu, y, limit, &format!("{:>6}", format!("{:5.1}", cpu.min(9999.9))), self.ramp(cpu / 100.0), true);
            scr.put(x_mem, y, limit, &format!("{:>6}", format!("{:5.1}", mem_pct.min(100.0))), self.ramp(mem_pct / 25.0), false);
            scr.put(x_rss, y, limit, &format!("{:>7}", fmt_mem(rss as f64)), th.soft, false);
        }
    }

    // --- charts --------------------------------------------------------------------------

    fn collecting(&self, scr: &mut Screen, r: Rect) {
        let th = self.opts.theme;
        let (width, height) = (r.w.max(8), r.h.max(1));
        let baseline = (height - 1) / 2;
        scr.put(r.x, r.y + baseline, r.x + r.w, &"⠒".repeat(width), th.track, false);
        if baseline + 1 < height {
            let hint = "collecting · 2 samples needed";
            let pad = width.saturating_sub(hint.chars().count()) / 2;
            scr.put(r.x + pad, r.y + baseline + 1, r.x + r.w, hint, th.muted, false);
        }
    }

    /// Rasterise series into braille dots: a filled area from the floor, lines drawn over it
    /// and joined vertically, a fixed-width y axis, one row of time labels.
    fn chart(&self, scr: &mut Screen, r: Rect, series: &[(Vec<f64>, u32, bool)], ylim: Option<(f64, f64)>, axis_w: usize) {
        let th = self.opts.theme;
        if r.w <= axis_w || r.h < 2 || series.is_empty() {
            return;
        }
        let (lo, hi) = ylim.unwrap_or_else(|| {
            let top = series.iter().flat_map(|s| s.0.iter().cloned()).fold(f64::MIN, f64::max);
            (0.0, nice_ceiling(if top.is_finite() { top } else { 1.0 }))
        });
        let span = if hi - lo == 0.0 { 1.0 } else { hi - lo };
        let (plot_w, plot_h) = (r.w - axis_w, r.h - 1);
        let (dot_w, dot_h) = (plot_w * 2, plot_h * 4);
        let mut masks = vec![0u32; plot_w * plot_h];
        let mut owner = vec![0usize; plot_w * plot_h];      // 0 = empty, else series index + 1
        for (si, (values, _colour, fill)) in series.iter().enumerate() {
            let n = values.len();
            if n == 0 {
                continue;
            }
            let step = if n > 1 && dot_w > 1 { (n - 1) as f64 / (dot_w - 1) as f64 } else { 0.0 };
            let mut previous: Option<usize> = None;
            for x in 0..dot_w {
                let pos = x as f64 * step;
                let i = pos as usize;
                let v = if i + 1 >= n { values[n - 1] } else { values[i] + (values[i + 1] - values[i]) * (pos - i as f64) };
                let t = clean((v - lo) / span).clamp(0.0, 1.0);
                let yv = (t * (dot_h - 1) as f64).round() as usize;
                let (first, last) = if *fill {
                    (0, yv)
                } else {
                    let p = previous.unwrap_or(yv);
                    previous = Some(yv);
                    (if p < yv { yv.min(p + 1) } else { yv }, if p > yv { yv.max(p - 1) } else { yv })
                };
                for dot in first..=last {
                    let cell = (plot_h - 1 - dot / 4) * plot_w + x / 2;
                    masks[cell] |= BRAILLE_BITS[3 - dot % 4][x % 2];
                    // A line's colour wins over the fill's, so the line stays visible.
                    if !fill || owner[cell] == 0 {
                        owner[cell] = si + 1;
                    }
                }
            }
        }
        // The fill fades toward the background from the floor up: weight where the data is.
        let fill_rows: Vec<usize> = (0..plot_h)
            .filter(|row| (0..plot_w).any(|c| owner[row * plot_w + c] != 0 && series[owner[row * plot_w + c] - 1].2))
            .collect();
        let (top_row, bottom_row) = (fill_rows.first().copied().unwrap_or(0), fill_rows.last().copied().unwrap_or(0));
        let mid_row = (plot_h - 1) / 2;
        for row in 0..plot_h {
            let y = r.y + row;
            let label = if row == 0 { Some(hi) } else if row == plot_h - 1 { Some(lo) }
                        else if row == mid_row { Some((lo + hi) / 2.0) } else { None };
            if let Some(v) = label {
                scr.put(r.x, y, r.x + axis_w, &fmt_axis(v, axis_w, hi), th.muted, false);
            }
            let k = CHART_FADE_TOP * bottom_row.saturating_sub(row) as f64 / bottom_row.saturating_sub(top_row).max(1) as f64;
            let mut faded: [Option<u32>; 4] = [None; 4];
            for c in 0..plot_w {
                let cell = row * plot_w + c;
                if masks[cell] == 0 {
                    continue;
                }
                let si = owner[cell] - 1;
                let colour = if series[si].2 {
                    *faded[si.min(3)].get_or_insert_with(|| mix(series[si].1, th.bg, k))
                } else {
                    series[si].1
                };
                scr.set(r.x + axis_w + c, y, char::from_u32(0x2800 + masks[cell]).unwrap_or(' '), colour, false);
            }
        }
        // The time axis: "now" at the right edge, the window's start at the left, the middle
        // between them - and a label that would touch its neighbour is left out.
        let window = HISTORY_LEN as f64 * self.opts.interval;
        let labels = [format!("-{:.0}s", window), format!("-{:.0}s", window / 2.0), "now".to_string()];
        let y = r.y + plot_h;
        let mut used: Vec<(usize, usize)> = Vec::new();
        let starts = [axis_w.saturating_sub(labels[0].len() / 2) as i64,
                      (axis_w + plot_w.saturating_sub(labels[1].len()) / 2) as i64,
                      r.w as i64 - 1 - labels[2].len() as i64];
        for i in [2usize, 0, 1] {
            let len = labels[i].len();
            if len > r.w {
                continue;
            }
            let start = starts[i].clamp(0, (r.w - len) as i64) as usize;
            if used.iter().any(|&(a, b)| start < b + 1 && a < start + len + 1) {
                continue;
            }
            used.push((start, start + len));
            scr.put(r.x + start, y, r.x + r.w, &labels[i], th.muted, false);
        }
    }

    /// (divisor, unit) for the network chart, with hysteresis so the axis does not flip.
    fn net_scale(&mut self) -> (f64, &'static str) {
        let peak = self.sent_hist.iter().chain(self.recv_hist.iter()).cloned().fold(0.0f64, f64::max);
        while self.net_unit + 1 < NET_UNITS.len() && peak >= 2.0 * NET_UNITS[self.net_unit + 1].0 {
            self.net_unit += 1;
        }
        while self.net_unit > 0 && peak < NET_UNITS[self.net_unit].0 {
            self.net_unit -= 1;
        }
        NET_UNITS[self.net_unit]
    }

    // --- frame ---------------------------------------------------------------------------

    fn chrome(&self) -> (usize, usize) {
        if self.opts.no_border { (1, 2) } else if self.opts.compact { (2, 2) } else { (2, 4) }
    }

    /// Draw a panel's frame and title; returns the rectangle its body may use.
    fn panel(&self, scr: &mut Screen, r: Rect, title: &str, subtitle: &[(String, u32)]) -> Rect {
        let th = self.opts.theme;
        if r.w < 4 || r.h == 0 {
            return Rect { x: r.x, y: r.y, w: 0, h: 0 };
        }
        let limit = r.x + r.w;
        if self.opts.no_border {
            // A title rule above the body and one gutter column each side.
            let mut col = scr.put(r.x + 1, r.y, limit - 1, title, th.accent, true);
            for (text, colour) in subtitle {
                col = scr.put(col, r.y, limit - 1, text, *colour, false);
            }
            col = scr.put(col, r.y, limit - 1, " ", NONE, false);
            for x in col..limit - 1 {
                scr.set(x, r.y, '─', th.border, false);
            }
            return Rect { x: r.x + 1, y: r.y + 1, w: r.w - 2, h: r.h - 1 };
        }
        if r.h < 2 {
            return Rect { x: r.x, y: r.y, w: 0, h: 0 };
        }
        let (right, bottom) = (limit - 1, r.y + r.h - 1);
        for x in r.x + 1..right {
            scr.set(x, r.y, '─', th.border, false);
            scr.set(x, bottom, '─', th.border, false);
        }
        for y in r.y + 1..bottom {
            scr.set(r.x, y, '│', th.border, false);
            scr.set(right, y, '│', th.border, false);
        }
        scr.set(r.x, r.y, '╭', th.border, false);
        scr.set(right, r.y, '╮', th.border, false);
        scr.set(r.x, bottom, '╰', th.border, false);
        scr.set(right, bottom, '╯', th.border, false);
        // "╭─ title · subtitle ─────╮": the title sits in the top border.
        let title_limit = right.saturating_sub(1);
        let mut col = scr.put(r.x + 2, r.y, title_limit, " ", NONE, false);
        col = scr.put(col, r.y, title_limit, title, th.accent, true);
        for (text, colour) in subtitle {
            col = scr.put(col, r.y, title_limit, text, *colour, false);
        }
        scr.put(col, r.y, title_limit, " ", NONE, false);
        let pad = if self.opts.compact { 0 } else { 1 };
        Rect { x: r.x + 1 + pad, y: r.y + 1, w: r.w - 2 - 2 * pad, h: r.h - 2 }
    }

    fn header(&mut self, scr: &mut Screen, width: usize) {
        let th = self.opts.theme;
        let load = sys::loadavg();
        let ncpu = self.core_pct.len().max(1);
        let uptime = (sys::now() - sys::boot_time()).max(0.0);
        // Every field has a fixed width: no value may move the fields to its right.
        let mut col = scr.put_bg(0, 0, width, " TERMSTATS ", th.wordmark_fg, th.wordmark_bg, true);
        let host: String = self.host.chars().take(12).collect();
        col = scr.put(col, 0, width, &format!("  {:12}", host), th.wordmark_fg, true);
        col = scr.put(col, 0, width, &format!(" {:7}", sys::OS_NAME), th.muted, false);
        col = scr.put(col, 0, width, " load ", th.muted, false);
        col = scr.put(col, 0, width, &format!("{:6.2}", load[0].min(999.99)), self.ramp(load[0] / (ncpu as f64 * 2.0)), true);
        col = scr.put(col, 0, width, &format!(" {:6.2} {:6.2}", load[1].min(999.99), load[2].min(999.99)), th.label, false);
        col = scr.put(col, 0, width, &format!("  {:3} cpu", ncpu.min(999)), th.muted, false);
        col = scr.put(col, 0, width, " up ", th.muted, false);
        let days = (uptime / 86400.0) as u64;
        col = scr.put(col, 0, width, &format!("{:3}d {:02}h", days.min(999), ((uptime % 86400.0) / 3600.0) as u64), th.text, false);
        col = scr.put(col, 0, width, " proc ", th.muted, false);
        col = scr.put(col, 0, width, &format!("{:5}", self.nproc.min(99999)), th.text, false);

        // The tail degrades in steps decided by the WIDTH alone: sparkline + clock, clock, none.
        let clock = format!("{}  ", local_time());
        let interval = format!("{:>4}s  ", fmt_g(self.opts.interval));
        let version = format!("v{} ", env!("CARGO_PKG_VERSION"));
        let tail_w = clock.len() + interval.len() + version.len();
        let free = width as i64 - col as i64 - tail_w as i64;
        let mut at = col;
        if free >= SPARK_W as i64 + 4 {
            at += (free as usize - SPARK_W) / 2;
            // Peaks rather than means: a sparkline exists to show that something spiked.
            let values: Vec<f64> = self.cpu_hist.iter().cloned().collect();
            let step = values.len().div_ceil(SPARK_W).max(1);
            let cells: Vec<f64> = values.chunks(step).map(|c| c.iter().cloned().fold(0.0, f64::max)).collect();
            for i in 0..SPARK_W - cells.len().min(SPARK_W) {
                scr.set(at + i, 0, SPARK[0], th.track, false);
            }
            let offset = SPARK_W - cells.len().min(SPARK_W);
            for (i, peak) in cells.iter().take(SPARK_W).enumerate() {
                let t = (peak / 100.0).clamp(0.0, 1.0);
                scr.set(at + offset + i, 0, SPARK[((t * 8.0) as usize).min(7)], self.ramp(t), false);
            }
            at = col + free as usize;
        } else if free >= 2 {
            at = col + free as usize;
        } else {
            return;
        }
        at = scr.put(at, 0, width, &clock, th.text, false);
        at = scr.put(at, 0, width, &interval, th.muted, false);
        scr.put(at, 0, width, &version, th.faint, false);
    }

    fn footer(&self, scr: &mut Screen, y: usize, width: usize) {
        let th = self.opts.theme;
        let brand = format!("© {} Martin Pfeffer | celox.io ", local_year());
        let brand_w = brand.chars().count();
        // Both fixed-width; the hint goes first when the line cannot hold everything.
        let exit_hint = " Esc or Ctrl+C to exit";
        let full = format!("{exit_hint}  s stats  b BPM  d dB  x both");
        let hint = if !self.opts.live { "" }
                   else if self.opts.switch_keys && full.len() + brand_w + 2 <= width { full.as_str() }
                   else { exit_hint };
        if hint.len() + brand_w + 2 <= width {
            scr.put(0, y, width, hint, th.muted, false);
        }
        scr.put(width.saturating_sub(brand_w), y, width, &brand, th.faint, false);
    }

    /// Compose the whole dashboard for a terminal of this size - the height budget first,
    /// because the layout has to know the height to decide what fits.
    pub fn draw(&mut self, scr: &mut Screen) {
        let (tw, th) = (scr.w, scr.h);
        if tw < 40 || th < 8 {
            scr.put(0, 0, tw, "termstats needs 40x8", NONE, false);
            return;
        }
        let (ch, cw) = self.chrome();
        let (chi, body_h) = (ch as i64, th as i64 - 2);
        let narrow = tw < NARROW_BELOW;
        let right_w = if narrow { 0 } else { ((tw as f64 * RIGHT_COL_SHARE) as usize).min(RIGHT_COL_MAX).max(RIGHT_COL_MIN) };
        let left_w = tw - right_w;
        let mem_h = (self.memory_rows() + ch) as i64;
        let net_h = (2 + ch) as i64;
        let disk_h = (self.disk_rows() + ch) as i64;

        // Height is decided before the columns are: the CPU panel would rather be one tall
        // column of wide bars than two short ones with a hole underneath.
        let ncores = self.core_pct.len().max(1);
        let extra_rows = 1 + i64::from(sys::HAS_STEAL);
        let cap = (body_h / 2).max(8);
        let cpu_body_w = (if narrow { tw } else { left_w }).saturating_sub(cw);
        let cpu_wanted = cpu_section_rows(ncores, (cap - chi - extra_rows).max(1) as usize, cpu_body_w) as i64 + chi;
        // A short CPU panel next to a tall stack leaves a hole: then the disk panel leaves
        // the stack and takes the full width.
        let disk_in_stack = cpu_wanted + chi >= mem_h + net_h + disk_h;
        let mut stack: Vec<(u8, i64)> = vec![(b'm', mem_h), (b'n', net_h)];
        if disk_in_stack {
            stack.push((b'd', disk_h));
        }
        let top_h;
        if narrow {
            let room = body_h - cpu_wanted;
            let (mut stack_h, mut kept) = (0i64, 0usize);
            for &(_, h) in &stack {
                if stack_h + h > room {
                    break;
                }
                stack_h += h;
                kept += 1;
            }
            stack.truncate(kept);
            top_h = cpu_wanted + stack_h;
        } else {
            top_h = cpu_wanted.max(stack.iter().map(|s| s.1).sum());
        }
        let remaining = body_h - top_h - if disk_in_stack { 0 } else { disk_h };
        let proc_min = chi + 3;
        let charts_h = if remaining >= CHART_MIN_H { CHART_MAX_H.min(remaining - proc_min) } else { 0 };
        let proc_h = remaining - charts_h;
        let cpu_h = if narrow { cpu_wanted } else { top_h };
        let core_rows = (cpu_h - chi - extra_rows).max(1) as usize;

        // The plan is not a guarantee: drop sections from the bottom until it fits, then let
        // the last one take what is left instead of leaving a blank strip.
        let mut sections: Vec<(u8, i64)> = vec![(b't', top_h)];
        if !disk_in_stack && !stack.is_empty() {
            sections.push((b'd', disk_h));
        }
        if charts_h > 0 {
            sections.push((b'c', charts_h));
        }
        if proc_h >= proc_min {
            sections.push((b'p', 0));
        }
        while sections.len() > 1 && 1 + sections.iter().map(|s| s.1).sum::<i64>() > th as i64 - 1 {
            sections.pop();
        }
        let last = sections.len() - 1;
        let fixed: i64 = sections[..last].iter().map(|s| s.1).sum();
        sections[last].1 = (body_h - fixed).max(0);

        self.header(scr, tw);
        let mut y = 1usize;
        for &(kind, height) in &sections {
            let h = (height.max(0) as usize).min(th - 1 - y);
            match kind {
                b't' => {
                    let cpu_rect = if stack.is_empty() { Rect { x: 0, y, w: tw, h } }
                                   else if narrow { Rect { x: 0, y, w: tw, h: (cpu_wanted as usize).min(h) } }
                                   else { Rect { x: 0, y, w: left_w, h } };
                    let body = self.panel(scr, cpu_rect, "cpu", &[]);
                    // Never more rows of cores than the panel really got: on a terminal too
                    // short for the plan the cores pack tighter (or become a heat strip) and
                    // TOTAL stays on screen instead of being cropped.
                    let fits = (body.h as i64 - extra_rows).max(1) as usize;
                    self.cpu_section(scr, body, core_rows.min(fits));
                    let (sx, sw) = if narrow { (0, tw) } else { (left_w, right_w) };
                    let mut sy = if narrow { y + cpu_rect.h } else { y };
                    for (i, &(which, sh)) in stack.iter().enumerate() {
                        let rest = (y + h).saturating_sub(sy);
                        let ph = if i + 1 == stack.len() { rest } else { (sh as usize).min(rest) };
                        let rect = Rect { x: sx, y: sy, w: sw, h: ph };
                        match which {
                            b'm' => { let b = self.panel(scr, rect, "memory", &[]); self.memory_section(scr, b) }
                            b'n' => { let b = self.panel(scr, rect, "network", &[]); self.network_section(scr, b) }
                            _ => { let b = self.panel(scr, rect, "disk", &[]); self.disk_section(scr, b) }
                        }
                        sy += ph;
                    }
                }
                b'd' => {
                    let b = self.panel(scr, Rect { x: 0, y, w: tw, h }, "disk", &[]);
                    self.disk_section(scr, b);
                }
                b'c' => self.charts(scr, Rect { x: 0, y, w: tw, h }, cw, (charts_h - chi).max(0) as usize),
                _ => {
                    let muted = self.opts.theme.muted;
                    let b = self.panel(scr, Rect { x: 0, y, w: tw, h }, "processes", &[(" · by cpu".to_string(), muted)]);
                    self.process_section(scr, b);
                }
            }
            y += h;
        }
        self.footer(scr, th - 1, tw);
        self.end_frame();
    }

    fn charts(&mut self, scr: &mut Screen, r: Rect, cw: usize, chart_h: usize) {
        let th = self.opts.theme;
        let left_w = r.w.div_ceil(2);
        let chart_w = ((r.w - 1) / 2).saturating_sub(cw);
        let now = self.cpu_hist.back().copied().unwrap_or(0.0);
        let cpu_sub = [(format!(" · {} · ", window_label(self.opts.interval)), th.muted),
                       (format!("{:3.0}", now.min(999.0)), self.ramp(now / 100.0)), ("%".to_string(), th.label)];
        let body = self.panel(scr, Rect { x: r.x, y: r.y, w: left_w, h: r.h }, "cpu", &cpu_sub);
        let area = Rect { x: body.x, y: body.y, w: chart_w.min(body.w), h: chart_h.min(body.h) };
        if self.cpu_hist.len() < 2 {
            self.collecting(scr, area);
        } else {
            // The area is tinted by the MEAN load of the window - an amber chart says what an
            // amber bar says.
            let mean = self.cpu_hist.iter().sum::<f64>() / self.cpu_hist.len() as f64;
            let mut series = vec![(self.cpu_hist.iter().cloned().collect::<Vec<f64>>(), self.ramp(mean / 100.0), true)];
            if sys::HAS_STEAL && self.steal_hist.iter().any(|&s| s > 0.0) {
                series.push((self.steal_hist.iter().cloned().collect(), self.ramp(1.0), false));
            }
            self.chart(scr, area, &series, Some((0.0, 100.0)), AXIS_W_PCT);
        }

        let (div, unit) = self.net_scale();
        let (rx_colour, tx_colour) = (self.ramp(0.0), self.ramp(0.55));
        let rx = fmt_rate(self.recv_hist.back().copied().unwrap_or(0.0));
        let tx = fmt_rate(self.sent_hist.back().copied().unwrap_or(0.0));
        let net_sub = [(" · ".to_string(), th.muted), ("▇ rx".to_string(), rx_colour), (format!(" {rx}  "), th.muted),
                       ("━ tx".to_string(), tx_colour), (format!(" {tx} · {unit:>4}"), th.muted)];
        let body = self.panel(scr, Rect { x: r.x + left_w, y: r.y, w: r.w - left_w, h: r.h }, "network", &net_sub);
        let area = Rect { x: body.x, y: body.y, w: chart_w.min(body.w), h: chart_h.min(body.h) };
        if self.sent_hist.len() < 2 {
            self.collecting(scr, area);
        } else {
            let series = vec![(self.recv_hist.iter().map(|v| v / div).collect::<Vec<f64>>(), rx_colour, true),
                              (self.sent_hist.iter().map(|v| v / div).collect::<Vec<f64>>(), tx_colour, false)];
            self.chart(scr, area, &series, None, AXIS_W_RATE);
        }
    }
}

fn local_tm() -> libc::tm {
    unsafe {
        let t = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&t, &mut tm);
        tm
    }
}

fn local_time() -> String {
    let tm = local_tm();
    format!("{:02}:{:02}:{:02}", tm.tm_hour, tm.tm_min, tm.tm_sec)
}

fn local_year() -> i32 {
    local_tm().tm_year + 1900
}

#[cfg(test)]
mod tests;
