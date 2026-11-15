//! The recorder: what the machine did, one line a minute, for weeks.
//!
//! `termstats-native record` is meant to run all day, so it is built to cost nothing that
//! can be seen: a light reading every five seconds (about a tenth of a millisecond), the
//! process table every thirty, one `poll()` between readings, and ONE line per minute
//! appended to a plain text file - written five minutes at a time. Fourteen days of that
//! are about four megabytes, and they are what `recommend` reads.
//!
//! The file is tab-separated text on purpose: it can be read with `less`, `awk` or a
//! spreadsheet, and a format nobody can inspect is a format nobody trusts with a log of
//! their own machine. It stays on the machine; nothing here opens a socket.

use crate::sys::{self, CpuTimes, Net, ProcSample, Vitals};
use std::collections::HashMap;
use std::fmt::Write as _;
use std::io::Write as _;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

pub const FORMAT: &str = "termstats-history v1";
/// Seconds between two readings.
pub const SAMPLE_S: i64 = 5;
/// The process table is read every this many readings.
const PROCS_EVERY: u32 = 6;
/// Lines are written this many minutes at a time: one write, no fsync.
const FLUSH_MINUTES: usize = 5;
/// A wall-clock step this much longer than the cadence is a sleeping machine, not idleness.
const GAP_FACTOR: i64 = 3;
/// Days of history `recommend` wants before it commits to an answer.
pub const READY_DAYS: i64 = 14;
pub const DEFAULT_KEEP_DAYS: i64 = 90;
const TOP_N: usize = 3;
const MIB: f64 = 1048576.0;

pub const COLUMNS: &[&str] = &[
    "ts", "n", "gap_s", "cpu_avg", "cpu_p95", "cpu_max", "busy_avg", "busy_max", "single", "mem_avg_mb", "mem_max_mb",
    "demand_mb", "swap_mb", "pressure", "gpu_avg", "gpu_max", "disk_used_gb", "disk_total_gb", "read_mb", "write_mb",
    "rx_mb", "tx_mb", "ac", "battery", "thermal", "displays", "top_cpu", "top_mem",
];

/// One reading of the machine - what a five-second sample contributes to its minute.
#[derive(Clone, Copy, Default)]
pub struct Reading {
    /// Load of all cores together, in percent of the whole machine.
    pub cpu_pct: f64,
    /// Cores that were at least half busy.
    pub busy_cores: u8,
    /// The busiest single core, in percent.
    pub max_core: f64,
    pub mem_used_mb: f64,
    /// What the machine WANTS: memory in use plus what was compressed or swapped away.
    pub demand_mb: f64,
    pub swap_mb: f64,
    pub vitals: Vitals,
}

/// One minute of the history - one line of the file.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Minute {
    /// Start of the minute, seconds since the epoch.
    pub ts: i64,
    /// Readings that went into it (12 for a full minute).
    pub n: u32,
    /// Seconds the machine was asleep or the recorder was stopped before this minute.
    pub gap_s: u32,
    pub cpu_avg: f64,
    pub cpu_p95: f64,
    pub cpu_max: f64,
    pub busy_avg: f64,
    pub busy_max: u8,
    /// Share of the readings in which ONE core was at its limit and the rest had room.
    pub single: f64,
    pub mem_avg_mb: f64,
    pub mem_max_mb: f64,
    pub demand_mb: f64,
    pub swap_mb: f64,
    pub pressure: Option<u8>,
    pub gpu_avg: Option<f64>,
    pub gpu_max: Option<f64>,
    pub disk_used_gb: f64,
    pub disk_total_gb: f64,
    pub read_mb: f64,
    pub write_mb: f64,
    pub rx_mb: f64,
    pub tx_mb: f64,
    /// Share of the minute on external power; None on a machine without a battery.
    pub ac: Option<f64>,
    pub battery: Option<u8>,
    pub thermal: Option<u8>,
    pub displays: Option<u8>,
    /// The largest consumers: (program, CPU seconds in this minute) and (program, MB).
    pub top_cpu: Vec<(String, f64)>,
    pub top_mem: Vec<(String, f64)>,
}

