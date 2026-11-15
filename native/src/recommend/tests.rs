//! The recommendation against histories whose answer is known in advance.

use super::*;
use crate::catalog::Step;

// A small catalogue with a clear ladder: every test can say which rung it expects.
static CHIPS: &[Chip] = &[
    Chip { id: "old-8", name: "Old (8 cores)", brand: "Old Pro", perf_cores: 6, eff_cores: 2, single: 2000, multi: 9000, gpu: 30000 },
    Chip { id: "old-10", name: "Old (10 cores)", brand: "Old Pro", perf_cores: 8, eff_cores: 2, single: 2000, multi: 10000, gpu: 40000 },
    Chip { id: "base", name: "Base", brand: "Base", perf_cores: 4, eff_cores: 6, single: 3500, multi: 14000, gpu: 45000 },
    Chip { id: "pro", name: "Pro", brand: "Pro", perf_cores: 10, eff_cores: 4, single: 3600, multi: 22000, gpu: 90000 },
    Chip { id: "max", name: "Max", brand: "Max", perf_cores: 12, eff_cores: 4, single: 3600, multi: 28000, gpu: 180000 },
    Chip { id: "amd", name: "Ryzen", brand: "Ryzen", perf_cores: 16, eff_cores: 0, single: 2900, multi: 21000, gpu: 80000 },
];

const SSD: &[Step] = &[Step { gb: 256, plus_eur: 0 }, Step { gb: 512, plus_eur: 200 }, Step { gb: 1000, plus_eur: 400 }, Step { gb: 2000, plus_eur: 800 }];

static DEVICES: &[Device] = &[
    Device { id: "air", vendor: "Apple", name: "Air", form: "laptop", os: "macOS", fanless: true, source: "s", verified: true, variants: &[
        Variant { chip: "base", base_price_eur: 1200, ram: &[Step { gb: 16, plus_eur: 0 }, Step { gb: 24, plus_eur: 250 }, Step { gb: 32, plus_eur: 500 }], ssd: SSD },
    ] },
    Device { id: "book", vendor: "Apple", name: "Book Pro", form: "laptop", os: "macOS", fanless: false, source: "s", verified: true, variants: &[
        Variant { chip: "base", base_price_eur: 1900, ram: &[Step { gb: 16, plus_eur: 0 }, Step { gb: 32, plus_eur: 500 }], ssd: SSD },
        Variant { chip: "pro", base_price_eur: 2500, ram: &[Step { gb: 24, plus_eur: 0 }, Step { gb: 48, plus_eur: 500 }, Step { gb: 64, plus_eur: 750 }], ssd: SSD },
        Variant { chip: "max", base_price_eur: 3800, ram: &[Step { gb: 36, plus_eur: 0 }, Step { gb: 64, plus_eur: 500 }, Step { gb: 128, plus_eur: 1500 }], ssd: SSD },
    ] },
    Device { id: "mini", vendor: "Apple", name: "Mini", form: "desktop", os: "macOS", fanless: false, source: "s", verified: true, variants: &[
        Variant { chip: "base", base_price_eur: 700, ram: &[Step { gb: 16, plus_eur: 0 }, Step { gb: 24, plus_eur: 250 }, Step { gb: 32, plus_eur: 500 }], ssd: SSD },
        Variant { chip: "pro", base_price_eur: 1600, ram: &[Step { gb: 24, plus_eur: 0 }, Step { gb: 48, plus_eur: 500 }, Step { gb: 64, plus_eur: 750 }], ssd: SSD },
    ] },
    Device { id: "tux", vendor: "Tux", name: "Laptop", form: "laptop", os: "Linux", fanless: false, source: "s", verified: false, variants: &[
        Variant { chip: "amd", base_price_eur: 1500, ram: &[Step { gb: 16, plus_eur: 0 }, Step { gb: 32, plus_eur: 100 }, Step { gb: 64, plus_eur: 300 }], ssd: SSD },
    ] },
    Device { id: "box", vendor: "Tux", name: "Box", form: "desktop", os: "Linux", fanless: false, source: "s", verified: true, variants: &[
        Variant { chip: "amd", base_price_eur: 900, ram: &[Step { gb: 32, plus_eur: 0 }, Step { gb: 64, plus_eur: 200 }, Step { gb: 128, plus_eur: 700 }], ssd: SSD },
    ] },
];

