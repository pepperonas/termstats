//! From two weeks of history to a computer.
//!
//! Three steps, each of them a pure function so it can be tested without a machine:
//! `profile` reduces the minutes to what the machine WANTED (memory at its 99th percentile,
//! load, graphics, storage, how mobile it was); `need` turns that into requirements at one
//! of three levels of headroom; `pick` finds the cheapest configuration in the catalogue
//! that meets them. The report says which numbers of the history each conclusion rests on -
//! a recommendation nobody can check is an advertisement.
//!
//! What this cannot know: the software someone needs (a program that only runs on one
//! system), ports, a screen size someone simply prefers. It says so.

use crate::catalog::{Chip, Device, Variant};
use crate::record::{Machine, Minute};
use std::collections::HashMap;
use std::fmt::Write as _;

/// The three levels of headroom a recommendation comes in.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Tier {
    Tight,
    Fit,
    Reserve,
}

pub const TIERS: [Tier; 3] = [Tier::Tight, Tier::Fit, Tier::Reserve];

impl Tier {
    pub fn label(self) -> &'static str {
        match self {
            Tier::Tight => "just enough",
            Tier::Fit => "fits",
            Tier::Reserve => "with reserve",
        }
    }

    /// Memory wanted, as a multiple of the 99th percentile of the measured demand.
    fn ram_factor(self) -> f64 {
        [1.10, 1.30, 1.80][self as usize]
    }

    /// The load the busy times (95th percentile) should put on the new machine, in percent.
    fn cpu_target(self) -> f64 {
        [80.0, 60.0, 40.0][self as usize]
    }

    /// Where the machine was saturated the measurement is cut off at 100 %: the true demand
    /// is unknown, so at least this multiple of the current chip is asked for.
    fn saturated_floor(self) -> f64 {
        [1.1, 1.4, 2.0][self as usize]
    }

    fn ssd_factor(self) -> f64 {
        [1.25, 1.5, 2.0][self as usize]
    }

    /// Years of the measured growth that are added to the storage in use.
    fn growth_years(self) -> f64 {
        [1.0, 2.0, 4.0][self as usize]
    }
}

/// What the history says, in the units a person reads.
#[derive(Clone, Debug, Default)]
pub struct Profile {
    pub minutes: usize,
    pub span_days: i64,
    pub days: usize,
    pub first_ts: i64,
    pub last_ts: i64,
    /// Share of the wall-clock span that was recorded (the rest: asleep or switched off).
    pub awake_share: f64,
    pub ram_p99_gb: f64,
    pub ram_max_gb: f64,
    pub installed_gb: f64,
    /// Days on which the demand went beyond the installed memory.
    pub days_over_ram: usize,
    pub pressure_share: Option<f64>,
    pub swap_p95_gb: f64,
    pub cpu_p95: f64,
    pub saturated_share: f64,
    /// Runs of ten minutes or more at 70 % load and above.
    pub sustained_runs: usize,
    pub sustained_share: f64,
    pub busy_p95: f64,
    pub single_share: f64,
    pub gpu_p95: Option<f64>,
    pub gpu_heavy_share: Option<f64>,
    pub disk_used_gb: f64,
    pub disk_total_gb: f64,
    pub disk_growth_gb_day: f64,
    pub battery_share: Option<f64>,
    pub display_share: Option<f64>,
    pub thermal_share: Option<f64>,
    /// (program, CPU hours) and (program, peak GB), largest first.
    pub top_cpu: Vec<(String, f64)>,
    pub top_mem: Vec<(String, f64)>,
}

fn percentile(values: &mut [f64], p: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    values[((p * values.len() as f64).ceil() as usize).clamp(1, values.len()) - 1]
}

fn share(minutes: &[Minute], known: impl Fn(&Minute) -> Option<bool>) -> Option<f64> {
    let (mut yes, mut all) = (0usize, 0usize);
    for m in minutes {
        if let Some(hit) = known(m) {
            all += 1;
            yes += usize::from(hit);
        }
    }
    if all == 0 { None } else { Some(yes as f64 / all as f64) }
}

