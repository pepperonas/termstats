//! What the machine reports, read with the cheapest call each platform has.
//!
//! No crate sits between these functions and the kernel: on macOS they are mach, sysctl
//! and libproc calls, on Linux reads of /proc. A whole process table costs about half a
//! millisecond here (one `proc_pidinfo` per process); the same scan through psutil costs
//! nine.

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::*;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::*;

/// Busy and total ticks of one CPU (or of all of them) since boot.
#[derive(Clone, Copy, Default)]
pub struct CpuTimes {
    pub busy: u64,
    pub total: u64,
}

#[derive(Clone, Copy, Default)]
pub struct Mem {
    pub total: u64,
    pub used: u64,
    pub available: u64,
    pub swap_total: u64,
    pub swap_used: u64,
}

pub struct Disk {
    pub mount: String,
    pub total: u64,
    pub used: u64,
    pub percent: f64,
}

#[derive(Clone, Copy, Default)]
pub struct Net {
    pub sent: u64,
    pub recv: u64,
}

#[derive(Clone, Copy)]
pub struct ProcSample {
    pub pid: i32,
    /// CPU time consumed since the process started, in nanoseconds.
    pub cpu_ns: u64,
    pub rss: u64,
}

pub fn loadavg() -> [f64; 3] {
    let mut l = [0f64; 3];
    unsafe { libc::getloadavg(l.as_mut_ptr(), 3) };
    l
}

pub fn hostname() -> String {
    let mut buf = [0u8; 256];
    unsafe { libc::gethostname(buf.as_mut_ptr() as *mut libc::c_char, buf.len() - 1) };
    let end = buf.iter().position(|&b| b == 0).unwrap_or(0);
    String::from_utf8_lossy(&buf[..end]).into_owned()
}

/// Seconds since the epoch, as a float.
pub fn now() -> f64 {
    let mut tv = libc::timeval { tv_sec: 0, tv_usec: 0 };
    unsafe { libc::gettimeofday(&mut tv, std::ptr::null_mut()) };
    tv.tv_sec as f64 + tv.tv_usec as f64 / 1e6
}

/// Filesystem types that are never a disk worth a meter.
pub const SKIP_FS: &[&str] = &["tmpfs", "devtmpfs", "squashfs", "overlay", "devfs", "autofs"];

pub fn usage_percent(used: u64, avail_to_user: u64) -> f64 {
    let denom = used + avail_to_user;
    if denom == 0 { 0.0 } else { used as f64 * 100.0 / denom as f64 }
}