fn machine() -> Machine {
    Machine { host: "h".into(), model: "OldBook1,1".into(), chip: "Old Pro".into(), ncpu: 10, mem_mb: 16384, os: "macOS".into() }
}

/// Fourteen days, eight recorded hours a day, every minute shaped by `shape(day, minute)`.
fn fortnight(shape: impl Fn(i64, i64, &mut Minute)) -> Vec<Minute> {
    let mut minutes = Vec::new();
    for day in 0..15 {
        for minute in 0..480 {
            let mut m = Minute { ts: 1_794_700_800 + day * 86400 + minute * 60, n: 12, cpu_avg: 8.0, cpu_p95: 15.0, cpu_max: 20.0, busy_avg: 0.5,
                                 busy_max: 1, mem_avg_mb: 7000.0, mem_max_mb: 7200.0, demand_mb: 7200.0, disk_used_gb: 120.0, disk_total_gb: 460.0,
                                 pressure: Some(0), gpu_avg: Some(3.0), gpu_max: Some(8.0), ac: Some(1.0), battery: Some(100), thermal: Some(0),
                                 displays: Some(1), ..Minute::default() };
            shape(day, minute, &mut m);
            minutes.push(m);
        }
    }
    minutes
}

fn report_for(minutes: &[Minute], machine: &Machine) -> (Profile, [Need; 3], [Option<Choice>; 3], Option<Choice>, Option<Choice>) {
    let p = profile(minutes, machine);
    let r = build(&p, machine, CHIPS, DEVICES, "2026-11-15", 0, true);
    (p.clone(), r.needs.clone(), r.picks.clone(), r.portable.clone(), r.linux.clone())
}

// --- the profile ----------------------------------------------------------------------------

#[test]
fn the_profile_reads_the_fortnight() {
    let minutes = fortnight(|day, minute, m| {
        if day % 2 == 0 && minute < 30 {
            m.demand_mb = 20.0 * 1024.0;
            m.pressure = Some(1);
        }
        m.disk_used_gb = 120.0 + day as f64 * 0.5;
    });
    let p = profile(&minutes, &machine());
    assert_eq!((p.span_days, p.days, p.minutes), (14, 15, 15 * 480));
    assert!((p.awake_share - 1.0 / 3.0).abs() < 0.02, "eight of twenty-four hours: {}", p.awake_share);
    assert_eq!(p.days_over_ram, 8, "the even days went beyond 16 GB");
    assert!((p.ram_p99_gb - 20.0).abs() < 0.01 && p.ram_max_gb == 20.0);
    assert!((p.pressure_share.unwrap() - 8.0 * 30.0 / 7200.0).abs() < 1e-9);
    assert!((p.disk_growth_gb_day - 0.5).abs() < 0.02, "{}", p.disk_growth_gb_day);
    assert_eq!((p.disk_used_gb, p.installed_gb), (127.0, 16.0));
    assert_eq!((p.battery_share, p.display_share, p.thermal_share), (Some(0.0), Some(1.0), Some(0.0)));
}

#[test]
fn sustained_load_is_ten_unbroken_minutes_and_a_gap_breaks_it() {
    let minutes = fortnight(|day, minute, m| {
        if day == 0 && (100..115).contains(&minute) || day == 1 && (100..109).contains(&minute) {
            m.cpu_avg = 90.0;
        }
    });
    let p = profile(&minutes, &machine());
    assert_eq!(p.sustained_runs, 1, "fifteen minutes count, nine do not");
    assert!((p.sustained_share - 15.0 / 7200.0).abs() < 1e-9);
    // The same fifteen minutes with the machine asleep in the middle are two short runs.
    let mut broken = minutes.clone();
    for m in broken.iter_mut().filter(|m| m.ts >= 1_794_700_800 + 108 * 60) {
        m.ts += 3600;
    }
    assert_eq!(profile(&broken, &machine()).sustained_runs, 0);
}