fn top(map: HashMap<String, f64>, n: usize) -> Vec<(String, f64)> {
    let mut list: Vec<(String, f64)> = map.into_iter().collect();
    list.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then_with(|| a.0.cmp(&b.0)));
    list.truncate(n);
    list
}

pub fn profile(minutes: &[Minute], machine: &Machine) -> Profile {
    let mut p = Profile { minutes: minutes.len(), installed_gb: machine.mem_mb as f64 / 1024.0, ..Profile::default() };
    let (Some(first), Some(last)) = (minutes.first(), minutes.last()) else { return p };
    p.first_ts = first.ts;
    p.last_ts = last.ts;
    p.span_days = (last.ts - first.ts) / 86400;
    p.awake_share = minutes.len() as f64 / (((last.ts - first.ts) / 60 + 1).max(1)) as f64;

    let mut demand: Vec<f64> = minutes.iter().map(|m| m.demand_mb / 1024.0).collect();
    p.ram_max_gb = demand.iter().cloned().fold(0.0, f64::max);
    p.ram_p99_gb = percentile(&mut demand, 0.99);
    let mut swap: Vec<f64> = minutes.iter().map(|m| m.swap_mb / 1024.0).collect();
    p.swap_p95_gb = percentile(&mut swap, 0.95);
    p.pressure_share = share(minutes, |m| m.pressure.map(|level| level >= 1));

    // Days are counted in UTC here: only "how many of them", never which one, is reported.
    let mut day_peak: HashMap<i64, f64> = HashMap::new();
    for m in minutes {
        let peak = day_peak.entry(m.ts.div_euclid(86400)).or_insert(0.0);
        *peak = peak.max(m.demand_mb / 1024.0);
    }
    p.days = day_peak.len();
    p.days_over_ram = day_peak.values().filter(|&&gb| p.installed_gb > 0.0 && gb > p.installed_gb).count();

    let mut load: Vec<f64> = minutes.iter().map(|m| m.cpu_avg).collect();
    p.cpu_p95 = percentile(&mut load, 0.95);
    p.saturated_share = minutes.iter().filter(|m| m.cpu_avg >= 80.0).count() as f64 / minutes.len() as f64;
    let mut busy: Vec<f64> = minutes.iter().map(|m| m.busy_max as f64).collect();
    p.busy_p95 = percentile(&mut busy, 0.95);
    p.single_share = minutes.iter().map(|m| m.single).sum::<f64>() / minutes.len() as f64;
    // Sustained load: consecutive minutes (no gap between them) at 70 % and above.
    let (mut run, mut in_runs) = (0usize, 0usize);
    let close = |run: &mut usize, runs: &mut usize, in_runs: &mut usize| {
        if *run >= 10 {
            *runs += 1;
            *in_runs += *run;
        }
        *run = 0;
    };
    let mut previous = first.ts - 60;
    for m in minutes {
        if m.cpu_avg >= 70.0 && m.ts - previous <= 120 {
            run += 1;
        } else {
            close(&mut run, &mut p.sustained_runs, &mut in_runs);
            run = usize::from(m.cpu_avg >= 70.0);
        }
        previous = m.ts;
    }
    close(&mut run, &mut p.sustained_runs, &mut in_runs);
    p.sustained_share = in_runs as f64 / minutes.len() as f64;

    let mut gpu: Vec<f64> = minutes.iter().filter_map(|m| m.gpu_max).collect();
    if !gpu.is_empty() {
        p.gpu_p95 = Some(percentile(&mut gpu, 0.95));
        p.gpu_heavy_share = share(minutes, |m| m.gpu_avg.map(|avg| avg >= 80.0));
    }

    p.disk_used_gb = last.disk_used_gb;
    p.disk_total_gb = last.disk_total_gb;
    let days = (last.ts - first.ts) as f64 / 86400.0;
    if days >= 1.0 {
        p.disk_growth_gb_day = ((last.disk_used_gb - first.disk_used_gb) / days).max(0.0);
    }
    p.battery_share = share(minutes, |m| m.ac.map(|ac| ac < 0.5));
    p.display_share = share(minutes, |m| m.displays.map(|n| n >= 1));
    p.thermal_share = share(minutes, |m| m.thermal.map(|level| level >= 2));

    let (mut cpu, mut mem): (HashMap<String, f64>, HashMap<String, f64>) = Default::default();
    for m in minutes {
        for (name, seconds) in &m.top_cpu {
            *cpu.entry(name.clone()).or_insert(0.0) += seconds / 3600.0;
        }
        for (name, mb) in &m.top_mem {
            let slot = mem.entry(name.clone()).or_insert(0.0);
            *slot = slot.max(mb / 1024.0);
        }
    }
    p.top_cpu = top(cpu, 5);
    p.top_mem = top(mem, 5);
    p
}