fn opt<T: std::fmt::Display>(value: Option<T>) -> String {
    value.map_or(String::new(), |v| v.to_string())
}

fn opt1(value: Option<f64>) -> String {
    value.map_or(String::new(), |v| format!("{v:.1}"))
}

/// A program name that cannot break the line format, and no longer than it needs to be.
pub fn safe_name(name: &str) -> String {
    let cleaned: String = name.chars().take(40)
        .map(|c| if c.is_control() || matches!(c, '\t' | '|' | ':') { '_' } else { c }).collect();
    if cleaned.is_empty() { "?".to_string() } else { cleaned }
}

fn tops(list: &[(String, f64)], decimals: usize) -> String {
    list.iter().map(|(name, value)| format!("{}:{:.*}", safe_name(name), decimals, value)).collect::<Vec<_>>().join("|")
}

fn parse_tops(field: &str) -> Vec<(String, f64)> {
    field.split('|').filter_map(|item| {
        let (name, value) = item.rsplit_once(':')?;
        Some((name.to_string(), value.parse().ok()?))
    }).collect()
}

impl Minute {
    pub fn to_line(&self) -> String {
        let mut line = String::with_capacity(240);
        let _ = write!(line, "{}\t{}\t{}\t{:.1}\t{:.1}\t{:.1}\t{:.1}\t{}\t{:.2}\t{:.0}\t{:.0}\t{:.0}\t{:.0}\t{}\t{}\t{}\t{:.1}\t{:.1}\t{:.1}\t{:.1}\t{:.1}\t{:.1}\t{}\t{}\t{}\t{}\t{}\t{}",
                       self.ts, self.n, self.gap_s, self.cpu_avg, self.cpu_p95, self.cpu_max, self.busy_avg, self.busy_max,
                       self.single, self.mem_avg_mb, self.mem_max_mb, self.demand_mb, self.swap_mb, opt(self.pressure),
                       opt1(self.gpu_avg), opt1(self.gpu_max), self.disk_used_gb, self.disk_total_gb, self.read_mb,
                       self.write_mb, self.rx_mb, self.tx_mb, self.ac.map_or(String::new(), |v| format!("{v:.2}")),
                       opt(self.battery), opt(self.thermal), opt(self.displays), tops(&self.top_cpu, 1), tops(&self.top_mem, 0));
        line
    }

    /// A line of the file back into a minute. None for a header, a damaged or a truncated
    /// line (a crash can leave half a line at the end of a file) - never a panic.
    pub fn parse(line: &str) -> Option<Minute> {
        if line.starts_with('#') {
            return None;
        }
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() < COLUMNS.len() {
            return None;
        }
        let num = |i: usize| f[i].parse::<f64>().ok();
        let maybe = |i: usize| if f[i].is_empty() { None } else { f[i].parse::<f64>().ok() };
        let small = |i: usize| if f[i].is_empty() { None } else { f[i].parse::<u8>().ok() };
        Some(Minute {
            ts: f[0].parse().ok()?, n: f[1].parse().ok()?, gap_s: f[2].parse().ok()?,
            cpu_avg: num(3)?, cpu_p95: num(4)?, cpu_max: num(5)?, busy_avg: num(6)?, busy_max: f[7].parse().ok()?,
            single: num(8)?, mem_avg_mb: num(9)?, mem_max_mb: num(10)?, demand_mb: num(11)?, swap_mb: num(12)?,
            pressure: small(13), gpu_avg: maybe(14), gpu_max: maybe(15), disk_used_gb: num(16)?, disk_total_gb: num(17)?,
            read_mb: num(18)?, write_mb: num(19)?, rx_mb: num(20)?, tx_mb: num(21)?, ac: maybe(22), battery: small(23),
            thermal: small(24), displays: small(25), top_cpu: parse_tops(f[26]), top_mem: parse_tops(f[27]),
        })
    }
}

/// The readings of the minute that is still running.
#[derive(Default)]
pub struct Aggregate {
    cpu: Vec<f64>,
    busy: Vec<u8>,
    single: u32,
    mem: Vec<f64>,
    demand_max: f64,
    swap_max: f64,
    pressure: Option<u8>,
    gpu: Vec<f64>,
    ac_on: u32,
    ac_seen: u32,
    battery: Option<u8>,
    thermal: Option<u8>,
    displays: Option<u8>,
    bytes: [u64; 4],
    pub gap_s: u32,
    cpu_by_name: HashMap<String, f64>,
    mem_by_name: HashMap<String, f64>,
}

