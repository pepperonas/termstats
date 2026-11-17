//! The dashboard without a machine: a State filled by hand, drawn into a Screen.

use super::*;
use crate::screen::{ColorLevel, Painter, BLANK};
use crate::themes::THEMES;

fn opts(live: bool) -> Options {
    Options { live, ..Options::terminal(0.5, &THEMES[0]) }
}

/// A machine with `ncores` cores, `ndisks` partitions and a full history.
fn machine(ncores: usize, ndisks: usize, live: bool) -> State {
    let mut s = State::new(opts(live));
    s.host = "testhost".to_string();
    s.primed = true;
    s.core_pct = (0..ncores).map(|i| (i * 37 % 101) as f64).collect();
    s.total_pct = 42.5;
    s.steal_pct = 1.5;
    s.mem = Mem { total: 16 << 30, used: 6 << 30, available: 7 << 30, swap_total: 4 << 30, swap_used: 1 << 30, ..Mem::default() };
    s.disks = (0..ndisks).map(|i| Disk { mount: format!("/Volumes/disk number {i}"), total: 500 << 30, used: (100 + i as u64) << 30,
                                          percent: 20.0 + i as f64 }).collect();
    s.net = Net { sent: 3 << 30, recv: 9 << 30 };
    s.sent_s = 120_000.0;
    s.recv_s = 900_000.0;
    s.has_io = true;
    s.io_rate = Some((1_500_000.0, 250_000.0));
    s.nproc = 321;
    s.ranked = (0..40).map(|i| ProcRow { pid: 100 + i, cpu: 90.0 - 2.0 * i as f64, rss: (50 + i as u64) << 20 }).collect();
    for row in &s.ranked {
        s.names.insert(row.pid, format!("process number {}", row.pid));
    }
    for i in 0..HISTORY_LEN {
        push(&mut s.cpu_hist, (i * 7 % 100) as f64);
        push(&mut s.steal_hist, 0.0);
        push(&mut s.sent_hist, 1000.0 * i as f64);
        push(&mut s.recv_hist, 2048.0 * i as f64);
    }
    s
}

fn row(scr: &Screen, y: usize) -> String {
    scr.cells[y * scr.w..(y + 1) * scr.w].iter().map(|c| c.ch).collect()
}

fn text(scr: &Screen) -> String {
    (0..scr.h).map(|y| row(scr, y)).collect::<Vec<_>>().join("\n")
}

fn drawn(state: &mut State, w: usize, h: usize) -> Screen {
    let mut scr = Screen::new(w, h);
    state.draw(&mut scr);
    scr
}

// --- number formats: the same strings the Python dashboard prints ---------------------------

#[test]
fn every_number_keeps_its_width() {
    assert_eq!(fmt_pct(5.0), "  5.0%");
    assert_eq!(fmt_pct(100.0), "100.0%");
    assert_eq!(fmt_pct(f64::NAN), "  0.0%");
    assert_eq!(fmt_pct(-3.0), "  0.0%");
    assert_eq!(fmt_gb(6.2 * 1073741824.0), "  6.2G");
    assert_eq!(fmt_gb(2048.0 * 1073741824.0), "  2.0T");
    assert_eq!(fmt_rate(0.0), "  0.0B/s");
    assert_eq!(fmt_rate(999.94), "999.9B/s");
    assert_eq!(fmt_rate(999.95), "  1.0K/s");
    assert_eq!(fmt_rate(45.2 * 1024.0), " 45.2K/s");
    assert_eq!(fmt_rate(3.0 * 1024.0 * 1024.0 * 1024.0 * 5000.0), "15000.0G/s");
    assert_eq!(fmt_mem(482.0 * 1048576.0), " 482M");
    assert_eq!(fmt_mem(1.2 * 1073741824.0), " 1.2G");
    for v in [0.0, 12.3, 99.9, 100.0] {
        assert_eq!(fmt_pct(v).len(), 6);
    }
}

#[test]
fn the_axis_label_is_a_fixed_field() {
    assert_eq!(fmt_axis(50.0, 3, 100.0), " 50");
    assert_eq!(fmt_axis(0.0, 5, 600.0), "    0");
    assert_eq!(fmt_axis(1.5, 5, 3.0), "  1.5");
    assert_eq!(fmt_axis(123456.0, 5, 123456.0), "99999");
}

#[test]
fn the_interval_reads_like_pythons_g_format() {
    assert_eq!(fmt_g(0.5), "0.5");
    assert_eq!(fmt_g(1.0), "1");
    assert_eq!(fmt_g(2.5), "2.5");
    assert_eq!(fmt_g(10.0), "10");
}

