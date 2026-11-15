//! The recorder without a machine: readings fed by hand, files in a temporary directory.

use super::*;
use crate::sys::Mem;

fn calm(cpu: f64) -> Reading {
    Reading { cpu_pct: cpu, busy_cores: (cpu / 20.0) as u8, max_core: cpu, mem_used_mb: 8000.0, demand_mb: 8000.0, swap_mb: 0.0,
              vitals: Vitals::default() }
}

fn temp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("termstats-test-{}-{}", std::process::id(), name));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}

fn minute_at(ts: i64) -> Minute {
    Minute { ts, n: 12, cpu_avg: 10.0, cpu_p95: 20.0, cpu_max: 20.0, busy_avg: 1.0, busy_max: 2, mem_avg_mb: 8000.0,
             mem_max_mb: 8100.0, demand_mb: 8100.0, disk_used_gb: 100.0, disk_total_gb: 500.0, ..Minute::default() }
}

// --- one minute out of twelve readings ------------------------------------------------------

#[test]
fn a_minute_is_the_mean_the_95th_percentile_and_the_peak_of_its_readings() {
    let mut agg = Aggregate::default();
    for cpu in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 0.0, 50.0] {
        agg.add(&calm(cpu));
    }
    let m = agg.finish(600, 100.0, 500.0);
    assert_eq!((m.ts, m.n), (600, 12));
    assert!((m.cpu_avg - 50.0).abs() < 1e-9);
    assert_eq!(m.cpu_max, 100.0);
    assert_eq!(m.cpu_p95, 100.0, "of twelve readings the 95th percentile is the largest");
    assert_eq!(m.busy_max, 5);
    assert!(agg.is_empty(), "finishing a minute starts the next one");
}

#[test]
fn single_core_bound_means_one_core_at_its_limit_and_the_rest_free() {
    let mut agg = Aggregate::default();
    agg.add(&Reading { max_core: 99.0, busy_cores: 1, cpu_pct: 10.0, ..calm(10.0) });
    agg.add(&Reading { max_core: 99.0, busy_cores: 8, cpu_pct: 80.0, ..calm(80.0) });      // every core busy: more cores help
    agg.add(&Reading { max_core: 40.0, busy_cores: 0, cpu_pct: 5.0, ..calm(5.0) });
    agg.add(&Reading { max_core: 90.0, busy_cores: 2, cpu_pct: 15.0, ..calm(15.0) });
    assert!((agg.finish(0, 0.0, 0.0).single - 0.5).abs() < 1e-9);
}

#[test]
fn what_a_machine_does_not_report_stays_empty_instead_of_becoming_a_zero() {
    let mut agg = Aggregate::default();
    agg.add(&calm(10.0));
    let m = agg.finish(0, 1.0, 2.0);
    assert_eq!((m.gpu_avg, m.gpu_max, m.pressure, m.ac, m.battery, m.thermal, m.displays), (None, None, None, None, None, None, None));
    let line = m.to_line();
    let fields: Vec<&str> = line.split('\t').collect();
    for column in ["pressure", "gpu_avg", "gpu_max", "ac", "battery", "thermal", "displays"] {
        let i = COLUMNS.iter().position(|c| *c == column).unwrap();
        assert_eq!(fields[i], "", "{column} must be empty, not 0");
    }
    assert_eq!(Minute::parse(&line).unwrap().gpu_max, None);
}

#[test]
fn the_reported_vitals_are_the_worst_of_the_minute() {
    let mut agg = Aggregate::default();
    let with = |gpu: f64, pressure: u8, thermal: u8, ac: bool, displays: u8| Reading {
        vitals: Vitals { gpu_pct: Some(gpu), pressure: Some(pressure), thermal: Some(thermal), on_ac: Some(ac), battery_pct: Some(80), ext_displays: Some(displays) },
        ..calm(10.0)
    };
    agg.add(&with(20.0, 0, 0, true, 1));
    agg.add(&with(90.0, 2, 1, false, 0));
    agg.add(&with(40.0, 1, 3, true, 2));
    agg.add(&with(10.0, 0, 0, true, 1));
    let m = agg.finish(0, 0.0, 0.0);
    assert_eq!((m.gpu_avg, m.gpu_max), (Some(40.0), Some(90.0)));
    assert_eq!((m.pressure, m.thermal, m.displays, m.battery), (Some(2), Some(3), Some(2), Some(80)));
    assert_eq!(m.ac, Some(0.75), "three of four readings on external power");
}