/// What a machine has to bring, at one level of headroom.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Need {
    pub ram_gb: f64,
    pub ssd_gb: f64,
    /// Geekbench-6-like scores; 0 = not rated (the current chip is not in the catalogue).
    pub multi: f64,
    pub single: f64,
    pub gpu: f64,
    /// It was used away from a socket: only laptops qualify.
    pub portable: bool,
    /// Sustained load or heat: a machine without a fan would throttle.
    pub no_fanless: bool,
}

pub fn need(p: &Profile, current: Option<&Chip>, tier: Tier) -> Need {
    let mut need = Need { ram_gb: p.ram_p99_gb * tier.ram_factor(), ..Need::default() };
    // Two years of growth cannot be read off two weeks with any confidence: it is damped,
    // and never allowed to outgrow what is in use today.
    let growth = (p.disk_growth_gb_day * 365.0 * tier.growth_years() * 0.5).min(p.disk_used_gb);
    need.ssd_gb = (p.disk_used_gb + growth) * tier.ssd_factor();
    if let Some(chip) = current {
        let mut factor = p.cpu_p95 / tier.cpu_target();
        if p.saturated_share > 0.05 {
            factor = factor.max(tier.saturated_floor());
        }
        need.multi = chip.multi as f64 * factor.clamp(0.3, 3.0);
        // A single-core bound workload gains from faster cores only; otherwise today's are the floor.
        need.single = chip.single as f64 * if p.single_share > 0.10 && tier != Tier::Tight { 1.2 } else { 0.9 };
        if let Some(gpu_p95) = p.gpu_p95 {
            let mut factor = gpu_p95 / tier.cpu_target();
            if p.gpu_heavy_share.unwrap_or(0.0) > 0.05 {
                factor = factor.max(tier.saturated_floor());
            }
            need.gpu = chip.gpu as f64 * factor.clamp(0.2, 3.0);
        }
    }
    need.portable = p.battery_share.unwrap_or(0.0) >= 0.05;
    need.no_fanless = p.sustained_share >= 0.05 || p.thermal_share.unwrap_or(0.0) >= 0.02;
    need
}

/// One purchasable configuration.
#[derive(Clone, Debug, PartialEq)]
pub struct Choice {
    pub device: &'static str,
    pub vendor: &'static str,
    pub name: &'static str,
    pub form: &'static str,
    pub chip: &'static str,
    pub ram_gb: u32,
    pub ssd_gb: u32,
    pub price_eur: u32,
    pub verified: bool,
    pub source: &'static str,
    multi: u32,
}

fn smallest_step(steps: &[crate::catalog::Step], wanted: f64) -> Option<(u32, u32)> {
    steps.iter().filter(|s| s.gb as f64 >= wanted).min_by_key(|s| s.gb).map(|s| (s.gb, s.plus_eur))
}

pub fn chip_by_id<'a>(chips: &'a [Chip], id: &str) -> Option<&'a Chip> {
    chips.iter().find(|c| c.id == id)
}

/// The chip of the machine the history is from. An exact brand match comes first ("Apple
/// M1 Pro" must not be taken for an "Apple M1" that happens to have as many cores), then
/// the core count picks among chips of one brand (an M1 Pro came with 8 or 10). Only when
/// no brand matches exactly - Linux reports "AMD Ryzen 9 7950X 16-Core Processor" - is the
/// longest brand contained in the machine's own name taken.
pub fn current_chip<'a>(chips: &'a [Chip], machine: &Machine) -> Option<&'a Chip> {
    // The longest brand the machine's name contains: "Apple M1 Pro" is an M1 Pro, not an M1.
    let longest = chips.iter().filter(|c| !c.brand.is_empty() && machine.chip.contains(c.brand)).map(|c| c.brand.len()).max()?;
    let family: Vec<&Chip> = chips.iter().filter(|c| c.brand.len() == longest && machine.chip.contains(c.brand)).collect();
    family.iter().find(|c| (c.perf_cores + c.eff_cores) as usize == machine.ncpu).or(family.first()).copied()
}