#[test]
fn an_unbounded_axis_gets_a_round_top() {
    assert_eq!(nice_ceiling(560.0), 600.0);
    assert_eq!(nice_ceiling(0.0), 1.0);
    assert_eq!(nice_ceiling(1.0), 1.0);
    assert_eq!(nice_ceiling(73.0), 80.0);
    assert_eq!(nice_ceiling(0.23), 0.25);
}

#[test]
fn the_window_label_follows_the_interval() {
    assert_eq!(window_label(0.5), "last 30s");
    assert_eq!(window_label(1.0), "last 60s");
    assert_eq!(window_label(2.0), "last 2m");
    assert_eq!(window_label(3.0), "last 3m");
    assert_eq!(window_label(2.5), "last 2.5m");
}

#[test]
fn a_mountpoint_is_shortened_from_the_left() {
    assert_eq!(short_mount("/", 12), "/");
    assert_eq!(short_mount("/Volumes/Untitled", 12), "…Untitled");
    assert_eq!(short_mount("/Volumes/USB-1 128GB", 12), "…USB-1 128GB");
    assert_eq!(short_mount("/a/very-long-component-name", 12).chars().count(), 12);
}

#[test]
fn cores_go_into_columns_only_when_they_must() {
    assert_eq!(core_columns(10, 12, 80), 1);
    assert_eq!(core_columns(10, 8, 80), 2);
    assert_eq!(core_columns(32, 8, 120), 4);
    assert_eq!(core_columns(128, 8, 120), 0, "too many to list: a heat strip");
    assert_eq!(core_columns(10, 5, 36), 0, "two columns of 17 cells are not meters: a heat strip");
    assert_eq!(core_columns(10, 5, 40), 2);
}

// --- meters ---------------------------------------------------------------------------------

#[test]
fn a_bar_is_exactly_as_wide_as_asked_and_accurate_to_an_eighth() {
    let s = machine(4, 1, false);
    let mut scr = Screen::new(40, 1);
    s.bar(&mut scr, 0, 0, 20, 50.0, 0.0, None);
    let line = row(&scr, 0);
    assert_eq!(line.chars().filter(|&c| c == BAR_FULL).count(), 10);
    assert_eq!(line.chars().filter(|&c| c == BAR_EMPTY).count(), 10);
    assert_eq!(line.trim_end().chars().count(), 20);
    let mut scr = Screen::new(40, 1);
    s.bar(&mut scr, 0, 0, 20, 52.5, 0.0, None);            // 10.5 cells: ten full, one half
    assert_eq!(row(&scr, 0).chars().nth(10), Some('▌'));
}

#[test]
fn the_peak_is_marked_only_beyond_the_fill() {
    let s = machine(4, 1, false);
    let mut scr = Screen::new(40, 1);
    s.bar(&mut scr, 0, 0, 20, 30.0, 0.0, Some(80.0));
    assert_eq!(row(&scr, 0).chars().nth(16), Some(PEAK_MARK));
    let mut scr = Screen::new(40, 1);
    s.bar(&mut scr, 0, 0, 20, 30.0, 0.0, Some(30.0));
    assert!(!row(&scr, 0).contains(PEAK_MARK), "a peak equal to the value has nothing to mark");
}

#[test]
fn a_bar_survives_junk() {
    let s = machine(4, 1, false);
    for (pct, secondary, peak) in [(f64::NAN, 0.0, None), (150.0, 80.0, Some(f64::NAN)), (-5.0, -5.0, Some(500.0)), (100.0, 100.0, Some(100.0))] {
        for width in [0usize, 1, 2, 3, 20] {
            let mut scr = Screen::new(30, 1);
            s.bar(&mut scr, 0, 0, width, pct, secondary, peak);
            assert_eq!(row(&scr, 0).trim_end().chars().count(), width);
        }
    }
}

#[test]
fn a_meter_drops_its_note_rather_than_slicing_it() {
    let s = machine(4, 1, false);
    let mut scr = Screen::new(80, 1);
    s.meter(&mut scr, 0, 0, 60, Meter { note: "6.0G/16.0G", note_w: Some(13), ..Meter::new("ram", 40.0, 40.0, 40.0) });
    assert!(row(&scr, 0).contains("6.0G/16.0G"));
    let mut scr = Screen::new(80, 1);
    s.meter(&mut scr, 0, 0, 30, Meter { note: "6.0G/16.0G", note_w: Some(13), ..Meter::new("ram", 40.0, 40.0, 40.0) });
    let line = row(&scr, 0);
    assert!(!line.contains("6.0G") && line.contains("40.0%"));
    assert!(line.trim_end().chars().count() <= 30);
}

#[test]
fn the_number_is_raw_while_the_fill_is_eased() {
    let mut live = machine(4, 1, true);
    assert_eq!(live.shown(7, 0.0), 0.0);
    assert_eq!(live.shown(7, 100.0), 50.0, "live: half way toward the new value");
    let mut snapshot = machine(4, 1, false);
    snapshot.shown(7, 0.0);
    assert_eq!(snapshot.shown(7, 100.0), 100.0, "a report never carries an interpolated value");
}