fn larger(a: Option<u8>, b: Option<u8>) -> Option<u8> {
    match (a, b) {
        (Some(x), Some(y)) => Some(x.max(y)),
        (x, y) => x.or(y),
    }
}

fn ranked(map: &HashMap<String, f64>) -> Vec<(String, f64)> {
    let mut list: Vec<(String, f64)> = map.iter().map(|(k, v)| (k.clone(), *v)).collect();
    // Value descending, then name: two programs at the same value must not swap from minute to minute.
    list.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then_with(|| a.0.cmp(&b.0)));
    list.truncate(TOP_N);
    list
}

impl Aggregate {
    pub fn is_empty(&self) -> bool {
        self.cpu.is_empty()
    }

    /// Was the span since the last reading watched? If the machine slept or the process was
    /// stopped, the counters moved over a time nobody saw: the gap is noted and the reading
    /// is not taken - sleep must not read as idle.
    pub fn watched(&mut self, slept: i64) -> bool {
        if slept > GAP_FACTOR * SAMPLE_S {
            self.gap_s = self.gap_s.saturating_add(slept.min(u32::MAX as i64) as u32);
            return false;
        }
        true
    }

    pub fn add(&mut self, r: &Reading) {
        self.cpu.push(r.cpu_pct);
        self.busy.push(r.busy_cores);
        // One core at its limit while at most one other is busy: more cores would not help
        // this workload, a faster core would.
        if r.max_core >= 85.0 && r.busy_cores <= 2 {
            self.single += 1;
        }
        self.mem.push(r.mem_used_mb);
        self.demand_max = self.demand_max.max(r.demand_mb);
        self.swap_max = self.swap_max.max(r.swap_mb);
        self.pressure = larger(self.pressure, r.vitals.pressure);
        self.thermal = larger(self.thermal, r.vitals.thermal);
        self.displays = larger(self.displays, r.vitals.ext_displays);
        if let Some(pct) = r.vitals.gpu_pct {
            self.gpu.push(pct);
        }
        if let Some(on_ac) = r.vitals.on_ac {
            self.ac_seen += 1;
            self.ac_on += u32::from(on_ac);
        }
        self.battery = r.vitals.battery_pct.or(self.battery);
    }

    /// Bytes read, written, received and sent since the previous reading.
    pub fn add_io(&mut self, read: u64, written: u64, received: u64, sent: u64) {
        for (total, delta) in self.bytes.iter_mut().zip([read, written, received, sent]) {
            *total += delta;
        }
    }

    /// CPU seconds and resident megabytes by program name, from one scan of the process table.
    pub fn add_procs(&mut self, cpu: &[(String, f64)], mem: &[(String, f64)]) {
        for (name, seconds) in cpu {
            *self.cpu_by_name.entry(safe_name(name)).or_insert(0.0) += seconds;
        }
        for (name, mb) in mem {
            let slot = self.mem_by_name.entry(safe_name(name)).or_insert(0.0);
            *slot = slot.max(*mb);
        }
    }