#[test]
fn bytes_add_up_and_programs_are_ranked() {
    let mut agg = Aggregate::default();
    agg.add(&calm(10.0));
    agg.add_io(1048576, 2 * 1048576, 512 * 1024, 0);
    agg.add_io(1048576, 0, 512 * 1024, 3 * 1048576);
    agg.add_procs(&[("rustc".into(), 20.0), ("node".into(), 5.0)], &[("Chrome".into(), 900.0), ("node".into(), 300.0)]);
    agg.add_procs(&[("rustc".into(), 25.0), ("Blender".into(), 30.0), ("zsh".into(), 0.1)], &[("Chrome".into(), 1200.0), ("Blender".into(), 2000.0)]);
    let m = agg.finish(0, 0.0, 0.0);
    assert_eq!((m.read_mb, m.write_mb, m.rx_mb, m.tx_mb), (2.0, 2.0, 1.0, 3.0));
    assert_eq!(m.top_cpu, vec![("rustc".to_string(), 45.0), ("Blender".to_string(), 30.0), ("node".to_string(), 5.0)], "CPU seconds add up over the scans");
    assert_eq!(m.top_mem, vec![("Blender".to_string(), 2000.0), ("Chrome".to_string(), 1200.0), ("node".to_string(), 300.0)], "memory is the peak, not the sum");
}

#[test]
fn equal_consumers_are_ordered_by_name_so_the_list_does_not_dance() {
    let mut agg = Aggregate::default();
    agg.add(&calm(1.0));
    agg.add_procs(&[("b".into(), 5.0), ("a".into(), 5.0), ("c".into(), 5.0)], &[]);
    assert_eq!(agg.finish(0, 0.0, 0.0).top_cpu.iter().map(|t| t.0.as_str()).collect::<Vec<_>>(), ["a", "b", "c"]);
}

// --- one reading from two snapshots ---------------------------------------------------------

#[test]
fn a_reading_is_the_difference_of_two_cpu_snapshots() {
    let before = [CpuTimes { busy: 100, total: 1000 }, CpuTimes { busy: 100, total: 1000 }, CpuTimes { busy: 0, total: 1000 }];
    let now = [CpuTimes { busy: 200, total: 1100 }, CpuTimes { busy: 140, total: 1100 }, CpuTimes { busy: 0, total: 1100 }];
    let r = reading(&now, &before, &Mem::default(), Vitals::default());
    assert!((r.cpu_pct - 140.0 / 3.0).abs() < 1e-9);
    assert_eq!((r.busy_cores, r.max_core), (1, 100.0));
}

#[test]
fn memory_that_was_compressed_or_swapped_away_still_counts_as_wanted() {
    const GB: u64 = 1 << 30;
    let enough = Mem { total: 16 * GB, available: 8 * GB, ..Mem::default() };
    let r = reading(&[], &[], &enough, Vitals::default());
    assert_eq!((r.mem_used_mb, r.demand_mb), (8192.0, 8192.0), "with room to spare, demand is what is in use");
    // 14 GB in use, 2 GB of RAM hold 7 GB of compressed pages: 5 GB more than there is.
    let squeezed = Mem { total: 16 * GB, available: 2 * GB, compressed: 2 * GB, uncompressed: 7 * GB, ..Mem::default() };
    assert_eq!(reading(&[], &[], &squeezed, Vitals::default()).demand_mb, 19.0 * 1024.0);
    let swapping = Mem { total: 16 * GB, available: 2 * GB, swap_used: 6 * GB, ..Mem::default() };
    let r = reading(&[], &[], &swapping, Vitals::default());
    assert_eq!((r.demand_mb, r.swap_mb), (20.0 * 1024.0, 6144.0));
}