#[test]
fn the_peak_is_the_high_water_mark_of_thirty_samples() {
    let mut s = machine(4, 1, true);
    assert_eq!(s.peak(9, 80.0), 80.0);
    for _ in 0..PEAK_WINDOW - 1 {
        assert_eq!(s.peak(9, 10.0), 80.0);
    }
    assert_eq!(s.peak(9, 10.0), 10.0, "the old maximum has left the window");
}

#[test]
fn what_was_not_drawn_in_a_frame_is_forgotten() {
    let mut s = machine(4, 1, true);
    s.shown(1, 5.0);
    s.peak(1, 5.0);
    s.end_frame();
    assert_eq!((s.eased.len(), s.peaks.len()), (1, 1));
    s.end_frame();
    assert_eq!((s.eased.len(), s.peaks.len()), (0, 0), "a process list cannot grow the table for ever");
}

// --- layout ---------------------------------------------------------------------------------

#[test]
fn the_dashboard_holds_at_every_geometry() {
    // Nothing may panic, the header and the CPU total are always there, and no line of the
    // picture is ever wider than the terminal (the grid makes the last one true by force).
    for frame in 0..3 {
        for &ncores in &[1usize, 4, 10, 16, 64, 128] {
            for ndisks in [0usize, 1, 4, 9] {
                let mut s = machine(ncores, ndisks, true);
                s.opts.compact = frame == 1;
                s.opts.no_border = frame == 2;
                for w in (40..=230).step_by(7) {
                    for h in 8..=64 {
                        let scr = drawn(&mut s, w, h);
                        let all = text(&scr);
                        assert!(row(&scr, 0).contains("TERMSTATS"), "{w}x{h}");
                        assert!(all.contains("TOTAL"), "{w}x{h} cores={ncores} disks={ndisks} frame={frame}\n{all}");
                    }
                }
            }
        }
    }
}

#[test]
fn a_terminal_too_small_says_so_instead_of_drawing_nonsense() {
    let mut s = machine(4, 1, true);
    assert!(text(&drawn(&mut s, 30, 20)).contains("termstats needs 40x8"));
    assert!(text(&drawn(&mut s, 80, 5)).contains("termstats needs 40x8"));
}

#[test]
fn the_full_layout_shows_every_panel() {
    let mut s = machine(10, 2, true);
    let all = text(&drawn(&mut s, 140, 42));
    for panel in ["╭─ cpu ", "╭─ memory ", "╭─ network ", "╭─ disk ", "╭─ processes · by cpu ", "cpu · last 30s", "▇ rx", "━ tx"] {
        assert!(all.contains(panel), "missing {panel}\n{all}");
    }
    assert!(all.contains("© ") && all.contains("Martin Pfeffer | celox.io"));
    assert!(all.contains("Esc or Ctrl+C to exit"));
}

#[test]
fn the_picture_fills_the_terminal_to_the_last_row() {
    let mut s = machine(10, 2, true);
    for (w, h) in [(140, 42), (100, 30), (200, 60), (80, 24), (120, 36)] {
        let scr = drawn(&mut s, w, h);
        for y in 0..h {
            assert!(!row(&scr, y).trim().is_empty(), "row {y} of {w}x{h} is blank\n{}", text(&scr));
        }
    }
}

#[test]
fn a_snapshot_has_no_exit_hint() {
    let mut s = machine(10, 2, false);
    assert!(!text(&drawn(&mut s, 140, 42)).contains("to exit"));
}

#[test]
fn as_many_processes_as_the_panel_has_rows() {
    let mut s = machine(10, 2, true);
    let scr = drawn(&mut s, 140, 42);
    let all = text(&scr);
    let top = all.lines().position(|l| l.contains("processes · by cpu")).unwrap();
    let bottom = all.lines().collect::<Vec<_>>().iter().rposition(|l| l.starts_with('╰')).unwrap();
    let shown = all.lines().filter(|l| l.contains("process number")).count();
    assert_eq!(shown, bottom - top - 2, "frame, header row, then one process per row");
    assert!(all.contains("process number 100"), "the busiest process leads");
}

#[test]
fn a_long_process_name_is_ellipsised_not_wrapped() {
    let mut s = machine(10, 2, true);
    s.names.insert(100, "x".repeat(400));
    let all = text(&drawn(&mut s, 100, 40));
    let line = all.lines().find(|l| l.contains("xxxx")).unwrap();
    assert!(line.contains('…') && line.chars().count() == 100);
}

#[test]
fn a_wide_character_in_a_name_cannot_shift_the_columns() {
    assert_eq!(cell_safe("日本語 app"), "??? app");
    assert_eq!(cell_safe("Café"), "Café");
}