fn config(device: &'static Device, variant: &'static Variant, chip: &Chip, need: &Need) -> Option<Choice> {
    if need.portable && device.form != "laptop" || need.no_fanless && device.fanless {
        return None;
    }
    if (chip.multi as f64) < need.multi || (chip.single as f64) < need.single || (chip.gpu as f64) < need.gpu {
        return None;
    }
    let (ram_gb, ram_eur) = smallest_step(variant.ram, need.ram_gb)?;
    let (ssd_gb, ssd_eur) = smallest_step(variant.ssd, need.ssd_gb)?;
    Some(Choice { device: device.id, vendor: device.vendor, name: device.name, form: device.form, chip: chip.name,
                  ram_gb, ssd_gb, price_eur: variant.base_price_eur + ram_eur + ssd_eur, verified: device.verified,
                  source: device.source, multi: chip.multi })
}

/// The cheapest configuration of the given system that meets the need; among equally
/// priced ones the faster.
pub fn pick(devices: &'static [Device], chips: &[Chip], need: &Need, os: &str, laptops_only: bool) -> Option<Choice> {
    let mut best: Option<Choice> = None;
    for device in devices.iter().filter(|d| d.os == os && (!laptops_only || d.form == "laptop")) {
        for variant in device.variants {
            let Some(chip) = chip_by_id(chips, variant.chip) else { continue };
            let Some(candidate) = config(device, variant, chip, need) else { continue };
            let better = best.as_ref().is_none_or(|b| (candidate.price_eur, std::cmp::Reverse(candidate.multi)) < (b.price_eur, std::cmp::Reverse(b.multi)));
            if better {
                best = Some(candidate);
            }
        }
    }
    best
}

/// Does the machine the history is from already meet a need?
pub fn current_meets(p: &Profile, need: &Need, current: Option<&Chip>) -> (bool, Vec<&'static str>) {
    let mut short = Vec::new();
    if p.installed_gb + 0.5 < need.ram_gb {
        short.push("memory");
    }
    if p.disk_total_gb > 0.0 && p.disk_total_gb < need.ssd_gb {
        short.push("storage");
    }
    if let Some(chip) = current {
        if (chip.multi as f64) < need.multi {
            short.push("processor");
        }
        if (chip.gpu as f64) < need.gpu {
            short.push("graphics");
        }
    }
    (short.is_empty(), short)
}

// ---------------------------------------------------------------------------------
// The report
// ---------------------------------------------------------------------------------

pub struct Report<'a> {
    pub profile: &'a Profile,
    pub machine: &'a Machine,
    pub current: Option<&'a Chip>,
    pub needs: [Need; 3],
    pub picks: [Option<Choice>; 3],
    /// The fitting tier as a laptop, when the pick itself is a desktop.
    pub portable: Option<Choice>,
    pub linux: Option<Choice>,
    pub catalogue: &'a str,
    pub catalogue_age_days: i64,
    pub ready: bool,
}

pub fn build<'a>(p: &'a Profile, machine: &'a Machine, chips: &'a [Chip], devices: &'static [Device], catalogue: &'a str,
                 catalogue_age_days: i64, ready: bool) -> Report<'a> {
    let current = current_chip(chips, machine);
    let needs = TIERS.map(|tier| need(p, current, tier));
    let picks = [0, 1, 2].map(|i| pick(devices, chips, &needs[i], "macOS", false));
    let portable = match &picks[1] {
        Some(choice) if choice.form != "laptop" => pick(devices, chips, &needs[1], "macOS", true),
        _ => None,
    };
    let linux = pick(devices, chips, &needs[1], "Linux", needs[1].portable);
    Report { profile: p, machine, current, needs, picks, portable, linux, catalogue, catalogue_age_days, ready }
}