// --- the line format ------------------------------------------------------------------------

#[test]
fn a_line_survives_the_round_trip() {
    let mut agg = Aggregate::default();
    agg.add(&Reading { vitals: Vitals { gpu_pct: Some(12.34), pressure: Some(1), thermal: Some(0), on_ac: Some(true), battery_pct: Some(77), ext_displays: Some(1) },
                       cpu_pct: 33.33, busy_cores: 3, max_core: 91.0, mem_used_mb: 11239.4, demand_mb: 17238.2, swap_mb: 5923.0 });
    agg.add_io(123456789, 987654, 1234567, 7654321);
    agg.add_procs(&[("Google Chrome Helper (Renderer)".into(), 12.26), ("rustc".into(), 3.0)], &[("node".into(), 460.4)]);
    let line = agg.finish(1794718320, 411.04, 460.43).to_line();
    assert_eq!(line.split('\t').count(), COLUMNS.len());
    let back = Minute::parse(&line).expect("the line parses");
    assert_eq!(back.to_line(), line);
    assert_eq!(back.top_cpu[0], ("Google Chrome Helper (Renderer)".to_string(), 12.3));
    assert_eq!((back.ts, back.battery, back.ac), (1794718320, Some(77), Some(1.0)));
}

#[test]
fn a_program_name_cannot_break_the_format() {
    assert_eq!(safe_name("a\tb|c:d\ne"), "a_b_c_d_e");
    assert_eq!(safe_name(""), "?");
    assert_eq!(safe_name(&"x".repeat(100)).len(), 40);
    let mut agg = Aggregate::default();
    agg.add(&calm(1.0));
    agg.add_procs(&[("evil\tname|with:all".into(), 1.0)], &[]);
    let line = agg.finish(0, 0.0, 0.0).to_line();
    assert_eq!(line.split('\t').count(), COLUMNS.len());
    assert_eq!(Minute::parse(&line).unwrap().top_cpu[0].0, "evil_name_with_all");
}

#[test]
fn headers_and_damaged_lines_are_skipped_not_fatal() {
    let good = minute_at(60).to_line();
    assert!(Minute::parse(&good).is_some());
    assert!(Minute::parse(&format!("#{}", COLUMNS.join("\t"))).is_none());
    assert!(Minute::parse("").is_none());
    assert!(Minute::parse(&good[..good.len() / 2]).is_none(), "half a line, as a crash leaves it");
    assert!(Minute::parse(&good.replacen("60", "sixty", 1)).is_none());
}

#[test]
fn the_header_says_which_machine_the_numbers_are_from() {
    let head = header("my\thost", "MacBookPro18,1", "Apple M1 Pro", 10, 16384);
    let mut lines = head.lines();
    let machine = parse_header(lines.next().unwrap()).unwrap();
    assert_eq!(machine, Machine { host: "my host".into(), model: "MacBookPro18,1".into(), chip: "Apple M1 Pro".into(), ncpu: 10, mem_mb: 16384,
                                  os: crate::sys::OS_NAME.into() });
    assert_eq!(lines.next().unwrap(), format!("#{}", COLUMNS.join("\t")));
    assert!(parse_header("#some other file v1\thost=x").is_none());
    assert!(parse_header(&minute_at(0).to_line()).is_none());
}

// --- the files ------------------------------------------------------------------------------