#[test]
fn no_border_keeps_a_gutter_and_a_rule() {
    let mut s = machine(10, 2, true);
    s.opts.no_border = true;
    let all = text(&drawn(&mut s, 140, 42));
    assert!(!all.contains('╭') && !all.contains('│'));
    assert!(all.contains("cpu ──") && all.contains("memory ──"));
}

// --- charts ---------------------------------------------------------------------------------

fn chart_rows(series: &[(Vec<f64>, u32, bool)], ylim: Option<(f64, f64)>, axis_w: usize, w: usize, h: usize) -> Vec<String> {
    let s = machine(4, 1, true);
    let mut scr = Screen::new(w, h);
    s.chart(&mut scr, Rect { x: 0, y: 0, w, h }, series, ylim, axis_w);
    (0..h).map(|y| row(&scr, y)).collect()
}

#[test]
fn a_filled_series_is_an_area_from_the_floor() {
    let rows = chart_rows(&[(vec![50.0; 60], 0xff0000, true)], Some((0.0, 100.0)), AXIS_W_PCT, 40, 10);
    let plot = |y: usize| rows[y].chars().skip(AXIS_W_PCT).filter(|&c| c != ' ').count();
    assert_eq!(plot(8), 37, "the floor row is covered edge to edge");
    assert_eq!(plot(7), 37);
    assert_eq!(plot(0), 0, "a 50 % curve leaves the top of the plot empty");
}

#[test]
fn a_line_series_is_one_cell_per_column_and_its_edges_are_joined() {
    let rows = chart_rows(&[(vec![50.0; 60], 0xff0000, false)], Some((0.0, 100.0)), AXIS_W_PCT, 40, 10);
    let cells: usize = rows[..9].iter().map(|r| r.chars().skip(AXIS_W_PCT).filter(|&c| c != ' ').count()).sum();
    assert_eq!(cells, 37);
    let mut step = vec![0.0; 10];
    step.extend(vec![100.0; 10]);
    let rows = chart_rows(&[(step, 0xff0000, false)], Some((0.0, 100.0)), AXIS_W_PCT, 40, 10);
    for (y, r) in rows[..9].iter().enumerate() {
        assert!(!r.chars().skip(AXIS_W_PCT).all(|c| c == ' '), "row {y} is empty: the edge has a gap");
    }
}

#[test]
fn the_axis_carries_top_middle_and_floor_and_the_time_runs_to_now() {
    let rows = chart_rows(&[(vec![0.0, 560.0], 0xff0000, true)], None, AXIS_W_RATE, 60, 8);
    assert_eq!(rows[0][..5].trim(), "600");
    assert_eq!(rows[3][..5].trim(), "300");
    assert_eq!(rows[6][..5].trim(), "0");
    let axis = &rows[7];
    assert!(axis.trim_end().ends_with("now") && axis.find("-30s") < axis.find("-15s"));
    let narrow = chart_rows(&[(vec![1.0; 60], 0xff0000, true)], Some((0.0, 100.0)), AXIS_W_PCT, 9, 6);
    assert!(narrow[5].contains("now") && !narrow[5].contains("-15s"), "a label with no room is dropped");
}

#[test]
fn a_chart_survives_junk() {
    for values in [vec![f64::NAN; 5], vec![f64::INFINITY, 1.0], vec![], vec![7.0]] {
        chart_rows(&[(values, 0xff0000, true)], None, AXIS_W_RATE, 30, 6);
    }
    chart_rows(&[(vec![1.0; 10], 0xff0000, true)], Some((5.0, 5.0)), 3, 3, 2);
}

#[test]
fn the_line_keeps_its_colour_inside_the_area() {
    let s = machine(4, 1, true);
    let mut scr = Screen::new(40, 10);
    let series = [(vec![80.0; 60], 0x00ff00, true), (vec![40.0; 60], 0x0000ff, false)];
    s.chart(&mut scr, Rect { x: 0, y: 0, w: 40, h: 10 }, &series, Some((0.0, 100.0)), AXIS_W_PCT);
    let blue: Vec<_> = scr.cells.iter().filter(|c| c.fg == 0x0000ff).collect();
    assert_eq!(blue.len(), 37, "one cell per column carries the line's colour");
    // ... and shows the line only: a full cell in the line's colour would be a solid block.
    assert!(blue.iter().all(|c| (c.ch as u32 - 0x2800).count_ones() == 2), "a flat line is two dots per cell");
}