#[test]
fn what_was_never_reported_is_unknown_not_zero() {
    let minutes = fortnight(|_, _, m| { m.gpu_avg = None; m.gpu_max = None; m.ac = None; m.battery = None; m.displays = None; m.thermal = None; m.pressure = None; });
    let p = profile(&minutes, &machine());
    assert_eq!((p.gpu_p95, p.gpu_heavy_share, p.battery_share, p.display_share, p.thermal_share, p.pressure_share), (None, None, None, None, None, None));
    let need = need(&p, current_chip(CHIPS, &machine()), Tier::Fit);
    assert_eq!((need.gpu, need.portable), (0.0, false));
    assert!(text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true)).contains("graphics   not reported"));
}

#[test]
fn the_busiest_programs_are_summed_over_the_fortnight() {
    let minutes = fortnight(|day, _, m| {
        m.top_cpu = vec![("rustc".into(), 36.0), ("node".into(), 3.6)];
        m.top_mem = vec![("Chrome".into(), if day == 3 { 4096.0 } else { 1024.0 })];
    });
    let p = profile(&minutes, &machine());
    assert_eq!(p.top_cpu[0].0, "rustc");
    assert!((p.top_cpu[0].1 - 72.0).abs() < 1e-6, "36 s a minute over 7200 minutes are 72 hours: {}", p.top_cpu[0].1);
    assert_eq!(p.top_mem, vec![("Chrome".to_string(), 4.0)], "memory is the peak");
}

#[test]
fn an_empty_history_is_an_empty_profile() {
    let p = profile(&[], &machine());
    assert_eq!((p.minutes, p.span_days, p.ram_p99_gb), (0, 0, 0.0));
}

// --- known machines, known answers ----------------------------------------------------------

#[test]
fn light_use_gets_the_smallest_machine_and_is_told_the_current_one_fits() {
    let minutes = fortnight(|_, _, _| {});
    let (p, needs, picks, _portable, _linux) = report_for(&minutes, &machine());
    let fit = picks[1].as_ref().unwrap();
    assert_eq!((fit.device, fit.ram_gb, fit.ssd_gb), ("mini", 16, 256), "never on battery: the cheapest desktop");
    assert_eq!(fit.price_eur, 700);
    assert!(current_meets(&p, &needs[1], current_chip(CHIPS, &machine())).0);
    let report = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true));
    assert!(report.contains("It fits what was measured"), "{report}");
}

#[test]
fn a_machine_that_wants_28_gb_is_given_at_least_32() {
    let minutes = fortnight(|_, minute, m| if minute < 120 { m.demand_mb = 28.0 * 1024.0; m.pressure = Some(2); });
    let (p, needs, picks, _, _) = report_for(&minutes, &machine());
    assert!((needs[0].ram_gb - 30.8).abs() < 0.01 && (needs[1].ram_gb - 36.4).abs() < 0.01 && (needs[2].ram_gb - 50.4).abs() < 0.01);
    assert_eq!(picks[0].as_ref().unwrap().ram_gb, 32);
    assert_eq!(picks[1].as_ref().unwrap().ram_gb, 48, "36.4 GB wanted: 32 is short, the next step is 48");
    assert_eq!(picks[2].as_ref().unwrap().ram_gb, 64);
    let (enough, short) = current_meets(&p, &needs[1], current_chip(CHIPS, &machine()));
    assert!(!enough && short == vec!["memory"]);
    let report = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true));
    assert!(report.contains("Short on: memory.") && report.contains("on 15 of 15 days"), "{report}");
}