    /// Close the minute that started at `ts` and start the next one.
    pub fn finish(&mut self, ts: i64, disk_used_gb: f64, disk_total_gb: f64) -> Minute {
        let n = self.cpu.len().max(1) as f64;
        let mut sorted = self.cpu.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let p95 = sorted.get(((0.95 * sorted.len() as f64).ceil() as usize).saturating_sub(1)).copied().unwrap_or(0.0);
        let mean = |v: &[f64]| if v.is_empty() { None } else { Some(v.iter().sum::<f64>() / v.len() as f64) };
        let minute = Minute {
            ts,
            n: self.cpu.len() as u32,
            gap_s: self.gap_s,
            cpu_avg: mean(&self.cpu).unwrap_or(0.0),
            cpu_p95: p95,
            cpu_max: sorted.last().copied().unwrap_or(0.0),
            busy_avg: self.busy.iter().map(|&b| b as f64).sum::<f64>() / n,
            busy_max: self.busy.iter().copied().max().unwrap_or(0),
            single: self.single as f64 / n,
            mem_avg_mb: mean(&self.mem).unwrap_or(0.0),
            mem_max_mb: self.mem.iter().cloned().fold(0.0, f64::max),
            demand_mb: self.demand_max,
            swap_mb: self.swap_max,
            pressure: self.pressure,
            gpu_avg: mean(&self.gpu),
            gpu_max: if self.gpu.is_empty() { None } else { Some(self.gpu.iter().cloned().fold(0.0, f64::max)) },
            disk_used_gb,
            disk_total_gb,
            read_mb: self.bytes[0] as f64 / MIB,
            write_mb: self.bytes[1] as f64 / MIB,
            rx_mb: self.bytes[2] as f64 / MIB,
            tx_mb: self.bytes[3] as f64 / MIB,
            ac: if self.ac_seen == 0 { None } else { Some(self.ac_on as f64 / self.ac_seen as f64) },
            battery: self.battery,
            thermal: self.thermal,
            displays: self.displays,
            top_cpu: ranked(&self.cpu_by_name),
            top_mem: ranked(&self.mem_by_name),
        };
        *self = Aggregate::default();
        minute
    }
}

/// One reading from two CPU snapshots and the current memory state.
pub fn reading(cores: &[CpuTimes], before: &[CpuTimes], mem: &sys::Mem, vitals: Vitals) -> Reading {
    let (mut busy_ticks, mut total_ticks, mut busy_cores, mut max_core) = (0u64, 0u64, 0u8, 0.0f64);
    for (now, prev) in cores.iter().zip(before) {
        let total = now.total.saturating_sub(prev.total);
        let busy = now.busy.saturating_sub(prev.busy);
        busy_ticks += busy;
        total_ticks += total;
        let pct = if total == 0 { 0.0 } else { (busy as f64 * 100.0 / total as f64).min(100.0) };
        max_core = max_core.max(pct);
        busy_cores += u8::from(pct >= 50.0);
    }
    let used = mem.total.saturating_sub(mem.available) as f64;
    // What was compressed still counts as wanted at its ORIGINAL size; what was swapped out
    // counts too. On a machine with enough memory both terms are zero and demand = used.
    let squeezed = mem.uncompressed.saturating_sub(mem.compressed) as f64;
    let demand = (used + squeezed).max(used + mem.swap_used as f64);
    Reading {
        cpu_pct: if total_ticks == 0 { 0.0 } else { (busy_ticks as f64 * 100.0 / total_ticks as f64).min(100.0) },
        busy_cores,
        max_core,
        mem_used_mb: used / MIB,
        demand_mb: demand / MIB,
        swap_mb: mem.swap_used as f64 / MIB,
        vitals,
    }
}

// ---------------------------------------------------------------------------------
// Where the history lives
// ---------------------------------------------------------------------------------

pub fn state_dir() -> PathBuf {
    let env = |name: &str| std::env::var_os(name).filter(|v| !v.is_empty()).map(PathBuf::from);
    env("TERMSTATS_STATE_DIR")
        .or_else(|| env("XDG_STATE_HOME").map(|d| d.join("termstats")))
        .unwrap_or_else(|| env("HOME").unwrap_or_else(|| PathBuf::from(".")).join(".local/state/termstats"))
}

pub fn history_dir(state: &Path) -> PathBuf {
    state.join("history")
}

fn local_date(ts: i64) -> String {
    unsafe {
        let t = ts as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&t, &mut tm);
        format!("{:04}-{:02}-{:02}", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday)
    }
}

pub fn now_wall() -> i64 {
    sys::now() as i64
}

/// The first line of every file: what machine these numbers are from.
pub fn header(host: &str, model: &str, chip: &str, ncpu: usize, mem_mb: u64) -> String {
    let clean = |s: &str| s.replace(['\t', '\n'], " ");
    format!("#{FORMAT}\thost={}\tmodel={}\tchip={}\tncpu={ncpu}\tmem_mb={mem_mb}\tos={}\n#{}\n",
            clean(host), clean(model), clean(chip), sys::OS_NAME, COLUMNS.join("\t"))
}