#[test]
fn the_network_axis_changes_unit_with_hysteresis() {
    let mut s = machine(4, 1, true);
    s.sent_hist.clear();
    s.recv_hist.clear();
    assert_eq!(s.net_scale().1, "KB/s");
    push(&mut s.recv_hist, 1.5 * 1048576.0);
    assert_eq!(s.net_scale().1, "KB/s", "one and a half megabytes is not yet two");
    push(&mut s.recv_hist, 2.0 * 1048576.0);
    assert_eq!(s.net_scale().1, "MB/s", "up once the peak reaches two of the next unit");
    s.recv_hist.clear();
    push(&mut s.recv_hist, 1.5 * 1048576.0);
    assert_eq!(s.net_scale().1, "MB/s", "and not back down while it is above one");
    s.recv_hist.clear();
    push(&mut s.recv_hist, 0.5 * 1048576.0);
    assert_eq!(s.net_scale().1, "KB/s");
}

// --- processes ------------------------------------------------------------------------------

#[test]
fn equal_cpu_is_ordered_by_pid_so_the_list_does_not_dance() {
    let mut s = machine(4, 1, true);
    s.ranked = vec![ProcRow { pid: 9, cpu: 5.0, rss: 0 }, ProcRow { pid: 3, cpu: 5.0, rss: 0 }, ProcRow { pid: 5, cpu: 50.0, rss: 0 }];
    rank(&mut s.ranked);
    assert_eq!(s.ranked.iter().map(|r| r.pid).collect::<Vec<_>>(), vec![5, 3, 9]);
}

// --- the painter: only what changed reaches the terminal --------------------------------------

/// A terminal just large enough for what the painter sends: cursor position, SGR, text.
struct Vt {
    w: usize,
    cells: Vec<char>,
    at: usize,
}

impl Vt {
    fn feed(&mut self, bytes: &str) {
        let chars: Vec<char> = bytes.chars().collect();
        let mut i = 0;
        while i < chars.len() {
            if chars[i] == '\x1b' && chars.get(i + 1) == Some(&'[') {
                let mut j = i + 2;
                while j < chars.len() && !chars[j].is_ascii_alphabetic() {
                    j += 1;
                }
                let body: String = chars[i + 2..j].iter().collect();
                match chars[j] {
                    'H' => {
                        let (r, c) = body.split_once(';').unwrap();
                        self.at = (r.parse::<usize>().unwrap() - 1) * self.w + c.parse::<usize>().unwrap() - 1;
                    }
                    'J' => self.cells.iter_mut().for_each(|c| *c = ' '),
                    _ => {}
                }
                i = j + 1;
            } else {
                self.cells[self.at] = chars[i];
                self.at += 1;
                i += 1;
            }
        }
    }
}

#[test]
fn replaying_the_diffs_reproduces_every_frame() {
    let mut s = machine(10, 3, true);
    let mut painter = Painter::new(ColorLevel::True);
    let mut vt = Vt { w: 120, cells: vec![' '; 120 * 36], at: 0 };
    for frame in 0..25 {
        s.total_pct = (frame * 13 % 100) as f64;
        s.core_pct.iter_mut().enumerate().for_each(|(i, p)| *p = ((i + frame) * 29 % 101) as f64);
        push(&mut s.cpu_hist, s.total_pct);
        s.recv_s = 1000.0 * frame as f64;
        let scr = drawn(&mut s, 120, 36);
        vt.feed(&painter.diff(&scr).to_string());
        let shown: String = vt.cells.iter().collect();
        let wanted: String = scr.cells.iter().map(|c| c.ch).collect();
        assert_eq!(shown, wanted, "frame {frame}");
    }
}

#[test]
fn an_unchanged_frame_sends_nothing_but_the_brackets() {
    let mut scr = Screen::new(80, 24);
    scr.put(0, 0, 80, "steady", NONE, false);
    let mut painter = Painter::new(ColorLevel::True);
    assert!(painter.diff(&scr).len() > 20);
    assert_eq!(painter.diff(&scr), "\x1b[?2026h\x1b[?2026l");
}

#[test]
fn one_changed_cell_costs_a_few_bytes_not_a_screen() {
    let mut scr = Screen::new(140, 42);
    for y in 0..42 {
        scr.put(0, y, 140, &"#".repeat(140), 0x123456, false);
    }
    let mut painter = Painter::new(ColorLevel::True);
    let full = painter.diff(&scr).len();
    scr.set(70, 20, '@', 0x654321, true);
    let one = painter.diff(&scr).len();
    assert!(one < 60 && full > 5000, "full {full} bytes, one cell {one} bytes");
}

#[test]
fn a_resize_repaints_everything() {
    let mut painter = Painter::new(ColorLevel::True);
    let scr = Screen::new(80, 24);
    painter.diff(&scr);
    assert!(painter.diff(&Screen::new(90, 24)).contains("\x1b[2J"));
    painter.invalidate();
    assert!(painter.diff(&Screen::new(90, 24)).contains("\x1b[2J"));
}