#[test]
fn heavy_graphics_asks_for_the_graphics_class() {
    let minutes = fortnight(|_, minute, m| if minute < 200 { m.gpu_avg = Some(95.0); m.gpu_max = Some(100.0); });
    let (_p, needs, picks, _, _) = report_for(&minutes, &machine());
    assert!(needs[1].gpu >= 40000.0 * 1.4, "saturated graphics: at least 1.4 times the current chip, got {}", needs[1].gpu);
    assert_eq!(picks[1].as_ref().unwrap().chip, "Pro", "the base chip's graphics are too small");
    assert_eq!(picks[2].as_ref().unwrap().chip, "Max");
}

#[test]
fn a_saturated_processor_asks_for_more_than_the_measurement_shows() {
    let minutes = fortnight(|_, minute, m| if minute < 100 { m.cpu_avg = 99.0; m.busy_max = 10; });
    let p = profile(&minutes, &machine());
    assert!(p.saturated_share > 0.05 && p.sustained_runs == 15);
    let chip = current_chip(CHIPS, &machine());
    let needs = TIERS.map(|t| need(&p, chip, t));
    assert!(needs[0].multi >= 11000.0 && needs[1].multi >= 14000.0 && needs[2].multi >= 20000.0, "{:?}", needs.iter().map(|n| n.multi).collect::<Vec<_>>());
    assert!(needs[1].no_fanless, "sustained load: no machine without a fan");
    let picks = TIERS.map(|t| pick(DEVICES, CHIPS, &need(&p, chip, t), "macOS", false).unwrap());
    assert!(picks.iter().all(|c| c.device != "air"));
    // 99 % at the busy times against a 40 % target is 2.5 times the current chip: beyond the Pro.
    assert_eq!((picks[1].chip, picks[2].chip), ("Pro", "Max"));
}

#[test]
fn work_on_battery_means_laptops_only_and_a_desk_machine_gets_a_laptop_alternative() {
    let mobile = fortnight(|_, minute, m| if minute < 60 { m.ac = Some(0.0); m.battery = Some(60); });
    let (_p, needs, picks, portable, linux) = report_for(&mobile, &machine());
    assert!(needs[1].portable);
    assert!(picks.iter().all(|c| c.as_ref().unwrap().form == "laptop"));
    assert_eq!(picks[1].as_ref().unwrap().device, "air");
    assert_eq!(portable, None, "the pick already is a laptop");
    assert_eq!(linux.unwrap().device, "tux", "the Linux alternative has to move as well");

    let desk = fortnight(|_, _, _| {});
    let (_p, needs, picks, portable, linux) = report_for(&desk, &machine());
    assert!(!needs[1].portable);
    assert_eq!(picks[1].as_ref().unwrap().form, "desktop");
    assert_eq!(portable.unwrap().device, "air", "the same tier as a laptop, in case it has to move");
    assert_eq!(linux.unwrap().device, "box");
}

#[test]
fn storage_that_is_nearly_full_asks_for_the_next_size() {
    let minutes = fortnight(|day, _, m| { m.disk_used_gb = 400.0 + day as f64; m.disk_total_gb = 460.0; });
    let (p, needs, picks, _, _) = report_for(&minutes, &machine());
    assert!(needs[1].ssd_gb > 600.0 && needs[1].ssd_gb < 1300.0, "{}", needs[1].ssd_gb);
    // What is in use plus damped growth (1, 2, 4 years at half the measured rate), times the reserve.
    assert!(p.disk_growth_gb_day > 0.5, "{}", p.disk_growth_gb_day);
    for (need, (years, factor)) in needs.iter().zip([(1.0, 1.25), (2.0, 1.5), (4.0, 2.0)]) {
        let expected = (p.disk_used_gb + (p.disk_growth_gb_day * 365.0 * years * 0.5).min(p.disk_used_gb)) * factor;
        assert!((need.ssd_gb - expected).abs() < 1e-6, "{} != {expected}", need.ssd_gb);
    }
    assert!(picks[1].as_ref().unwrap().ssd_gb >= 1000);
    assert_eq!(current_meets(&p, &needs[1], current_chip(CHIPS, &machine())).1, vec!["storage"]);
}