fn make_private_dir(dir: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
}

/// Append finished minutes to the file of their day. One write per file; a new file starts
/// with the header.
pub fn append(dir: &Path, head: &str, minutes: &[Minute]) -> std::io::Result<()> {
    make_private_dir(dir)?;
    let mut by_day: Vec<(String, String)> = Vec::new();
    for minute in minutes {
        let day = local_date(minute.ts);
        if by_day.last().map(|d| &d.0) != Some(&day) {
            by_day.push((day, String::new()));
        }
        let text = &mut by_day.last_mut().unwrap().1;
        text.push_str(&minute.to_line());
        text.push('\n');
    }
    for (day, text) in by_day {
        let path = dir.join(format!("{day}.tsv"));
        let fresh = !path.exists();
        let mut file = std::fs::OpenOptions::new().create(true).append(true).mode(0o600).open(&path)?;
        if fresh {
            file.write_all(head.as_bytes())?;
        }
        file.write_all(text.as_bytes())?;
    }
    Ok(())
}

/// The day files, oldest first.
fn day_files(dir: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir).map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default();
    files.retain(|p| p.extension().is_some_and(|e| e == "tsv"));
    files.sort();
    files
}

/// Delete day files older than `keep_days`. Returns how many went.
pub fn prune(dir: &Path, keep_days: i64, now: i64) -> usize {
    let oldest_kept = local_date(now - keep_days * 86400);
    let mut removed = 0;
    for path in day_files(dir) {
        let day = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        if day.len() == 10 && day < oldest_kept.as_str() && std::fs::remove_file(&path).is_ok() {
            removed += 1;
        }
    }
    removed
}

/// What the machine is, as the newest file header says.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Machine {
    pub host: String,
    pub model: String,
    pub chip: String,
    pub ncpu: usize,
    pub mem_mb: u64,
    pub os: String,
}

pub fn parse_header(line: &str) -> Option<Machine> {
    let rest = line.strip_prefix('#')?;
    let mut fields = rest.split('\t');
    if fields.next()? != FORMAT {
        return None;
    }
    let mut machine = Machine::default();
    for field in fields {
        let (key, value) = field.split_once('=')?;
        match key {
            "host" => machine.host = value.to_string(),
            "model" => machine.model = value.to_string(),
            "chip" => machine.chip = value.to_string(),
            "ncpu" => machine.ncpu = value.parse().ok()?,
            "mem_mb" => machine.mem_mb = value.parse().ok()?,
            "os" => machine.os = value.to_string(),
            _ => {}
        }
    }
    Some(machine)
}

pub struct History {
    pub machine: Option<Machine>,
    pub minutes: Vec<Minute>,
    pub bytes: u64,
    pub files: usize,
}

/// Every minute on disk from `since` on, oldest first. Unreadable lines are skipped.
pub fn load(dir: &Path, since: i64) -> History {
    let mut history = History { machine: None, minutes: Vec::new(), bytes: 0, files: 0 };
    let first_day = local_date(since);
    for path in day_files(dir) {
        let day = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string();
        if day.as_str() < first_day.as_str() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        history.files += 1;
        history.bytes += text.len() as u64;
        for line in text.lines() {
            if let Some(machine) = parse_header(line) {
                history.machine = Some(machine);
            } else if let Some(minute) = Minute::parse(line) {
                if minute.ts >= since {
                    history.minutes.push(minute);
                }
            }
        }
    }
    history.minutes.sort_by_key(|m| m.ts);
    history
}

/// Whole days between the first and the last recorded minute.
pub fn span_days(minutes: &[Minute]) -> i64 {
    match (minutes.first(), minutes.last()) {
        (Some(first), Some(last)) => (last.ts - first.ts) / 86400,
        _ => 0,
    }
}

// ---------------------------------------------------------------------------------
// The loop
// ---------------------------------------------------------------------------------

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_stop(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}

fn catch(signal: libc::c_int) {
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = on_stop as *const () as usize;
        libc::sigemptyset(&mut action.sa_mask);
        libc::sigaction(signal, &action, std::ptr::null_mut());
    }
}