#[test]
fn a_live_frame_at_rest_is_a_fraction_of_a_full_repaint() {
    // The claim the README makes: a dashboard at rest changes a few dozen cells a frame.
    let mut s = machine(10, 2, true);
    let mut painter = Painter::new(ColorLevel::True);
    let full = painter.diff(&drawn(&mut s, 140, 42)).len();
    for _ in 0..12 {
        painter.diff(&drawn(&mut s, 140, 42));           // let the eased fills settle
    }
    s.total_pct += 3.0;
    let next = painter.diff(&drawn(&mut s, 140, 42)).len();
    assert!(next * 8 < full, "full repaint {full} bytes, next frame {next} bytes");
}

#[test]
fn colour_depth_decides_the_escape_form() {
    let mut scr = Screen::new(4, 1);
    scr.set(0, 0, 'x', 0xff8800, true);
    assert!(Painter::new(ColorLevel::True).diff(&scr).contains("\x1b[0;1;38;2;255;136;0m"));
    let indexed = Painter::new(ColorLevel::Indexed).diff(&scr).to_string();
    assert!(indexed.contains(";38;5;") && !indexed.contains("38;2;"));
    let mono = Painter::new(ColorLevel::Mono).lines(&scr).to_string();
    assert_eq!(mono, "x\n", "nothing but the text");
}

#[test]
fn a_snapshot_line_carries_no_trailing_blanks() {
    let mut scr = Screen::new(40, 2);
    scr.put(0, 0, 40, "abc", NONE, false);
    assert!(scr.cells[5] == BLANK);
    assert_eq!(Painter::new(ColorLevel::Mono).lines(&scr), "abc\n\n");
}

// --- themes ---------------------------------------------------------------------------------

#[test]
fn the_generated_themes_are_the_six_the_python_program_has() {
    let names: Vec<&str> = THEMES.iter().map(|t| t.name).collect();
    assert_eq!(names, ["default", "mono", "nord", "gruvbox", "catppuccin-mocha", "viridis"]);
    assert!(THEMES.iter().any(|t| t.name == crate::themes::DEFAULT_THEME));
    // The ramp of the default theme starts and ends on its first and last stop.
    assert_eq!(THEMES[0].ramp[0], 0x5f7f80);
    assert_eq!(THEMES[0].ramp[255], 0xff7b78);
}

#[test]
fn mixing_toward_the_background_stays_between_the_two() {
    assert_eq!(mix(0x336699, 0x101010, 0.0), 0x336699);
    assert_eq!(mix(0x336699, 0x101010, 1.0), 0x101010);
    let half = mix(0xffffff, 0x000000, 0.5);
    assert!(half > 0x505050 && half < 0xb0b0b0);
}

#[test]
fn a_seven_digit_pid_does_not_push_the_row_out_of_line() {
    let mut s = machine(10, 2, true);
    s.ranked[1].pid = 2_526_985;
    s.names.insert(2_526_985, "python".to_string());
    let all = text(&drawn(&mut s, 120, 40));
    let lines: Vec<&str> = all.lines().filter(|l| l.contains("process number") || l.contains("python")).collect();
    let name_col = |l: &str| l.chars().collect::<Vec<_>>().iter().position(|c| c.is_alphabetic()).unwrap();
    assert!(lines.len() > 3 && lines.iter().all(|l| name_col(l) == name_col(lines[0])), "{all}");
    assert!(all.contains("2526985"));
    // ... and the pids end in one column, two cells before the names begin.
    let pid_end = |l: &str| l.chars().collect::<Vec<_>>().iter().rposition(|c| c.is_ascii_digit() && true).map(|_| {
        let chars: Vec<char> = l.chars().collect();
        (0..name_col(l)).rev().find(|&i| chars[i].is_ascii_digit()).unwrap()
    }).unwrap();
    assert!(lines.iter().all(|l| pid_end(l) + 3 == name_col(l)), "a pid touches its name\n{all}");
}

#[test]
fn the_switch_keys_are_offered_only_when_there_is_something_to_switch_to() {
    let mut s = machine(10, 2, true);
    assert!(!text(&drawn(&mut s, 140, 42)).contains("b BPM"));
    s.opts.switch_keys = true;
    let wide = text(&drawn(&mut s, 140, 42));
    assert!(wide.contains("Esc or Ctrl+C to exit  s stats  b BPM  d dB  x both"));
    let narrow = text(&drawn(&mut s, 70, 42));
    assert!(narrow.contains("Esc or Ctrl+C to exit") && !narrow.contains("b BPM"), "the exit hint stays, the rest goes");
}

