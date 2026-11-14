//! termstats-native: the termstats system dashboard as a native program.
//!
//! Same picture, same layout budget, same number formats as `termstats` (Python) - at a
//! small fraction of its CPU and memory. Three things make the difference:
//!
//!  * the machine is read with direct system calls (`sys`), not through a library;
//!  * a frame is drawn into a cell grid and only the cells that CHANGED are sent to the
//!    terminal, in one write (`screen`);
//!  * between two frames the process sleeps in one `poll()` - a key or a resize wakes it,
//!    nothing else does.
//!
//! The microphone modes, `--demo` and Windows stay with the Python program.

mod color;
mod screen;
mod sys;
mod themes;
mod ui;

use screen::{ColorLevel, Painter, Screen};
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use ui::{Options, State};

static QUIT: AtomicBool = AtomicBool::new(false);
static RESIZED: AtomicBool = AtomicBool::new(false);
/// The key of the microphone screen that was asked for (b, d or x), 0 while none was.
static SWITCH: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);
static SWITCH_KEYS: AtomicBool = AtomicBool::new(false);

extern "C" fn on_quit(_: libc::c_int) {
    QUIT.store(true, Ordering::SeqCst);
}

extern "C" fn on_resize(_: libc::c_int) {
    RESIZED.store(true, Ordering::SeqCst);
}

/// Install a handler WITHOUT SA_RESTART: the signal must interrupt poll(), that is the point.
fn handle(signal: libc::c_int, handler: extern "C" fn(libc::c_int)) {
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = handler as usize;
        libc::sigemptyset(&mut action.sa_mask);
        libc::sigaction(signal, &action, std::ptr::null_mut());
    }
}

fn monotonic() -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

fn terminal_size() -> Option<(usize, usize)> {
    unsafe {
        let mut ws: libc::winsize = std::mem::zeroed();
        if libc::ioctl(libc::STDOUT_FILENO, libc::TIOCGWINSZ, &mut ws) == 0 && ws.ws_col > 0 && ws.ws_row > 0 {
            return Some((ws.ws_col as usize, ws.ws_row as usize));
        }
    }
    None
}

fn env_size() -> Option<(usize, usize)> {
    let get = |name: &str| std::env::var(name).ok().and_then(|v| v.parse::<usize>().ok()).filter(|&v| v > 0);
    Some((get("COLUMNS")?, get("LINES")?))
}

fn write_all(bytes: &[u8]) {
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(bytes);
    let _ = out.flush();
}

/// The terminal attributes to put back - also read by the panic hook, because a release
/// build aborts on a panic and no destructor would run.
static SAVED_TTY: std::sync::OnceLock<libc::termios> = std::sync::OnceLock::new();
const LEAVE_SCREEN: &[u8] = b"\x1b[0m\x1b[?2026l\x1b[?25h\x1b[?1049l";

fn restore_terminal() {
    write_all(LEAVE_SCREEN);
    if let Some(attrs) = SAVED_TTY.get() {
        unsafe { libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, attrs) };
    }
}

/// Cbreak for the session - keys arrive unbuffered and unechoed - restored on drop.
struct Tty {
    saved: Option<libc::termios>,
}

impl Tty {
    fn enter() -> Tty {
        let mut saved = None;
        unsafe {
            let mut attrs: libc::termios = std::mem::zeroed();
            if libc::isatty(libc::STDIN_FILENO) == 1 && libc::tcgetattr(libc::STDIN_FILENO, &mut attrs) == 0 {
                saved = Some(attrs);
                attrs.c_lflag &= !(libc::ICANON | libc::ECHO);
                attrs.c_cc[libc::VMIN] = 0;
                attrs.c_cc[libc::VTIME] = 0;
                libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, &attrs);
            }
        }
        if let Some(attrs) = saved {
            let _ = SAVED_TTY.set(attrs);
        }
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            restore_terminal();
            default_hook(info);
        }));
        // Alternate screen, cursor hidden.
        write_all(b"\x1b[?1049h\x1b[?25l");
        Tty { saved }
    }
}

impl Drop for Tty {
    fn drop(&mut self) {
        restore_terminal();
    }
}