/// Take the lock that says "a recorder is running". None when another one holds it.
pub fn lock(state: &Path) -> Option<std::fs::File> {
    make_private_dir(state).ok()?;
    let file = std::fs::OpenOptions::new().create(true).truncate(false).write(true).mode(0o600).open(state.join("recorder.lock")).ok()?;
    use std::os::unix::io::AsRawFd;
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 { Some(file) } else { None }
}

fn sleep_until(wall_s: i64) {
    loop {
        let left = wall_s as f64 - sys::now();
        if left <= 0.0 || STOP.load(Ordering::SeqCst) {
            return;
        }
        // One poll() with nothing to watch: the kernel wakes us at the deadline or for a signal.
        unsafe { libc::poll(std::ptr::null_mut(), 0, (left * 1000.0).ceil().min(60_000.0) as libc::c_int) };
    }
}

fn root_usage() -> (f64, f64) {
    const GIB: f64 = 1073741824.0;
    let disks = sys::disks();
    disks.iter().find(|d| d.mount == "/").or_else(|| disks.first())
        .map_or((0.0, 0.0), |d| (d.used as f64 / GIB, d.total as f64 / GIB))
}

/// Tell the user once that two weeks are on file.
fn announce_ready(state: &Path) {
    let marker = state.join("ready-notified");
    if marker.exists() || std::fs::write(&marker, "").is_err() {
        return;
    }
    let text = "Two weeks of history are recorded. Run: termstats-native recommend";
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("/usr/bin/osascript")
        .args(["-e", &format!("display notification \"{text}\" with title \"termstats\"")])
        .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status();
    #[cfg(not(target_os = "macos"))]
    let _ = std::process::Command::new("notify-send").args(["termstats", text])
        .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status();
}

struct Baseline {
    cores: Vec<CpuTimes>,
    net: Net,
    io: Option<(u64, u64)>,
    procs: Vec<(i32, u64)>,
}