#[test]
fn a_process_share_is_the_cpu_time_it_used_since_the_last_scan() {
    let mut s = machine(4, 1, true);
    s.proc_prev = vec![(10, 1_000_000_000), (20, 5_000_000_000)];
    s.proc_prev_at = 100.0;
    // What scan_processes() does with two sorted lists, without asking the machine.
    s.samples = vec![ProcSample { pid: 20, cpu_ns: 5_500_000_000, rss: 1 }, ProcSample { pid: 10, cpu_ns: 1_250_000_000, rss: 2 },
                     ProcSample { pid: 30, cpu_ns: 9_000_000_000, rss: 3 }, ProcSample { pid: 15, cpu_ns: 7_000_000_000, rss: 4 }];
    let shares = cpu_shares(&mut s.samples, &s.proc_prev, 1e9);
    // 15 is new and sits BETWEEN two known pids: it must not borrow its neighbour's past.
    assert_eq!(shares, vec![(10, 25.0), (15, 0.0), (20, 50.0), (30, 0.0)], "a process seen for the first time has no share yet");
}

#[test]
fn changes_close_together_travel_in_one_run() {
    // Moving the cursor costs about as many bytes as repeating a few unchanged cells.
    let mut scr = Screen::new(80, 4);
    scr.put(0, 1, 80, &"-".repeat(80), NONE, false);
    let mut painter = Painter::new(ColorLevel::True);
    painter.diff(&scr);
    scr.set(10, 1, 'a', NONE, false);
    scr.set(13, 1, 'b', NONE, false);
    let near = painter.diff(&scr).to_string();
    assert_eq!(near.matches('H').count(), 1, "{near:?}");
    assert!(near.contains("a--b"));
    scr.set(20, 1, 'c', NONE, false);
    scr.set(60, 1, 'd', NONE, false);
    assert_eq!(painter.diff(&scr).matches('H').count(), 2, "far apart: two runs, nothing in between");
}

#[cfg(target_os = "macos")]
#[test]
fn a_program_installed_under_its_version_number_is_named_after_its_directory() {
    use crate::sys::name_from_path;
    assert_eq!(name_from_path("/usr/bin/zsh").as_deref(), Some("zsh"));
    assert_eq!(name_from_path("/Users/a/.local/share/claude/versions/2.1.325").as_deref(), Some("claude"));
    assert_eq!(name_from_path("/Applications/Foo.app/Contents/MacOS/12").as_deref(), Some("Foo.app"));
    assert_eq!(name_from_path("/1/2/3").as_deref(), Some("3"), "nothing better to offer: the file name stays");
    assert_eq!(name_from_path(""), None);
}

// --- views and chrome: a dashboard of any subset ------------------------------------------------

/// The real machine, read twice, with these views.
fn sampled(views: u32, live: bool) -> State {
    let mut s = State::new(Options { views: Views(views), live, ..Options::terminal(0.5, &THEMES[0]) });
    s.sample(1.0);
    s.sample(1.5);
    s
}

/// Which of the six dashboard views have a panel on screen, in bit order.
fn panels_on(all: &str) -> [bool; 6] {
    ["╭─ cpu ─", "╭─ memory ─", "╭─ network ─", "╭─ disk ─", "· last", "╭─ processes"].map(|t| all.contains(t))
}

const VIEW_BITS: [u32; 6] = [Views::CPU, Views::MEMORY, Views::NETWORK, Views::DISK, Views::CHARTS, Views::PROCESSES];

#[test]
fn the_terminal_options_are_the_whole_dashboard_with_all_its_chrome() {
    let o = Options::terminal(0.5, &THEMES[0]);
    assert_eq!((o.views, o.chrome), (Views(Views::DASHBOARD), Chrome(Chrome::ALL)));
    assert_eq!(Views::DASHBOARD, VIEW_BITS.iter().sum::<u32>());
    assert_eq!(Views::AUDIO, Views::EQ | Views::BPM | Views::LEVEL);
    assert_eq!(Chrome::ALL, Chrome::HEADER | Chrome::CLOCK | Chrome::VERSION | Chrome::FOOTER | Chrome::HINT | Chrome::BRAND);
}

#[test]
fn default_options_draw_what_they_drew_before() {
    // The panels the terminal program showed before the views existed (a --size snapshot).
    let mut s = sampled(Views::DASHBOARD, false);
    for (w, h, wanted) in [(140, 42, [true; 6]), (80, 24, [true, true, true, false, false, false]),
                           (60, 20, [true, true, true, false, false, false])] {
        let all = text(&drawn(&mut s, w, h));
        assert_eq!(panels_on(&all), wanted, "{w}x{h}\n{all}");
        assert!(row(&drawn(&mut s, w, h), 0).contains("TERMSTATS") && all.contains("celox.io"));
    }
}

#[test]
fn every_view_subset_draws_inside_the_screen() {
    for views in 0..=Views::DASHBOARD {
        let mut s = sampled(views, true);
        for (w, h) in [(60, 20), (80, 24), (100, 30), (140, 42), (200, 60)] {
            let all = text(&drawn(&mut s, w, h));
            let on = panels_on(&all);
            for (i, &bit) in VIEW_BITS.iter().enumerate() {
                assert!(!on[i] || views & bit != 0, "views {views:#b} {w}x{h}: panel {i} drawn while off\n{all}");
                assert!(on[i] || views & bit == 0 || dropped_for_room(bit, w),
                        "views {views:#b} {w}x{h}: panel {i} missing while on\n{all}");
            }
        }
    }
}