#[test]
fn the_three_levels_never_go_down() {
    let minutes = fortnight(|_, minute, m| { if minute < 50 { m.cpu_avg = 70.0; m.demand_mb = 18000.0; m.gpu_max = Some(60.0); } });
    let (_p, needs, picks, _, _) = report_for(&minutes, &machine());
    for pair in needs.windows(2) {
        assert!(pair[0].ram_gb <= pair[1].ram_gb && pair[0].ssd_gb <= pair[1].ssd_gb && pair[0].multi <= pair[1].multi && pair[0].gpu <= pair[1].gpu);
    }
    let prices: Vec<u32> = picks.iter().map(|c| c.as_ref().unwrap().price_eur).collect();
    assert!(prices[0] <= prices[1] && prices[1] <= prices[2], "{prices:?}");
}

// --- the pick -------------------------------------------------------------------------------

#[test]
fn the_cheapest_configuration_that_meets_the_need_wins() {
    let need = Need { ram_gb: 20.0, ssd_gb: 400.0, multi: 12000.0, ..Need::default() };
    let choice = pick(DEVICES, CHIPS, &need, "macOS", false).unwrap();
    assert_eq!((choice.device, choice.ram_gb, choice.ssd_gb, choice.price_eur), ("mini", 24, 512, 700 + 250 + 200));
    let laptop = pick(DEVICES, CHIPS, &need, "macOS", true).unwrap();
    assert_eq!((laptop.device, laptop.price_eur), ("air", 1200 + 250 + 200));
    let no_fan = pick(DEVICES, CHIPS, &Need { no_fanless: true, portable: true, ..need.clone() }, "macOS", false).unwrap();
    assert_eq!(no_fan.device, "book");
}

#[test]
fn nothing_is_made_up_when_nothing_fits() {
    let need = Need { ram_gb: 512.0, ..Need::default() };
    assert_eq!(pick(DEVICES, CHIPS, &need, "macOS", false), None);
    let minutes = fortnight(|_, _, m| m.demand_mb = 400.0 * 1024.0);
    let p = profile(&minutes, &machine());
    let report = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true));
    assert!(report.matches("nothing in the catalogue meets this").count() >= 3, "{report}");
}

#[test]
fn the_current_chip_is_found_by_brand_and_core_count() {
    assert_eq!(current_chip(CHIPS, &machine()).unwrap().id, "old-10");
    // "Old Pro" contains "Old": a chip of the plain family with the right core count must not win.
    static FAMILY: &[Chip] = &[
        Chip { id: "plain-8", name: "Old", brand: "Old", perf_cores: 4, eff_cores: 4, single: 1, multi: 1, gpu: 1 },
        Chip { id: "pro-8", name: "Old Pro (8)", brand: "Old Pro", perf_cores: 6, eff_cores: 2, single: 1, multi: 1, gpu: 1 },
        Chip { id: "pro-10", name: "Old Pro (10)", brand: "Old Pro", perf_cores: 8, eff_cores: 2, single: 1, multi: 1, gpu: 1 },
    ];
    assert_eq!(current_chip(FAMILY, &Machine { ncpu: 8, ..machine() }).unwrap().id, "pro-8");
    assert_eq!(current_chip(FAMILY, &Machine { ncpu: 12, ..machine() }).unwrap().id, "pro-8", "an unlisted core count: still the same family");
    assert_eq!(current_chip(FAMILY, &Machine { chip: "Old".into(), ncpu: 8, ..machine() }).unwrap().id, "plain-8");
    assert_eq!(current_chip(CHIPS, &Machine { ncpu: 8, ..machine() }).unwrap().id, "old-8");
    let linux = Machine { chip: "AMD Ryzen 9 with Radeon graphics".into(), ncpu: 32, ..machine() };
    assert_eq!(current_chip(CHIPS, &linux).unwrap().id, "amd", "cpuinfo names contain the brand");
    assert!(current_chip(CHIPS, &Machine { chip: "Mystery 9000".into(), ..machine() }).is_none());
}