/// Run until SIGTERM/SIGINT. Returns the process exit code.
pub fn run(state: &Path, keep_days: i64) -> i32 {
    let Some(_lock) = lock(state) else {
        eprintln!("termstats-native: a recorder is already running for {}", state.display());
        return 1;
    };
    for signal in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
        catch(signal);
    }
    let dir = history_dir(state);
    let (mut cores, mut net_buf, mut pids, mut samples): (Vec<CpuTimes>, Vec<u8>, Vec<i32>, Vec<ProcSample>) = Default::default();
    sys::cpu_times(&mut cores);
    let (model, chip) = sys::machine();
    let head = header(&sys::hostname(), &model, &chip, cores.len(), sys::memory().total / 1048576);
    let mut base = Baseline { cores: cores.clone(), net: sys::net(&mut net_buf), io: sys::disk_io(), procs: Vec::new() };
    let mut slow = sys::vitals(true);
    let mut agg = Aggregate::default();
    let mut pending: Vec<Minute> = Vec::new();
    let mut tick = 0u32;
    let mut last = now_wall();
    let mut minute = last / 60;
    let mut day = local_date(last);

    while !STOP.load(Ordering::SeqCst) {
        sleep_until((last / SAMPLE_S + 1) * SAMPLE_S);
        if STOP.load(Ordering::SeqCst) {
            break;
        }
        let now = now_wall();
        let slept = now - last;
        last = now;
        tick += 1;
        let scan = tick % PROCS_EVERY == 0;

        if now / 60 != minute {
            if !agg.is_empty() {
                let (used, total) = root_usage();
                pending.push(agg.finish(minute * 60, used, total));
            }
            minute = now / 60;
        }

        sys::cpu_times(&mut cores);
        let net = sys::net(&mut net_buf);
        let io = sys::disk_io();
        if agg.watched(slept) {
            let mut vitals = sys::vitals(scan);
            if scan {
                slow = vitals;
            }
            vitals.on_ac = slow.on_ac;
            vitals.battery_pct = slow.battery_pct;
            vitals.ext_displays = slow.ext_displays;
            agg.add(&reading(&cores, &base.cores, &sys::memory(), vitals));
            let (rd, wr) = match (io, base.io) {
                (Some(a), Some(b)) => (a.0.saturating_sub(b.0), a.1.saturating_sub(b.1)),
                _ => (0, 0),
            };
            agg.add_io(rd, wr, net.recv.saturating_sub(base.net.recv), net.sent.saturating_sub(base.net.sent));
        }
        base.cores.clone_from(&cores);
        base.net = net;
        base.io = io;

        if scan {
            sys::proc_scan(&mut pids, &mut samples);
            samples.sort_unstable_by_key(|s| s.pid);
            if !base.procs.is_empty() && slept <= GAP_FACTOR * SAMPLE_S * PROCS_EVERY as i64 {
                let mut used: Vec<(i32, f64)> = Vec::new();
                let mut earlier = base.procs.iter().peekable();
                for s in &samples {
                    while earlier.peek().is_some_and(|p| p.0 < s.pid) {
                        earlier.next();
                    }
                    if let Some(&&(pid, ns)) = earlier.peek() {
                        if pid == s.pid && s.cpu_ns > ns {
                            used.push((s.pid, (s.cpu_ns - ns) as f64 / 1e9));
                        }
                    }
                }
                used.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                let cpu: Vec<(String, f64)> = used.iter().take(TOP_N + 2).map(|&(pid, s)| (sys::proc_name(pid), s)).collect();
                let mut by_rss: Vec<&ProcSample> = samples.iter().collect();
                by_rss.sort_by_key(|s| std::cmp::Reverse(s.rss));
                let mem: Vec<(String, f64)> = by_rss.iter().take(TOP_N + 2).map(|s| (sys::proc_name(s.pid), s.rss as f64 / MIB)).collect();
                agg.add_procs(&cpu, &mem);
            }
            base.procs.clear();
            base.procs.extend(samples.iter().map(|s| (s.pid, s.cpu_ns)));
        }

        if pending.len() >= FLUSH_MINUTES {
            if let Err(error) = append(&dir, &head, &pending) {
                eprintln!("termstats-native: cannot write the history: {error}");
            }
            pending.clear();
            let today = local_date(now);
            if today != day {
                day = today;
                prune(&dir, keep_days, now);
            }
            let history = load(&dir, now - (READY_DAYS + 2) * 86400);
            if span_days(&history.minutes) >= READY_DAYS {
                announce_ready(state);
            }
        }
    }
    if !agg.is_empty() {
        let (used, total) = root_usage();
        pending.push(agg.finish(minute * 60, used, total));
    }
    if let Err(error) = append(&dir, &head, &pending) {
        eprintln!("termstats-native: cannot write the history: {error}");
        return 1;
    }
    0
}

// ---------------------------------------------------------------------------------
// The service: started at login, restarted if it dies
// ---------------------------------------------------------------------------------

pub const SERVICE_LABEL: &str = "io.celox.termstats.recorder";

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

pub fn service_file() -> PathBuf {
    if cfg!(target_os = "macos") {
        home().join("Library/LaunchAgents").join(format!("{SERVICE_LABEL}.plist"))
    } else {
        home().join(".config/systemd/user/termstats-recorder.service")
    }
}