#[test]
fn minutes_go_to_the_file_of_their_day_which_only_the_owner_can_read() {
    let dir = temp("append");
    let head = header("h", "m", "c", 4, 8192);
    // Noon of two consecutive days: on different dates in every time zone.
    let (day1, day2) = (1_794_744_000 - 1_794_744_000 % 86400 + 43200, 1_794_744_000 - 1_794_744_000 % 86400 + 43200 + 86400);
    append(&dir, &head, &[minute_at(day1), minute_at(day1 + 60)]).unwrap();
    append(&dir, &head, &[minute_at(day1 + 120), minute_at(day2)]).unwrap();
    let files = day_files(&dir);
    assert_eq!(files.len(), 2);
    let first = std::fs::read_to_string(&files[0]).unwrap();
    assert_eq!(first.matches(FORMAT).count(), 1, "the header is written once, with the file");
    assert_eq!(first.lines().filter(|l| !l.starts_with('#')).count(), 3);
    assert_eq!(std::fs::metadata(&files[0]).unwrap().permissions().mode() & 0o777, 0o600);
    assert_eq!(std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn loading_returns_the_minutes_in_order_and_from_the_asked_time_on() {
    let dir = temp("load");
    let head = header("h", "MacBookPro18,1", "Apple M1 Pro", 10, 16384);
    let noon = 1_794_744_000 - 1_794_744_000 % 86400 + 43200;
    append(&dir, &head, &[minute_at(noon + 86400), minute_at(noon + 86460)]).unwrap();
    append(&dir, &head, &[minute_at(noon), minute_at(noon + 60)]).unwrap();
    std::fs::write(dir.join("notes.txt"), "not a day file").unwrap();
    let all = load(&dir, 0);
    assert_eq!(all.minutes.iter().map(|m| m.ts).collect::<Vec<_>>(), vec![noon, noon + 60, noon + 86400, noon + 86460]);
    assert_eq!((all.files, all.machine.as_ref().map(|m| m.ncpu)), (2, Some(10)));
    assert!(all.bytes > 200);
    assert_eq!(load(&dir, noon + 86400).minutes.len(), 2);
    assert_eq!(span_days(&all.minutes), 1);
    assert_eq!(span_days(&[]), 0);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_file_that_ends_in_half_a_line_costs_that_line_only() {
    let dir = temp("torn");
    let head = header("h", "m", "c", 4, 8192);
    append(&dir, &head, &[minute_at(43200), minute_at(43260)]).unwrap();
    let file = &day_files(&dir)[0];
    let text = std::fs::read_to_string(file).unwrap();
    std::fs::write(file, &text[..text.len() - 40]).unwrap();
    assert_eq!(load(&dir, 0).minutes.len(), 1);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn old_day_files_are_pruned_and_nothing_else() {
    let dir = temp("prune");
    std::fs::create_dir_all(&dir).unwrap();
    let now = 1_794_744_000;
    for name in ["2020-01-01.tsv", "2026-11-14.tsv", "keep-me.tsv", "2020-01-01.txt"] {
        std::fs::write(dir.join(name), "x").unwrap();
    }
    let recent = format!("{}.tsv", local_date(now - 5 * 86400));
    std::fs::write(dir.join(&recent), "x").unwrap();
    assert_eq!(prune(&dir, 90, now), 1);
    let left: Vec<String> = std::fs::read_dir(&dir).unwrap().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    assert!(!left.contains(&"2020-01-01.tsv".to_string()));
    for kept in ["2026-11-14.tsv", "keep-me.tsv", "2020-01-01.txt", recent.as_str()] {
        assert!(left.contains(&kept.to_string()), "{kept} must stay");
    }
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn only_one_recorder_runs_per_history() {
    let dir = temp("lock");
    let first = lock(&dir);
    assert!(first.is_some());
    assert!(lock(&dir).is_none(), "the second one must be turned away");
    drop(first);
    assert!(lock(&dir).is_some(), "and may start once the first is gone");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_state_directory_follows_the_environment() {
    // One test for all three: the environment is shared between test threads.
    let saved: Vec<_> = ["TERMSTATS_STATE_DIR", "XDG_STATE_HOME", "HOME"].iter().map(|k| (*k, std::env::var_os(k))).collect();
    std::env::set_var("HOME", "/home/u");
    std::env::remove_var("TERMSTATS_STATE_DIR");
    std::env::remove_var("XDG_STATE_HOME");
    assert_eq!(state_dir(), PathBuf::from("/home/u/.local/state/termstats"));
    std::env::set_var("XDG_STATE_HOME", "/x/state");
    assert_eq!(state_dir(), PathBuf::from("/x/state/termstats"));
    std::env::set_var("TERMSTATS_STATE_DIR", "/elsewhere");
    assert_eq!(state_dir(), PathBuf::from("/elsewhere"));
    assert_eq!(history_dir(&state_dir()), PathBuf::from("/elsewhere/history"));
    for (key, value) in saved {
        match value {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }
}

// --- the service ----------------------------------------------------------------------------

#[test]
fn the_launchd_agent_runs_in_the_background_and_comes_back() {
    let plist = launchd_plist("/Users/a & b/bin/termstats-native", "/Users/a & b/state");
    for needed in ["<key>RunAtLoad</key><true/>", "<key>KeepAlive</key><true/>", "<string>Background</string>",
                   "<key>LowPriorityIO</key><true/>", "<string>record</string>", SERVICE_LABEL] {
        assert!(plist.contains(needed), "missing {needed}");
    }
    assert!(plist.contains("/Users/a &amp; b/bin/termstats-native") && !plist.contains("a & b"), "paths are XML-escaped");
}

#[test]
fn the_systemd_unit_is_nice_and_restarts() {
    let unit = systemd_unit("/usr/local/bin/termstats-native", "/home/u/.local/state/termstats");
    for needed in ["ExecStart=/usr/local/bin/termstats-native record", "Restart=always", "Nice=19", "IOSchedulingClass=idle", "WantedBy=default.target"] {
        assert!(unit.contains(needed), "missing {needed}");
    }
}

#[test]
fn status_counts_the_days_towards_the_fortnight() {
    let dir = temp("status");
    let head = header("h", "m", "c", 4, 8192);
    let start = now_wall() - 6 * 86400 - 3600;
    append(&history_dir(&dir), &head, &(0..7).map(|d| minute_at(start + d * 86400)).collect::<Vec<_>>()).unwrap();
    let text = status(&dir);
    assert!(text.contains("day 7 of 14") && text.contains("recommend --now"), "{text}");
    assert!(text.contains("not running") && text.contains("7 minutes"), "{text}");
    append(&history_dir(&dir), &head, &[minute_at(start - 9 * 86400)]).unwrap();
    assert!(status(&dir).contains("ready      yes"), "{}", status(&dir));
    assert!(status(&temp("status-empty")).contains("nothing yet"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn purge_deletes_the_history_and_the_marker() {
    let dir = temp("purge");
    append(&history_dir(&dir), &header("h", "m", "c", 4, 8192), &[minute_at(43200)]).unwrap();
    std::fs::write(dir.join("ready-notified"), "").unwrap();
    assert!(purge(&dir).unwrap().contains("1 day file"));
    assert!(day_files(&history_dir(&dir)).is_empty() && !dir.join("ready-notified").exists());
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sleep_is_a_gap_and_not_a_reading() {
    let mut agg = Aggregate::default();
    assert!(agg.watched(SAMPLE_S) && agg.watched(GAP_FACTOR * SAMPLE_S), "a late tick is still a tick");
    agg.add(&calm(10.0));
    assert!(!agg.watched(GAP_FACTOR * SAMPLE_S + 1) && !agg.watched(3600));
    let minute = agg.finish(0, 0.0, 0.0);
    assert_eq!((minute.n, minute.gap_s as i64), (1, GAP_FACTOR * SAMPLE_S + 1 + 3600));
}

#[test]
fn the_demand_of_a_minute_is_its_highest_not_its_last() {
    let mut agg = Aggregate::default();
    for demand in [9000.0, 15000.0, 8000.0] {
        agg.add(&Reading { demand_mb: demand, ..calm(10.0) });
    }
    assert_eq!(agg.finish(0, 0.0, 0.0).demand_mb, 15000.0);
}