#[test]
fn an_unknown_chip_is_said_not_guessed() {
    let unknown = Machine { chip: "Mystery 9000".into(), ..machine() };
    let minutes = fortnight(|_, minute, m| if minute < 100 { m.cpu_avg = 99.0; });
    let p = profile(&minutes, &unknown);
    let r = build(&p, &unknown, CHIPS, DEVICES, "2026-11-15", 0, true);
    assert_eq!((r.needs[1].multi, r.needs[1].gpu), (0.0, 0.0));
    assert!(text(&r).contains("is not in the catalogue, so processor and graphics could not be compared"));
}

// --- the report -----------------------------------------------------------------------------

#[test]
fn a_report_before_the_fortnight_says_it_is_provisional() {
    let minutes = fortnight(|_, _, _| {});
    let p = profile(&minutes[..3 * 480], &machine());
    let early = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, false));
    assert!(early.contains("PROVISIONAL: 2 of 14 days"), "{early}");
    let p = profile(&minutes, &machine());
    assert!(!text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true)).contains("PROVISIONAL"));
}

#[test]
fn an_old_catalogue_is_called_old() {
    let minutes = fortnight(|_, _, _| {});
    let p = profile(&minutes, &machine());
    assert!(!text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 120, true)).contains("days old"));
    assert!(text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 121, true)).contains("121 days old"));
}

#[test]
fn every_pick_carries_its_reasons_and_an_unverified_price_is_marked() {
    let minutes = fortnight(|_, minute, m| if minute < 100 { m.demand_mb = 22.0 * 1024.0; });
    let p = profile(&minutes, &machine());
    let report = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true));
    assert!(report.contains("22.0 GB wanted at the 99th percentile x 1.30"), "{report}");
    assert!(report.contains("of the current Old Pro"));
    assert!(report.contains("just enough") && report.contains("fits") && report.contains("with reserve"));
    assert!(report.contains("Not measured: software that needs one particular system"));
    let mobile = fortnight(|_, minute, m| if minute < 60 { m.ac = Some(0.0); });
    let p = profile(&mobile, &machine());
    let report = text(&build(&p, &machine(), CHIPS, DEVICES, "2026-11-15", 0, true));
    let linux_line = report.lines().skip_while(|l| !l.contains("Running Linux")).nth(1).unwrap();
    assert!(linux_line.contains("price not from the maker's own page"), "{linux_line}");
}

#[test]
fn the_json_names_the_same_picks() {
    let minutes = fortnight(|_, _, m| m.top_cpu = vec![("say \"hi\"\\".into(), 1.0)]);
    let this = machine();
    let p = profile(&minutes, &this);
    let r = build(&p, &this, CHIPS, DEVICES, "2026-11-15", 0, true);
    let out = json(&r);
    assert!(out.starts_with('{') && out.trim_end().ends_with('}'));
    assert_eq!(out.matches('{').count(), out.matches('}').count());
    assert!(out.contains("\"device\":\"mini\"") && out.contains("\"tier\":\"fits\"") && out.contains("\"ready\":true"));
    assert!(out.contains("say \\\"hi\\\"\\\\"), "quotes and backslashes are escaped");
}

#[test]
fn prices_and_dates_read_the_way_a_person_writes_them() {
    assert_eq!((euro(700), euro(1149), euro(12999)), ("700 EUR".to_string(), "1.149 EUR".to_string(), "12.999 EUR".to_string()));
    assert_eq!((storage(512), storage(1000), storage(2000)), ("512 GB".to_string(), "1 TB".to_string(), "2 TB".to_string()));
    assert_eq!((storage(1024), storage(4096), storage(16384), storage(1500)), ("1 TB".to_string(), "4 TB".to_string(), "16 TB".to_string(), "1.5 TB".to_string()));
    assert_eq!((pct(0.0), pct(0.004), pct(0.31)), ("0 %".to_string(), "under 1 %".to_string(), "31 %".to_string()));
    assert_eq!(days_since("1970-01-11", 20 * 86400), 10);
    assert_eq!(days_since("2026-11-15", 1_794_700_800 + 3 * 86400), 3);
    assert_eq!(days_since("not a date", 1_794_700_800), 0);
}