fn euro(amount: u32) -> String {
    let digits = amount.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push('.');
        }
        out.push(ch);
    }
    out + " EUR"
}

fn storage(gb: u32) -> String {
    // Makers size storage in powers of two (1024, 2048) or of ten (1000, 2000): both are "1 TB", "2 TB".
    if gb >= 1000 {
        let unit = if gb % 1024 == 0 { 1024.0 } else { 1000.0 };
        format!("{} TB", (gb as f64 / unit * 10.0).round() / 10.0)
    } else {
        format!("{gb} GB")
    }
}

fn pct(share: f64) -> String {
    let value = share * 100.0;
    if value > 0.0 && value < 1.0 { "under 1 %".to_string() } else { format!("{value:.0} %") }
}

fn date(ts: i64) -> String {
    unsafe {
        let t = ts as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&t, &mut tm);
        format!("{:04}-{:02}-{:02}", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday)
    }
}

fn describe(choice: &Choice) -> String {
    format!("{} {} - {}, {} GB memory, {} storage", choice.vendor, choice.name, choice.chip, choice.ram_gb, storage(choice.ssd_gb))
}

/// Why this tier asks for what it asks, in the history's own numbers.
fn reasons(p: &Profile, need: &Need, tier: Tier, current: Option<&Chip>) -> String {
    let mut parts = vec![format!("memory {:.0} GB = {:.1} GB wanted at the 99th percentile x {:.2}", need.ram_gb.ceil(), p.ram_p99_gb, tier.ram_factor())];
    parts.push(format!("storage {:.0} GB = {:.0} GB in use x {:.2} plus growth", need.ssd_gb.ceil(), p.disk_used_gb, tier.ssd_factor()));
    if let Some(chip) = current {
        parts.push(format!("processor at least {:.0} % of the current {}", need.multi * 100.0 / chip.multi.max(1) as f64, chip.brand));
    }
    parts.join("; ")
}