/// Sleep until `deadline` in ONE poll(). Returns early on a key or a signal.
fn wait_until(deadline: f64, watch_keys: bool) {
    loop {
        let left = deadline - monotonic();
        if left <= 0.0 || QUIT.load(Ordering::SeqCst) || RESIZED.load(Ordering::SeqCst) {
            return;
        }
        let mut fd = libc::pollfd { fd: libc::STDIN_FILENO, events: libc::POLLIN, revents: 0 };
        let ready = unsafe { libc::poll(&mut fd, if watch_keys { 1 } else { 0 }, (left * 1000.0).ceil() as libc::c_int) };
        if ready > 0 {
            let mut buf = [0u8; 64];
            let n = unsafe { libc::read(libc::STDIN_FILENO, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
            if n <= 0 {
                // stdin is gone (closed pipe): stop watching it, keep the dashboard.
                wait_until(deadline, false);
                return;
            }
            // Esc is also the first byte of every arrow and function key; the whole burst
            // arrives in one read, which is the only reason they can be told apart.
            let keys = &buf[..n as usize];
            if keys == b"\x1b" || keys.iter().all(|&b| b == b'q' || b == b'Q') || keys.contains(&3) {
                QUIT.store(true, Ordering::SeqCst);
                return;
            }
            if SWITCH_KEYS.load(Ordering::SeqCst) && keys.len() == 1 && b"bdx".contains(&keys[0].to_ascii_lowercase()) {
                SWITCH.store(keys[0].to_ascii_lowercase(), Ordering::SeqCst);
                QUIT.store(true, Ordering::SeqCst);
                return;
            }
        }
        // ready < 0: a signal interrupted the wait - the flags decide at the top of the loop.
    }
}

const HELP: &str = "\
termstats-native - the termstats system dashboard, native

Usage: termstats-native [options]

  -l, --live            live dashboard (the default in a terminal)
  -1, --once            one snapshot, then exit (the default when piped)
  -i, --interval SEC    seconds between frames (default 0.5)
  -t, --theme NAME      default, mono, nord, gruvbox, catppuccin-mocha, viridis
      --list-themes     print the theme names
      --compact         no padding inside the panels
      --no-border       a title rule instead of a frame
      --size COLSxROWS  snapshot size when there is no terminal to ask (e.g. 140x42)
      --samples N       snapshot after N samples, -i apart (default 1): fills the charts
  -V, --version         print the version
  -h, --help            this text

Environment: TERMSTATS_THEME, NO_COLOR, COLORTERM, TERM, COLUMNS, LINES.
Keys: q, Esc or Ctrl+C to leave.

The microphone modes (-eq, -bpm, -db, -x), --demo and Windows are served by the Python
program `termstats`; this is its system dashboard at a fraction of the CPU.
";

fn fail(message: &str) -> ! {
    eprintln!("termstats-native: {message}");
    eprintln!("Try 'termstats-native --help'.");
    std::process::exit(2);
}

fn find_theme(name: &str) -> &'static themes::Theme {
    match themes::THEMES.iter().find(|t| t.name == name) {
        Some(theme) => theme,
        None => fail(&format!("unknown theme '{name}' (try --list-themes)")),
    }
}

fn color_level() -> ColorLevel {
    let var = |name: &str| std::env::var(name).unwrap_or_default();
    let term = var("TERM");
    if std::env::var_os("NO_COLOR").is_some_and(|v| !v.is_empty()) || term == "dumb" {
        return ColorLevel::Mono;
    }
    let colorterm = var("COLORTERM");
    if colorterm.contains("truecolor") || colorterm.contains("24bit") || term.contains("direct") {
        return ColorLevel::True;
    }
    if term.contains("256") { ColorLevel::Indexed } else { ColorLevel::True }
}

fn cpu_time() -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

/// What each part of a frame costs, in microseconds of CPU, at 140x42.
fn bench() {
    const N: usize = 200;
    let time = |name: &str, f: &mut dyn FnMut()| {
        f();
        let start = cpu_time();
        for _ in 0..N {
            f();
        }
        println!("{:<34}{:>9.1} us", name, (cpu_time() - start) * 1e6 / N as f64);
    };
    let theme = find_theme(themes::DEFAULT_THEME);
    let (mut cores, mut pids, mut procs) = (Vec::new(), Vec::new(), Vec::new());
    time("cpu times, per core", &mut || { sys::cpu_times(&mut cores); });
    time("memory + swap", &mut || { sys::memory(); });
    let mut net_buf = Vec::new();
    time("network counters", &mut || { sys::net(&mut net_buf); });
    time("partitions + usage", &mut || { sys::disks(); });
    time("disk throughput", &mut || { sys::disk_io(); });
    time("process table (scan)", &mut || { sys::proc_scan(&mut pids, &mut procs); });
    println!("{:<34}{:>9} processes, {} readable", "", pids.len(), procs.len());

    let mut state = State::new(Options { interval: 0.5, theme, compact: false, no_border: false, live: true, switch_keys: false });
    let mut clock = 0.0;
    for _ in 0..70 {
        clock += 0.5;
        state.sample(clock);
    }
    let mut scr = Screen::new(140, 42);
    let mut painter = Painter::new(ColorLevel::True);
    time("sample (one frame, all of it)", &mut || { clock += 0.5; state.sample(clock); });
    time("draw 140x42", &mut || { scr.reset(140, 42); state.draw(&mut scr); });
    let mut bytes = 0usize;
    time("diff + encode", &mut || {
        clock += 0.5;
        state.sample(clock);
        scr.reset(140, 42);
        state.draw(&mut scr);
        bytes = painter.diff(&scr).len();
    });
    println!("{:<34}{:>9} bytes in the last frame", "", bytes);
}

fn main() {
    let mut interval = 0.5f64;
    let mut theme_name = std::env::var("TERMSTATS_THEME").ok().filter(|v| !v.is_empty());
    let (mut compact, mut no_border, mut once, mut live) = (false, false, false, false);
    let mut size: Option<(usize, usize)> = None;
    let mut samples: Option<usize> = None;
    let mut python: Option<String> = None;
    let mut python_args: Vec<String> = Vec::new();
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        // Long options are accepted with one dash as well, like the Python program does.
        match arg.as_str() {
            "-h" | "--help" | "-help" => { print!("{HELP}"); return }
            "-V" | "--version" | "-version" => { println!("termstats-native {}", env!("CARGO_PKG_VERSION")); return }
            "--list-themes" | "-list-themes" => {
                for theme in themes::THEMES {
                    println!("{}{}", theme.name, if theme.name == themes::DEFAULT_THEME { "  (default)" } else { "" });
                }
                return;
            }
            "-l" | "--live" | "-live" => live = true,
            "-1" | "--once" | "-once" => once = true,
            "--compact" | "-compact" => compact = true,
            "--no-border" | "-no-border" => no_border = true,
            "-i" | "--interval" | "-interval" => {
                let value = args.next().unwrap_or_else(|| fail("--interval needs a number of seconds"));
                interval = match value.parse::<f64>() {
                    Ok(v) if v.is_finite() && v > 0.0 => v,
                    _ => fail(&format!("--interval needs a positive number, not '{value}'")),
                };
            }
            "-t" | "--theme" | "-theme" => {
                theme_name = Some(args.next().unwrap_or_else(|| fail("--theme needs a name")));
            }
            "--size" | "-size" => {
                let value = args.next().unwrap_or_else(|| fail("--size needs COLSxROWS"));
                size = value.split_once(['x', 'X']).and_then(|(c, r)| Some((c.parse::<usize>().ok()?, r.parse::<usize>().ok()?)))
                    .filter(|&(c, r)| (40..=1000).contains(&c) && (8..=500).contains(&r));
                if size.is_none() {
                    fail(&format!("--size needs COLSxROWS from 40x8 to 1000x500, not '{value}'"));
                }
            }
            "--samples" | "-samples" => {
                let value = args.next().unwrap_or_else(|| fail("--samples needs a number"));
                samples = match value.parse::<usize>() {
                    Ok(n) if (1..=3600).contains(&n) => Some(n),
                    _ => fail(&format!("--samples needs a number from 1 to 3600, not '{value}'")),
                };
            }
            // Set by the Python program when it hands the dashboard over: where to find it
            // again for the microphone screens, and (after "--") what it was started with.
            "--python" => python = Some(args.next().unwrap_or_else(|| fail("--python needs a path"))),
            "--" => { python_args = args.by_ref().collect(); break }
            // Not in --help: prints what each part of a frame costs on this machine.
            "--bench" => { bench(); return }
            other => fail(&format!("unknown option '{other}'")),
        }
    }
    if once && live {
        fail("--once and --live exclude each other");
    }
    let is_tty = unsafe { libc::isatty(libc::STDOUT_FILENO) } == 1;
    // Live needs a terminal; piped, redirected or under cron it would never return.
    let live = !once && (live || is_tty) && is_tty;
    let theme = find_theme(theme_name.as_deref().unwrap_or(themes::DEFAULT_THEME));
    let switch_keys = live && python.is_some();
    SWITCH_KEYS.store(switch_keys, Ordering::SeqCst);
    let mut state = State::new(Options { interval, theme, compact, no_border, live, switch_keys });
    let mut painter = Painter::new(color_level());

    if !live {
        // A snapshot samples for one second: rates need a gap between two reads, and
        // --interval is the live refresh rate, not a sampling window. With --samples the
        // caller asks for a history instead, and the interval is the gap it asked for.
        let gap = if samples.is_some() { interval } else { 1.0 };
        state.opts.interval = gap;
        let (w, h) = size.or_else(terminal_size).or_else(env_size).unwrap_or((100, 30));
        handle(libc::SIGINT, on_quit);
        state.sample(monotonic());
        for _ in 0..samples.unwrap_or(1) {
            wait_until(monotonic() + gap, false);
            if QUIT.load(Ordering::SeqCst) {
                std::process::exit(130);
            }
            state.sample(monotonic());
        }
        let mut scr = Screen::new(w, h);
        state.draw(&mut scr);
        write_all(painter.lines(&scr).as_bytes());
        return;
    }

    handle(libc::SIGINT, on_quit);
    handle(libc::SIGTERM, on_quit);
    handle(libc::SIGHUP, on_quit);
    handle(libc::SIGWINCH, on_resize);
    let tty = Tty::enter();
    let watch_keys = tty.saved.is_some();
    let (mut w, mut h) = terminal_size().unwrap_or((100, 30));
    let mut scr = Screen::new(w, h);
    state.sample(monotonic());                       // baselines; the first frame is the second read
    let mut next_tick = monotonic() + interval.min(0.5);
    loop {
        wait_until(next_tick, watch_keys);
        if QUIT.load(Ordering::SeqCst) {
            break;
        }
        let now = monotonic();
        if RESIZED.swap(false, Ordering::SeqCst) {
            if let Some(size) = terminal_size() {
                (w, h) = size;
            }
            painter.invalidate();
            if now < next_tick {
                // Relayout at once with the numbers already measured; the cadence stays.
                scr.reset(w, h);
                state.draw(&mut scr);
                write_all(painter.diff(&scr).as_bytes());
                continue;
            }
        }
        state.sample(now);
        scr.reset(w, h);
        state.draw(&mut scr);
        write_all(painter.diff(&scr).as_bytes());
        // A fixed grid: the cadence does not drift by the cost of a frame, and after a stall
        // (a suspended laptop) it resynchronises instead of firing a burst of frames.
        next_tick += interval;
        let after = monotonic();
        if next_tick < after {
            next_tick = after + interval;
        }
    }
    drop(tty);
    // b, d or x: the microphone screens live in the Python program - become it.
    let flag = match SWITCH.load(Ordering::SeqCst) {
        b'b' => "-bpm",
        b'd' => "-db",
        b'x' => "-x",
        _ => return,
    };
    if let Some(program) = python {
        use std::os::unix::process::CommandExt;
        let mut command = std::process::Command::new(&program);
        command.arg(flag).arg("--theme").arg(theme.name);
        if compact {
            command.arg("--compact");
        }
        if no_border {
            command.arg("--no-border");
        }
        let error = command.args(&python_args).exec();
        eprintln!("termstats-native: cannot start {program}: {error}");
        std::process::exit(1);
    }
}