/// The one way a view that is on may still be missing: on a terminal narrower than
/// NARROW_BELOW everything stacks, and the disk, the charts and the processes go when the
/// rows run out (as they did before the views). From 100x30 on, every view that is on shows.
fn dropped_for_room(bit: u32, w: usize) -> bool {
    w < NARROW_BELOW && bit & (Views::DISK | Views::CHARTS | Views::PROCESSES) != 0
}

#[test]
fn no_views_leaves_only_header_and_footer() {
    let mut s = sampled(0, true);
    let scr = drawn(&mut s, 100, 30);
    assert!(row(&scr, 0).contains("TERMSTATS") && row(&scr, 29).contains("celox.io"));
    for y in 1..29 {
        assert!(row(&scr, y).trim().is_empty(), "row {y}\n{}", text(&scr));
    }
}

fn has_clock(line: &str) -> bool {
    let c: Vec<char> = line.chars().collect();
    c.windows(8).any(|w| w.iter().enumerate().all(|(i, ch)| if i == 2 || i == 5 { *ch == ':' } else { ch.is_ascii_digit() }))
}

#[test]
fn chrome_bits_hide_their_parts() {
    let with = |chrome: u32| {
        let mut s = machine(10, 2, true);
        s.opts.chrome = Chrome(chrome);
        drawn(&mut s, 140, 42)
    };
    let all = with(Chrome::ALL);
    assert!(has_clock(&row(&all, 0)) && row(&all, 0).contains("v0.") && text(&all).contains("Ctrl+C") && text(&all).contains("celox.io"),
            "the controls: with everything on, every part is there");

    let no_header = with(Chrome::ALL & !Chrome::HEADER);
    assert!(!text(&no_header).contains("TERMSTATS"));
    assert!(row(&no_header, 0).starts_with("╭─ cpu "), "the body starts at row 0\n{}", text(&no_header));
    assert!(row(&no_header, 40).starts_with('╰'), "... and gains the header's row at the bottom\n{}", text(&no_header));
    let mut empty = machine(10, 2, true);
    empty.opts.views = Views(0);
    empty.opts.chrome = Chrome(Chrome::ALL & !Chrome::HEADER);
    let bare = drawn(&mut empty, 140, 42);
    assert!(row(&bare, 0).trim().is_empty(), "with nothing to cover it, row 0 shows the header is gone\n{}", text(&bare));

    let no_footer = with(Chrome::ALL & !Chrome::FOOTER);
    assert!(!text(&no_footer).contains("celox.io") && !text(&no_footer).contains("Ctrl+C"));
    assert!(row(&no_footer, 41).starts_with('╰'), "the last row belongs to the body\n{}", text(&no_footer));

    let no_hint = text(&with(Chrome::ALL & !Chrome::HINT));
    assert!(!no_hint.contains("Ctrl+C") && no_hint.contains("celox.io"));
    let no_brand = text(&with(Chrome::ALL & !Chrome::BRAND));
    assert!(!no_brand.contains("celox.io") && no_brand.contains("Ctrl+C"));
    let no_clock = with(Chrome::ALL & !Chrome::CLOCK);
    assert!(!has_clock(&row(&no_clock, 0)) && row(&no_clock, 0).contains("v0."));
    let no_version = with(Chrome::ALL & !Chrome::VERSION);
    assert!(!row(&no_version, 0).contains("v0.") && has_clock(&row(&no_version, 0)));
}

#[test]
fn views_off_skip_their_scans() {
    let all = sampled(Views::DASHBOARD, true);
    assert!(all.nproc > 0 && !all.disks.is_empty(), "the control: with every view on, both are read");
    assert_eq!(sampled(Views::DASHBOARD & !Views::PROCESSES, true).nproc, 0);
    assert!(sampled(Views::DASHBOARD & !Views::DISK, true).disks.is_empty());
}

#[test]
fn without_cpu_the_stack_is_one_row_and_without_charts_or_processes_the_rest_stays_blank() {
    let mut s = machine(10, 2, true);
    s.opts.views = Views(Views::MEMORY | Views::NETWORK | Views::DISK);
    let scr = drawn(&mut s, 140, 42);
    let top = row(&scr, 1);
    assert!(top.starts_with("╭─ memory ─") && top.contains("╭─ network ─") && top.contains("╭─ disk ─"), "{}", text(&scr));
    for y in 8..41 {
        assert!(row(&scr, y).trim().is_empty(), "row {y}\n{}", text(&scr));
    }
}