pub fn text(r: &Report) -> String {
    let p = r.profile;
    let mut out = String::new();
    let _ = writeln!(out, "termstats recommendation");
    let _ = writeln!(out, "  history    {} to {}: {} days, {} minutes recorded ({} of that time awake)",
                     date(p.first_ts), date(p.last_ts), p.span_days, p.minutes, pct(p.awake_share));
    let _ = writeln!(out, "  machine    {} - {}, {} cores, {:.0} GB memory, {:.0} GB storage",
                     if r.machine.model.is_empty() { "this computer" } else { &r.machine.model }, r.machine.chip, r.machine.ncpu, p.installed_gb, p.disk_total_gb);
    let _ = writeln!(out, "  catalogue  {}{}", r.catalogue,
                     if r.catalogue_age_days > 120 { format!("  - {} days old: models and prices may have changed, update termstats", r.catalogue_age_days) } else { String::new() });
    if !r.ready {
        let _ = writeln!(out, "\n  PROVISIONAL: {} of {} days are on file. A week has weekdays and a weekend, a fortnight has two;",
                         p.span_days, crate::record::READY_DAYS);
        let _ = writeln!(out, "  what is below is a first impression of the days recorded so far, not the recommendation.");
    }

    let _ = writeln!(out, "\nWhat the history says");
    let _ = writeln!(out, "  memory     wanted {:.1} GB at the 99th percentile (peak {:.1} GB), installed {:.0} GB; beyond the installed memory on {} of {} days{}",
                     p.ram_p99_gb, p.ram_max_gb, p.installed_gb, p.days_over_ram, p.days,
                     p.pressure_share.map_or(String::new(), |s| format!("; under memory pressure {} of the time", pct(s))));
    let _ = writeln!(out, "  processor  95 % of the time below {:.0} % load on {} cores; at 80 % and above {} of the time; {} sustained peak(s) of ten minutes or more; single-core bound {}",
                     p.cpu_p95, r.machine.ncpu, pct(p.saturated_share), p.sustained_runs, pct(p.single_share));
    match (p.gpu_p95, p.gpu_heavy_share) {
        (Some(p95), Some(heavy)) => { let _ = writeln!(out, "  graphics   95 % of the time below {:.0} %; at 80 % and above {} of the time", p95, pct(heavy)); }
        _ => { let _ = writeln!(out, "  graphics   not reported by this machine - not rated"); }
    }
    let _ = writeln!(out, "  storage    {:.0} of {:.0} GB in use ({:.0} %), growing {:.2} GB a day",
                     p.disk_used_gb, p.disk_total_gb, if p.disk_total_gb > 0.0 { p.disk_used_gb * 100.0 / p.disk_total_gb } else { 0.0 }, p.disk_growth_gb_day);
    let mobility = match p.battery_share {
        Some(battery) => format!("on battery {} of the time", pct(battery)),
        None => "no battery".to_string(),
    };
    let _ = writeln!(out, "  mobility   {}{}{}", mobility,
                     p.display_share.map_or(String::new(), |s| format!("; an external display {} of the time", pct(s))),
                     p.thermal_share.map_or(String::new(), |s| format!("; throttled by heat {} of the time", pct(s))));
    if !p.top_cpu.is_empty() {
        let list: Vec<String> = p.top_cpu.iter().map(|(name, hours)| format!("{name} {hours:.1} h")).collect();
        let _ = writeln!(out, "  busiest    {}  (CPU time)", list.join(", "));
    }
    if !p.top_mem.is_empty() {
        let list: Vec<String> = p.top_mem.iter().map(|(name, gb)| format!("{name} {gb:.1} GB")).collect();
        let _ = writeln!(out, "  largest    {}  (peak memory)", list.join(", "));
    }

    let (enough, short) = current_meets(p, &r.needs[1], r.current);
    let _ = writeln!(out, "\nThis machine");
    if enough {
        let _ = writeln!(out, "  It fits what was measured. Nothing below is needed; it shows what the same work would cost new.");
    } else {
        let _ = writeln!(out, "  Short on: {}.", short.join(", "));
    }
    if r.current.is_none() {
        let _ = writeln!(out, "  Its chip ({}) is not in the catalogue, so processor and graphics could not be compared: the picks below rest on memory, storage and mobility only.", r.machine.chip);
    }

    let _ = writeln!(out, "\nRecommendation (macOS){}",
                     if r.needs[1].portable { "  - used on battery, so laptops only" } else { "  - never used on battery, so desktops qualify" });
    for (i, tier) in TIERS.iter().enumerate() {
        match &r.picks[i] {
            Some(choice) => {
                let _ = writeln!(out, "  {:<14}{}  -  {}{}", tier.label(), describe(choice), euro(choice.price_eur), if choice.verified { "" } else { "  (price not from the maker's own page)" });
            }
            None => { let _ = writeln!(out, "  {:<14}nothing in the catalogue meets this", tier.label()); }
        }
        let _ = writeln!(out, "  {:<14}{}", "", reasons(p, &r.needs[i], *tier, r.current));
    }
    if r.needs[1].no_fanless {
        let _ = writeln!(out, "  Machines without a fan are left out: the load was sustained ({} of the time in runs of ten minutes or more).", pct(p.sustained_share));
    }
    if let Some(choice) = &r.portable {
        let _ = writeln!(out, "  as a laptop   {}  -  {}  (the fitting tier, if it has to move)", describe(choice), euro(choice.price_eur));
    }
    let _ = writeln!(out, "\nRunning Linux instead (the fitting tier)");
    match &r.linux {
        Some(choice) => { let _ = writeln!(out, "  {}  -  {}{}", describe(choice), euro(choice.price_eur), if choice.verified { "" } else { "  (price not from the maker's own page)" }); }
        None => { let _ = writeln!(out, "  nothing in the catalogue meets this"); }
    }

    let _ = writeln!(out, "\nHow to read this");
    let _ = writeln!(out, "  The cheapest configuration that meets each level is named; prices are list prices in euro from the catalogue of {}.", r.catalogue);
    let _ = writeln!(out, "  Processor and graphics are compared through published benchmark scores - an approximation, good for classes, not for percent.");
    let _ = writeln!(out, "  Not measured: software that needs one particular system, ports, screen size, taste. Those are yours to weigh.");
    out
}

fn json_string(text: &str) -> String {
    let mut out = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 => { let _ = write!(out, "\\u{:04x}", c as u32); }
            c => out.push(c),
        }
    }
    out + "\""
}