fn xml(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// The launchd agent: background priority, low-priority disk access, kept alive.
pub fn launchd_plist(program: &str, state: &str) -> String {
    format!(r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array><string>{}</string><string>record</string></array>
    <key>EnvironmentVariables</key>
    <dict><key>TERMSTATS_STATE_DIR</key><string>{}</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardErrorPath</key><string>{}/recorder.log</string>
</dict>
</plist>
"#, xml(program), xml(state), xml(state))
}

pub fn systemd_unit(program: &str, state: &str) -> String {
    format!("[Unit]\nDescription=termstats recorder: one line of system history a minute\n\n[Service]\nExecStart={program} record\nEnvironment=TERMSTATS_STATE_DIR={state}\nRestart=always\nRestartSec=30\nNice=19\nIOSchedulingClass=idle\n\n[Install]\nWantedBy=default.target\n")
}

fn command(program: &str, args: &[&str]) -> bool {
    std::process::Command::new(program).args(args)
        .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
        .status().is_ok_and(|s| s.success())
}

pub fn install(state: &Path) -> Result<String, String> {
    let program = std::env::current_exe().and_then(|p| p.canonicalize()).map_err(|e| format!("cannot find my own path: {e}"))?;
    let (program, state_text) = (program.to_string_lossy().into_owned(), state.to_string_lossy().into_owned());
    make_private_dir(state).map_err(|e| format!("cannot create {}: {e}", state.display()))?;
    let file = service_file();
    std::fs::create_dir_all(file.parent().unwrap()).map_err(|e| e.to_string())?;
    let uid = unsafe { libc::getuid() };
    if cfg!(target_os = "macos") {
        std::fs::write(&file, launchd_plist(&program, &state_text)).map_err(|e| e.to_string())?;
        let (domain, path) = (format!("gui/{uid}"), file.to_string_lossy().into_owned());
        command("/bin/launchctl", &["bootout", &format!("{domain}/{SERVICE_LABEL}")]);      // an older copy, if any
        if !command("/bin/launchctl", &["bootstrap", &domain, &path]) {
            return Err(format!("launchctl could not load {path}"));
        }
    } else {
        std::fs::write(&file, systemd_unit(&program, &state_text)).map_err(|e| e.to_string())?;
        command("systemctl", &["--user", "daemon-reload"]);
        if !command("systemctl", &["--user", "enable", "--now", "termstats-recorder.service"]) {
            return Err(format!("systemctl --user could not start the unit {}", file.display()));
        }
    }
    Ok(format!("Recorder installed and started.\n  service  {}\n  history  {}\n  program  {program}", file.display(), history_dir(state).display()))
}

pub fn uninstall() -> Result<String, String> {
    let file = service_file();
    if cfg!(target_os = "macos") {
        let uid = unsafe { libc::getuid() };
        command("/bin/launchctl", &["bootout", &format!("gui/{uid}/{SERVICE_LABEL}")]);
    } else {
        command("systemctl", &["--user", "disable", "--now", "termstats-recorder.service"]);
    }
    if file.exists() {
        std::fs::remove_file(&file).map_err(|e| e.to_string())?;
    }
    Ok("Recorder stopped and removed. The history is still there; 'record purge' deletes it.".to_string())
}

pub fn purge(state: &Path) -> Result<String, String> {
    let dir = history_dir(state);
    let files = day_files(&dir);
    for path in &files {
        std::fs::remove_file(path).map_err(|e| e.to_string())?;
    }
    let _ = std::fs::remove_file(state.join("ready-notified"));
    Ok(format!("Deleted {} day file(s) from {}.", files.len(), dir.display()))
}

pub fn status(state: &Path) -> String {
    let now = now_wall();
    let history = load(&history_dir(state), 0);
    let running = lock(state).is_none();
    let mut out = String::new();
    let _ = writeln!(out, "recorder   {}", if running { "running" } else { "not running" });
    let _ = writeln!(out, "service    {}", if service_file().exists() { "installed (starts at login)" } else { "not installed  ->  termstats-native record install" });
    let _ = writeln!(out, "history    {}", history_dir(state).display());
    if history.minutes.is_empty() {
        let _ = writeln!(out, "recorded   nothing yet (the first lines are written after five minutes)");
        return out;
    }
    let (first, last) = (history.minutes[0].ts, history.minutes[history.minutes.len() - 1].ts);
    let days = span_days(&history.minutes);
    let wall_minutes = ((last - first) / 60 + 1).max(1);
    let _ = writeln!(out, "recorded   {} minutes in {} file(s), {:.1} MB", history.minutes.len(), history.files, history.bytes as f64 / MIB);
    let _ = writeln!(out, "span       {} to {}  ({:.0} % of that time the machine was awake and recorded)",
                     local_date(first), local_date(last), history.minutes.len() as f64 * 100.0 / wall_minutes as f64);
    if days >= READY_DAYS {
        let _ = writeln!(out, "ready      yes - {days} days on file  ->  termstats-native recommend");
    } else {
        let _ = writeln!(out, "ready      day {} of {READY_DAYS}  (a first impression: termstats-native recommend --now)", days + 1);
    }
    if !running && now - last > 600 {
        let _ = writeln!(out, "note       the last line is {} minutes old", (now - last) / 60);
    }
    out
}

#[cfg(test)]
mod tests;