fn json_choice(choice: &Option<Choice>) -> String {
    match choice {
        None => "null".to_string(),
        Some(c) => format!("{{\"device\":{},\"vendor\":{},\"name\":{},\"form\":{},\"chip\":{},\"ram_gb\":{},\"ssd_gb\":{},\"price_eur\":{},\"verified\":{},\"source\":{}}}",
                           json_string(c.device), json_string(c.vendor), json_string(c.name), json_string(c.form), json_string(c.chip),
                           c.ram_gb, c.ssd_gb, c.price_eur, c.verified, json_string(c.source)),
    }
}

fn json_opt(value: Option<f64>) -> String {
    value.map_or("null".to_string(), |v| format!("{v:.4}"))
}

pub fn json(r: &Report) -> String {
    let p = r.profile;
    let tiers: Vec<String> = TIERS.iter().enumerate().map(|(i, tier)| {
        let n = &r.needs[i];
        format!("{{\"tier\":{},\"need\":{{\"ram_gb\":{:.1},\"ssd_gb\":{:.0},\"multi\":{:.0},\"gpu\":{:.0},\"portable\":{},\"no_fanless\":{}}},\"pick\":{}}}",
                json_string(tier.label()), n.ram_gb, n.ssd_gb, n.multi, n.gpu, n.portable, n.no_fanless, json_choice(&r.picks[i]))
    }).collect();
    let tops = |list: &[(String, f64)]| list.iter().map(|(name, v)| format!("[{},{:.2}]", json_string(name), v)).collect::<Vec<_>>().join(",");
    format!("{{\"ready\":{},\"catalogue\":{},\"machine\":{{\"model\":{},\"chip\":{},\"ncpu\":{},\"mem_gb\":{:.0},\"chip_known\":{}}},\"history\":{{\"first\":{},\"last\":{},\"days\":{},\"minutes\":{},\"awake_share\":{:.4}}},\"profile\":{{\"ram_p99_gb\":{:.2},\"ram_max_gb\":{:.2},\"days_over_ram\":{},\"pressure_share\":{},\"cpu_p95\":{:.1},\"saturated_share\":{:.4},\"sustained_runs\":{},\"single_share\":{:.4},\"gpu_p95\":{},\"disk_used_gb\":{:.1},\"disk_total_gb\":{:.1},\"disk_growth_gb_day\":{:.3},\"battery_share\":{},\"display_share\":{},\"thermal_share\":{},\"top_cpu_hours\":[{}],\"top_mem_gb\":[{}]}},\"tiers\":[{}],\"portable\":{},\"linux\":{}}}\n",
            r.ready, json_string(r.catalogue), json_string(&r.machine.model), json_string(&r.machine.chip), r.machine.ncpu, p.installed_gb, r.current.is_some(),
            p.first_ts, p.last_ts, p.span_days, p.minutes, p.awake_share, p.ram_p99_gb, p.ram_max_gb, p.days_over_ram, json_opt(p.pressure_share),
            p.cpu_p95, p.saturated_share, p.sustained_runs, p.single_share, json_opt(p.gpu_p95), p.disk_used_gb, p.disk_total_gb, p.disk_growth_gb_day,
            json_opt(p.battery_share), json_opt(p.display_share), json_opt(p.thermal_share), tops(&p.top_cpu), tops(&p.top_mem),
            tiers.join(","), json_choice(&r.portable), json_choice(&r.linux))
}

/// Days from an ISO date (YYYY-MM-DD) to now; 0 if it cannot be read.
pub fn days_since(iso: &str, now: i64) -> i64 {
    let parts: Vec<i64> = iso.split('-').filter_map(|p| p.parse().ok()).collect();
    if parts.len() != 3 {
        return 0;
    }
    // Days from the civil date (Howard Hinnant's algorithm) - no time zone, no libc call.
    let (y, m, d) = (if parts[1] <= 2 { parts[0] - 1 } else { parts[0] }, parts[1], parts[2]);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + d - 1;
    let days = era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
    (now / 86400 - days).max(0)
}

#[cfg(test)]
mod tests;
